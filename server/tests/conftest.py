"""Testumgebung: echte MySQL/MariaDB-Datenbank (keine SQLite-Attrappe).

Benötigte Umgebungsvariablen (siehe tests/README oder CI):
  TEST_DB_HOST, TEST_DB_PORT, TEST_DB_USER, TEST_DB_PASSWORD
Für jeden Testlauf wird eine eigene Datenbank lbc_test_<zufall> angelegt, per Alembic migriert und
am Ende gelöscht.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import pymysql
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB_NAME = f"lbc_test_{secrets.token_hex(4)}"
os.environ.update({
    "DB_HOST": os.environ.get("TEST_DB_HOST", "127.0.0.1"),
    "DB_PORT": os.environ.get("TEST_DB_PORT", "3306"),
    "DB_USER": os.environ.get("TEST_DB_USER", "lbc"),
    "DB_PASSWORD": os.environ.get("TEST_DB_PASSWORD", "lbcpass"),
    "DB_NAME": DB_NAME,
    "DB_SSL_MODE": os.environ.get("TEST_DB_SSL_MODE", "preferred"),
    "APP_SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnop",
    "COOKIE_SECURE": "false",
    "RUN_SCHEDULER": "false",
    "OWNER_DISCORD_ID": "100000000000000001",
    "DISCORD_CLIENT_ID": "200000000000000001",
    "DISCORD_CLIENT_SECRET": "client-secret-for-tests",
    "DISCORD_REDIRECT_URI": "https://panel.test/api/auth/callback",
    "DISCORD_GUILD_ID": "300000000000000001",
    "LOG_LEVEL": "WARNING",
})

from lbc.core import db as dbmod  # noqa: E402
from lbc.core.config import get_settings, reset_settings_cache  # noqa: E402
from lbc.core.models import Base  # noqa: E402

OWNER_ID = "100000000000000001"


def _admin_conn(database=None):
    return pymysql.connect(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
                           password=os.environ["DB_PASSWORD"], database=database, autocommit=True)


@pytest.fixture(scope="session", autouse=True)
def database():
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE `{DB_NAME}` CHARACTER SET latin1")  # bewusst latin1 wie manche Hoster
    conn.close()
    reset_settings_cache()
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield cfg
    dbmod.dispose_engine()
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{DB_NAME}`")
    conn.close()


@pytest.fixture(autouse=True)
def clean_db(database):
    """Vor jedem Test alle Tabellen leeren und Stammdaten neu anlegen."""
    conn = _admin_conn(DB_NAME)
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for table in Base.metadata.tables:
            cur.execute(f"TRUNCATE TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
    conn.close()
    from lbc.services.applications import seed_default_types
    from lbc.services.team import bootstrap
    with dbmod.session_scope() as s:
        bootstrap(s, OWNER_ID)
        seed_default_types(s)
    yield


@pytest.fixture
def session():
    s = dbmod.get_session()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def app():
    from lbc.api.main import create_app
    return create_app(with_lifespan=False)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app, base_url="http://testserver") as c:
        yield c


def make_member(discord_id: str, role_name: str | None = None, overrides: list[tuple[str, bool]] | None = None,
                username: str | None = None) -> int:
    from sqlalchemy import select

    from lbc.core.models import MemberPermissionOverride, TeamMember, TeamRole
    from lbc.services.base import upsert_discord_user
    with dbmod.session_scope() as s:
        user = upsert_discord_user(s, discord_id, username=username or f"user{discord_id[-4:]}")
        s.flush()
        role = s.scalar(select(TeamRole).where(TeamRole.name == role_name)) if role_name else None
        m = TeamMember(user_id=user.id, role_id=role.id if role else None, status="active")
        s.add(m)
        s.flush()
        for perm, allow in overrides or []:
            s.add(MemberPermissionOverride(member_id=m.id, permission=perm, allow=allow))
        return m.id


def login(client, discord_id: str) -> str:
    """Erzeugt eine Panel-Sitzung direkt (ohne Discord) und gibt das CSRF-Token zurück."""
    from datetime import timedelta

    from lbc.core.crypto import hash_token, new_token
    from lbc.core.models import WebSession, utcnow
    from lbc.services.team import find_member_by_discord_id
    token, csrf = new_token(), new_token(16)
    with dbmod.session_scope() as s:
        m = find_member_by_discord_id(s, discord_id)
        s.add(WebSession(token_hash=hash_token(token), csrf_token=csrf, member_id=m.id,
                         expires_at=utcnow() + timedelta(hours=1)))
    client.cookies.set("lbc_session", token)
    client.headers["X-CSRF-Token"] = csrf
    return csrf


@pytest.fixture
def owner_client(client):
    login(client, OWNER_ID)
    return client


_ = get_settings
