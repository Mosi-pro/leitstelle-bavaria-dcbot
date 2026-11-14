"""Datenbankanbindung, Vorabprüfung und Migrationen."""
from __future__ import annotations

import socket

import pytest
from alembic import command
from sqlalchemy import inspect, text

from lbc.core import db as dbmod
from lbc.core.config import get_settings
from lbc.core.models import Base
from lbc.tools import dbcheck


def test_all_tables_created_as_innodb_utf8mb4():
    engine = dbmod.get_engine()
    names = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= names
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT table_name, engine, table_collation FROM information_schema.tables WHERE table_schema = DATABASE() "
            "AND table_name <> 'alembic_version'")).all()
    assert rows
    for name, engine_name, collation in rows:
        assert engine_name == "InnoDB", name
        assert collation.startswith("utf8mb4"), name


def test_foreign_keys_and_unique_indexes_exist():
    insp = inspect(dbmod.get_engine())
    fks = insp.get_foreign_keys("bugs")
    assert any(fk["referred_table"] == "game_versions" for fk in fks)
    uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("bugs")}
    idx = {tuple(i["column_names"]) for i in insp.get_indexes("bugs") if i.get("unique")}
    assert ("forum_thread_id",) in uniques | idx


def test_migration_downgrade_and_upgrade_roundtrip(database):
    command.downgrade(database, "base")
    assert "bugs" not in inspect(dbmod.get_engine()).get_table_names()
    command.upgrade(database, "head")
    assert "bugs" in inspect(dbmod.get_engine()).get_table_names()


def test_models_match_migrations(database):
    """alembic check: das Datenmodell entspricht exakt den Migrationen."""
    command.check(database)


def test_dbcheck_success():
    report = dbcheck.run_checks()
    assert report.ok, report.as_dict()
    keys = [c.key for c in report.checks]
    assert keys == ["reachable", "credentials", "database", "privileges", "version", "tls"]


def test_dbcheck_wrong_password():
    cfg = get_settings().model_copy(update={"db_password": "Xq9-geheim-Zz"})
    report = dbcheck.run_checks(cfg)
    assert not report.ok
    failed = [c for c in report.checks if not c.ok][0]
    assert failed.key == "credentials"
    assert "Xq9-geheim-Zz" not in failed.message


def test_dbcheck_missing_database():
    cfg = get_settings().model_copy(update={"db_name": "gibt_es_nicht_123"})
    report = dbcheck.run_checks(cfg)
    assert not report.ok
    assert [c for c in report.checks if not c.ok][0].key == "database"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_dbcheck_unreachable_gives_strato_hint():
    cfg = get_settings().model_copy(update={"db_port": _free_port(), "db_connect_timeout": 2})
    report = dbcheck.run_checks(cfg)
    assert not report.ok
    c = report.checks[0]
    assert c.key == "reachable" and not c.ok
    assert "STRATO" in c.hint


def test_dbcheck_missing_config():
    cfg = get_settings().model_copy(update={"db_host": ""})
    report = dbcheck.run_checks(cfg)
    assert not report.ok and report.checks[0].key == "config"


def test_no_sqlite_fallback_when_unconfigured():
    cfg = get_settings().model_copy(update={"db_host": ""})
    with pytest.raises(dbmod.DatabaseConfigError):
        dbmod.create_db_engine(cfg)


def test_invalid_ssl_mode_rejected():
    cfg = get_settings().model_copy(update={"db_ssl_mode": "irgendwas"})
    with pytest.raises(dbmod.DatabaseConfigError):
        dbmod.build_ssl_context(cfg)


def test_version_compatibility():
    assert dbcheck.version_compatible("8.0.36")[0]
    assert dbcheck.version_compatible("5.7.44-log")[0]
    assert not dbcheck.version_compatible("5.6.51")[0]
    assert dbcheck.version_compatible("10.11.6-MariaDB")[0]
    assert not dbcheck.version_compatible("10.3.39-MariaDB")[0]


def test_tls_required_mode(monkeypatch):
    """Bei DB_SSL_MODE=required muss die Verbindung verschlüsselt sein (Testserver bietet TLS)."""
    cfg = get_settings().model_copy(update={"db_ssl_mode": "required"})
    conn = dbmod.raw_connect(cfg)
    with conn.cursor() as cur:
        cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        cipher = cur.fetchone()[1]
    conn.close()
    if not cipher:
        pytest.skip("Testserver ohne TLS")
    engine = dbmod.create_db_engine(cfg)
    with engine.connect() as c:
        assert c.execute(text("SELECT 1")).scalar() == 1
    engine.dispose()


def test_retry_on_transient_error():
    from sqlalchemy.exc import OperationalError
    calls = {"n": 0}

    class Orig(Exception):
        pass

    @dbmod.with_retry(attempts=3, base_delay=0.01)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError("SELECT 1", {}, Orig(2013, "Lost connection"))
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_api_returns_503_when_database_unreachable(app, monkeypatch):
    """STRATO-Datenbank nicht erreichbar → verständliche Fehlermeldung statt Absturz."""
    from fastapi.testclient import TestClient
    cfg = get_settings().model_copy(update={"db_port": _free_port(), "db_connect_timeout": 1})
    bad_engine = dbmod.create_db_engine(cfg)
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(dbmod, "_engine", bad_engine)
    monkeypatch.setattr(dbmod, "_session_factory", sessionmaker(bind=bad_engine))
    with TestClient(app) as c:
        c.cookies.set("lbc_session", "irgendein-token")
        r = c.get("/api/dashboard")
        assert r.status_code == 503
        assert "STRATO" in r.json()["detail"]
        assert c.get("/api/health").status_code == 503
    bad_engine.dispose()
