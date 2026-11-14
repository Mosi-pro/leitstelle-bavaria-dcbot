"""Backup und Wiederherstellung (Datenbank-Dump der STRATO-MySQL-Datenbank + Konfiguration).

Aufrufe (im Backend-Container, gesteuert durch backup.sh / restore.sh):
  python -m lbc.tools.backup create [--dir /backups] [--scheduled]
  python -m lbc.tools.backup verify <datei>
  python -m lbc.tools.backup restore <datei> --yes
  python -m lbc.tools.backup list

Format: tar.gz mit
  manifest.json  – Version, Zeitpunkt, SHA-256-Prüfsummen
  database.sql.gz – mysqldump (--single-transaction, ohne Tablespaces/Trigger-Rechte)
  config.env     – Kopie der .env (enthält Geheimnisse → Datei-Rechte 600)
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..core.config import Settings, get_settings

FORMAT_VERSION = 1
DEFAULT_DIR = os.environ.get("BACKUP_DIR", "/backups")
PREFIX = "lbc-backup-"


class BackupError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _client_binary(names: tuple[str, ...]) -> str:
    for n in names:
        path = shutil.which(n)
        if path:
            return path
    raise BackupError(f"Programm nicht gefunden: {' / '.join(names)} (MySQL-Client im Container installieren)")


def _defaults_file(settings: Settings, tmpdir: str) -> str:
    """Zugangsdaten über eine temporäre Optionsdatei übergeben (nicht in der Prozessliste sichtbar)."""
    path = os.path.join(tmpdir, "client.cnf")
    mode = (settings.db_ssl_mode or "preferred").lower()
    lines = ["[client]", f"host={settings.db_host}", f"port={settings.db_port}", f"user={settings.db_user}",
             f'password="{settings.db_password}"']
    if mode in ("disabled", "disable", "off"):
        lines.append("skip-ssl")
    elif mode in ("required", "require", "verify_ca", "verify_identity"):
        lines.append("ssl")
        if settings.db_ssl_ca:
            lines.append(f"ssl-ca={settings.db_ssl_ca}")
        if mode == "verify_identity":
            lines.append("ssl-verify-server-cert")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def dump_database(settings: Settings) -> bytes:
    binary = _client_binary(("mariadb-dump", "mysqldump"))
    with tempfile.TemporaryDirectory() as tmp:
        cnf = _defaults_file(settings, tmp)
        cmd = [binary, f"--defaults-extra-file={cnf}", "--single-transaction", "--quick", "--no-tablespaces",
               "--skip-triggers", "--routines=FALSE", "--events=FALSE", "--default-character-set=utf8mb4",
               "--hex-blob", settings.db_name]
        proc = subprocess.run(cmd, capture_output=True, timeout=1800)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace").strip()
            if "ssl" in err.lower() and (settings.db_ssl_mode or "").lower() in ("preferred", "prefer"):
                # Server ohne TLS: erneut ohne TLS versuchen (DB_SSL_MODE=preferred)
                cmd.insert(2, "--skip-ssl")
                proc = subprocess.run(cmd, capture_output=True, timeout=1800)
                err = proc.stderr.decode(errors="replace").strip()
            if proc.returncode != 0:
                raise BackupError(f"Datenbank-Dump fehlgeschlagen: {err[:500]}")
        if b"CREATE TABLE" not in proc.stdout:
            raise BackupError("Der Datenbank-Dump enthält keine Tabellen – Backup abgebrochen.")
        return proc.stdout


def create_backup(directory: str = DEFAULT_DIR, settings: Optional[Settings] = None,
                  env_path: Optional[str] = None) -> Path:
    settings = settings or get_settings()
    env_path = env_path or settings.env_file_path
    os.makedirs(directory, exist_ok=True)
    os.chmod(directory, 0o700)
    sql = dump_database(settings)
    sql_gz = gzip.compress(sql, compresslevel=6)
    env_bytes = Path(env_path).read_bytes() if env_path and os.path.isfile(env_path) else b""
    now = datetime.now(timezone.utc)
    manifest = {
        "format": FORMAT_VERSION, "application": "Leitstelle Bavaria Control", "created_at": now.isoformat(),
        "database": settings.db_name, "db_host": settings.db_host,
        "files": {"database.sql.gz": _sha256(sql_gz), **({"config.env": _sha256(env_bytes)} if env_bytes else {})},
        "sql_bytes": len(sql),
    }
    name = f"{PREFIX}{now:%Y%m%d-%H%M%S}.tar.gz"
    target = Path(directory) / name
    tmp_target = target.with_suffix(".partial")
    fd = os.open(tmp_target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for fname, data in (("manifest.json", json.dumps(manifest, indent=2).encode()), ("database.sql.gz", sql_gz),
                            *((("config.env", env_bytes),) if env_bytes else ())):
            info = tarfile.TarInfo(fname)
            info.size = len(data)
            info.mode = 0o600
            info.mtime = int(now.timestamp())
            tar.addfile(info, io.BytesIO(data))
    os.replace(tmp_target, target)
    os.chmod(target, 0o600)
    return target


def read_backup(path: str) -> dict[str, bytes]:
    if not os.path.isfile(path):
        raise BackupError(f"Backup-Datei nicht gefunden: {path}")
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = {m.name: m for m in tar.getmembers()}
            for name in members:
                if name not in ("manifest.json", "database.sql.gz", "config.env"):
                    raise BackupError(f"Unerwartete Datei im Backup: {name}")
            if "manifest.json" not in members or "database.sql.gz" not in members:
                raise BackupError("Backup unvollständig (manifest.json oder database.sql.gz fehlt).")
            return {name: tar.extractfile(m).read() for name, m in members.items() if m.isfile()}
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(f"Backup-Datei ist beschädigt: {exc}") from exc


def verify_backup(path: str) -> dict:
    files = read_backup(path)
    manifest = json.loads(files["manifest.json"])
    if manifest.get("format") != FORMAT_VERSION:
        raise BackupError(f"Unbekanntes Backup-Format: {manifest.get('format')}")
    for name, digest in manifest.get("files", {}).items():
        if name not in files or _sha256(files[name]) != digest:
            raise BackupError(f"Prüfsumme stimmt nicht: {name} – Backup ist beschädigt oder manipuliert.")
    try:
        sql = gzip.decompress(files["database.sql.gz"])
    except OSError as exc:
        raise BackupError("Datenbank-Dump ist nicht lesbar.") from exc
    if b"CREATE TABLE" not in sql:
        raise BackupError("Datenbank-Dump enthält keine Tabellen.")
    return {"manifest": manifest, "sql_bytes": len(sql), "has_config": "config.env" in files}


def restore_backup(path: str, settings: Optional[Settings] = None, env_out: Optional[str] = None) -> dict:
    settings = settings or get_settings()
    info = verify_backup(path)
    files = read_backup(path)
    sql = gzip.decompress(files["database.sql.gz"])
    binary = _client_binary(("mariadb", "mysql"))
    with tempfile.TemporaryDirectory() as tmp:
        cnf = _defaults_file(settings, tmp)
        cmd = [binary, f"--defaults-extra-file={cnf}", "--default-character-set=utf8mb4", settings.db_name]
        proc = subprocess.run(cmd, input=b"SET FOREIGN_KEY_CHECKS=0;\n" + sql + b"\nSET FOREIGN_KEY_CHECKS=1;\n",
                              capture_output=True, timeout=3600)
        if proc.returncode != 0 and "ssl" in proc.stderr.decode(errors="replace").lower() and \
                (settings.db_ssl_mode or "").lower() in ("preferred", "prefer"):
            cmd.insert(2, "--skip-ssl")
            proc = subprocess.run(cmd, input=b"SET FOREIGN_KEY_CHECKS=0;\n" + sql + b"\nSET FOREIGN_KEY_CHECKS=1;\n",
                                  capture_output=True, timeout=3600)
        if proc.returncode != 0:
            raise BackupError(f"Wiederherstellung fehlgeschlagen: {proc.stderr.decode(errors='replace')[:500]}")
    if env_out and "config.env" in files:
        fd = os.open(env_out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(files["config.env"])
    return info


def list_backups(directory: str = DEFAULT_DIR) -> list[dict]:
    if not os.path.isdir(directory):
        return []
    out = []
    for p in sorted(Path(directory).glob(f"{PREFIX}*.tar.gz"), reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size,
                    "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    return out


def prune(directory: str, keep: int) -> list[str]:
    files = sorted(Path(directory).glob(f"{PREFIX}*.tar.gz"), reverse=True)
    removed = []
    for p in files[max(keep, 1):]:
        p.unlink()
        removed.append(p.name)
    return removed


def _panel_setting(key: str, default):
    """Liest Backup-Einstellungen aus dem Webpanel; bei DB-Problemen Standardwert."""
    try:
        from ..core.db import session_scope
        from ..services import settings_service
        with session_scope() as s:
            v = settings_service.get_value(s, key)
            return default if v is None else v
    except Exception:  # noqa: BLE001
        return default


def scheduled_due(directory: str, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    hour = int(_panel_setting("backup.daily_hour", 3))
    if now.hour != hour:
        return False
    latest = list_backups(directory)
    if latest:
        last = datetime.fromisoformat(latest[0]["created_at"])
        if datetime.now(timezone.utc) - last < timedelta(hours=20):
            return False
    return True


def _log_result(ok: bool, message: str) -> None:
    try:
        from ..core.db import session_scope
        from ..services.base import Actor, audit, record_error
        with session_scope() as s:
            if ok:
                audit(s, Actor.system("Backup"), "system.backup_created", "backup", None, {"file": message})
            else:
                record_error(s, "backup", message)
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    directory = DEFAULT_DIR
    if "--dir" in args:
        directory = args[args.index("--dir") + 1]
    try:
        if cmd == "create":
            if "--scheduled" in args and not scheduled_due(directory):
                print("Kein Backup fällig.")
                return 0
            path = create_backup(directory)
            keep = int(_panel_setting("backup.keep_count", 14))
            removed = prune(directory, keep)
            _log_result(True, path.name)
            print(f"Backup erstellt: {path.name}")
            if removed:
                print(f"Alte Backups entfernt: {', '.join(removed)}")
            return 0
        if cmd == "verify":
            info = verify_backup(args[0])
            m = info["manifest"]
            print(f"Backup gültig. Erstellt: {m['created_at']}, Datenbank: {m['database']}, "
                  f"SQL: {info['sql_bytes']} Bytes, Konfiguration enthalten: {'ja' if info['has_config'] else 'nein'}")
            return 0
        if cmd == "restore":
            if "--yes" not in args:
                print("Sicherheitsabfrage fehlt (--yes).")
                return 2
            env_out = args[args.index("--env-out") + 1] if "--env-out" in args else None
            restore_backup(args[0], env_out=env_out)
            print("Datenbank wurde wiederhergestellt.")
            return 0
        if cmd == "list":
            for b in list_backups(directory):
                print(f"{b['name']}\t{b['size'] / 1024 / 1024:.2f} MB\t{b['created_at']}")
            return 0
    except BackupError as exc:
        _log_result(False, str(exc)) if cmd == "create" else None
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    print(f"Unbekannter Befehl: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
