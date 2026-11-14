"""Datenbankanbindung an die zentrale STRATO-MySQL-Datenbank.

- Connection Pooling (QueuePool) mit pre-ping und Recycle (Reconnect-Verhalten)
- TLS-Konfiguration über DB_SSL_MODE
- Transaktions-Helfer (session_scope) und Wiederholungslogik bei transienten Fehlern
- Es gibt bewusst KEINEN Fallback auf SQLite oder eine andere lokale Datenbank.
"""
from __future__ import annotations

import functools
import logging
import ssl
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional, TypeVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings

log = logging.getLogger("lbc.db")

T = TypeVar("T")

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None
_lock = threading.Lock()

# Zeitpunkt des letzten erfolgreichen Datenbankzugriffs (pro Prozess)
_last_success: Optional[datetime] = None
_last_error: Optional[str] = None


class DatabaseConfigError(RuntimeError):
    """Konfiguration unvollständig oder fehlerhaft."""


# MySQL-Fehlercodes, bei denen eine Wiederholung sinnvoll ist
TRANSIENT_CODES = {1205, 1213, 2003, 2006, 2013, 2055}


def build_ssl_context(settings: Settings) -> Optional[ssl.SSLContext]:
    mode = (settings.db_ssl_mode or "preferred").lower()
    if mode in ("disabled", "disable", "off", "false", "0"):
        return None
    if mode in ("verify_ca", "verify_identity", "verify-full", "verify-ca"):
        ctx = ssl.create_default_context(cafile=settings.db_ssl_ca or None)
        ctx.check_hostname = mode in ("verify_identity", "verify-full")
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx
    if mode not in ("preferred", "prefer", "required", "require"):
        raise DatabaseConfigError(
            f"Ungültiger Wert für DB_SSL_MODE: '{settings.db_ssl_mode}'. "
            "Erlaubt: disabled, preferred, required, verify_ca, verify_identity"
        )
    # preferred/required: verschlüsselt, ohne Zertifikatsprüfung (wie mysql-Client)
    ctx = ssl.create_default_context(cafile=settings.db_ssl_ca or None)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE if not settings.db_ssl_ca else ssl.CERT_REQUIRED
    return ctx


def ssl_required(settings: Settings) -> bool:
    return (settings.db_ssl_mode or "").lower() in (
        "required", "require", "verify_ca", "verify_identity", "verify-full", "verify-ca"
    )


def build_url(settings: Settings, include_db: bool = True) -> URL:
    missing = settings.missing_db_fields()
    if missing:
        raise DatabaseConfigError(
            "Die STRATO-Datenbank ist nicht konfiguriert. Fehlende Werte in der .env: " + ", ".join(missing)
        )
    return URL.create(
        "mysql+pymysql",
        username=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name if include_db else None,
        query={"charset": "utf8mb4"},
    )


def ssl_preferred(settings: Settings) -> bool:
    return (settings.db_ssl_mode or "preferred").lower() in ("preferred", "prefer")


def raw_connect(settings: Settings, with_db: bool = True, read_timeout: int = 60):
    """Öffnet eine PyMySQL-Verbindung gemäß DB_SSL_MODE.

    preferred: TLS wird versucht; bietet der Server kein TLS an, wird unverschlüsselt verbunden
               (entspricht dem Verhalten des mysql-Kommandozeilenclients).
    required/verify_*: ohne TLS schlägt die Verbindung fehl.
    """
    import pymysql
    from pymysql.constants import CLIENT

    kwargs = dict(
        # FOUND_ROWS: UPDATE liefert gefundene statt geänderte Zeilen (von SQLAlchemy vorausgesetzt)
        client_flag=CLIENT.FOUND_ROWS,
        host=settings.db_host, port=settings.db_port, user=settings.db_user, password=settings.db_password,
        connect_timeout=settings.db_connect_timeout, read_timeout=read_timeout, write_timeout=60,
        charset="utf8mb4", autocommit=False,
    )
    if with_db:
        kwargs["database"] = settings.db_name
    ctx = build_ssl_context(settings)
    if ctx is None:
        return pymysql.connect(**kwargs)
    try:
        return pymysql.connect(ssl=ctx, **kwargs)
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else 0
        if ssl_preferred(settings) and code == 2026:
            log.warning("Datenbankserver bietet kein TLS an – Verbindung unverschlüsselt (DB_SSL_MODE=preferred)")
            return pymysql.connect(**kwargs)
        raise


def _mark_success() -> None:
    global _last_success, _last_error
    _last_success = datetime.now(timezone.utc)
    _last_error = None


def _mark_error(msg: str) -> None:
    global _last_error
    _last_error = msg


def last_db_status() -> dict:
    return {
        "last_success": _last_success.isoformat() if _last_success else None,
        "last_error": _last_error,
    }


def create_db_engine(settings: Optional[Settings] = None) -> Engine:
    settings = settings or get_settings()
    engine = create_engine(
        build_url(settings),
        creator=lambda: raw_connect(settings),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        pool_timeout=30,
        isolation_level="READ COMMITTED",
        future=True,
    )

    must_encrypt = ssl_required(settings)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        try:
            cur.execute("SET time_zone = '+00:00'")
            if must_encrypt:
                cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                row = cur.fetchone()
                if not row or not row[1]:
                    raise DatabaseConfigError(
                        "DB_SSL_MODE verlangt eine verschlüsselte Verbindung, der Server hat jedoch "
                        "keine TLS-Verbindung aufgebaut."
                    )
        finally:
            cur.close()

    @event.listens_for(engine, "after_cursor_execute")
    def _after_exec(*_a, **_k):
        _mark_success()

    @event.listens_for(engine, "handle_error")
    def _on_error(ctx):
        _mark_error(str(ctx.original_exception)[:500])

    return engine


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = create_db_engine()
                _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def dispose_engine() -> None:
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


def get_session() -> Session:
    get_engine()
    assert _session_factory is not None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaktion: Commit bei Erfolg, Rollback bei Fehler."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, DBAPIError) and exc.orig is not None and getattr(exc.orig, "args", None):
        code = exc.orig.args[0]
        return isinstance(code, int) and code in TRANSIENT_CODES
    return isinstance(exc, OperationalError)


def with_retry(attempts: int = 3, base_delay: float = 0.5) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Wiederholt eine komplette Transaktion bei Verbindungsabbrüchen oder Deadlocks."""

    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            for i in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except (OperationalError, DBAPIError) as exc:
                    if i == attempts - 1 or not is_transient(exc):
                        raise
                    log.warning("Transienter Datenbankfehler (%s), Wiederholung %s/%s", exc.orig, i + 1, attempts)
                    time.sleep(base_delay * (2 ** i))
            raise RuntimeError("unreachable")

        return wrapper

    return deco


def ping() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        _mark_error(str(exc)[:500])
        return False
