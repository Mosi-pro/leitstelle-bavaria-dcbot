"""Vorabprüfung der STRATO-MySQL-Verbindung.

Aufruf:  python -m lbc.tools.dbcheck  [--json]

Prüft nacheinander:
 1. Erreichbarkeit des Datenbankservers (TCP)
 2. Zugangsdaten
 3. Vorhandensein der Datenbank
 4. Benötigte Berechtigungen (CREATE/ALTER/INDEX/INSERT/UPDATE/DELETE/DROP/REFERENCES)
 5. Kompatible MySQL-Version
 6. Sichere (TLS-)Verbindung

Exit-Code 0 = alles in Ordnung, 1 = mindestens eine Pflichtprüfung fehlgeschlagen.
"""
from __future__ import annotations

import json
import re
import socket
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional

import pymysql

from ..core.config import Settings, get_settings
from ..core.db import DatabaseConfigError, build_ssl_context, raw_connect, ssl_required

STRATO_HINT = (
    "Hinweis zu STRATO: Je nach Hostingtarif sind STRATO-MySQL-Datenbanken nur aus dem STRATO-Webhosting "
    "selbst erreichbar. Prüfe im STRATO-Kundenbereich bzw. beim STRATO-Support, ob für deinen Tarif ein "
    "externer Zugriff (Remote-Zugriff) möglich ist und ob die öffentliche IP-Adresse deines Anschlusses "
    "freigegeben werden muss. Ohne externen Zugriff kann der Dell Wyse die Datenbank nicht nutzen."
)


@dataclass
class CheckResult:
    key: str
    title: str
    ok: bool
    message: str
    hint: Optional[str] = None
    required: bool = True


@dataclass
class CheckReport:
    ok: bool
    checks: list[CheckResult] = field(default_factory=list)
    server_version: Optional[str] = None

    def as_dict(self) -> dict:
        return {"ok": self.ok, "server_version": self.server_version, "checks": [asdict(c) for c in self.checks]}


def parse_version(version: str) -> tuple[str, tuple[int, int, int]]:
    flavor = "mariadb" if "mariadb" in version.lower() else "mysql"
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    nums = tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
    return flavor, nums  # type: ignore[return-value]


def version_compatible(version: str) -> tuple[bool, str]:
    flavor, nums = parse_version(version)
    if flavor == "mysql":
        if nums >= (8, 0, 0):
            return True, f"MySQL {version} ist kompatibel."
        if nums >= (5, 7, 8):
            return True, f"MySQL {version} wird unterstützt (MySQL 8 wird empfohlen)."
        return False, f"MySQL {version} ist zu alt. Benötigt wird mindestens MySQL 5.7.8, empfohlen MySQL 8."
    if nums >= (10, 5, 0):
        return True, f"MariaDB {version} ist kompatibel."
    return False, f"MariaDB {version} ist zu alt (mindestens 10.5 erforderlich)."


def _connect(settings: Settings, with_db: bool) -> pymysql.connections.Connection:
    return raw_connect(settings, with_db=with_db)


def run_checks(settings: Optional[Settings] = None) -> CheckReport:
    settings = settings or get_settings()
    report = CheckReport(ok=False)
    add = report.checks.append

    missing = settings.missing_db_fields()
    if missing:
        add(CheckResult("config", "Konfiguration", False,
                        "Fehlende Werte in der .env: " + ", ".join(missing),
                        "Trage die STRATO-Zugangsdaten in die .env ein oder führe ./install.sh erneut aus."))
        return report
    try:
        build_ssl_context(settings)
    except DatabaseConfigError as exc:
        add(CheckResult("config", "Konfiguration", False, str(exc)))
        return report

    # 1. Erreichbarkeit
    try:
        with socket.create_connection((settings.db_host, settings.db_port), timeout=settings.db_connect_timeout):
            pass
        add(CheckResult("reachable", "Datenbankserver erreichbar", True,
                        f"{settings.db_host}:{settings.db_port} ist erreichbar."))
    except socket.gaierror:
        add(CheckResult("reachable", "Datenbankserver erreichbar", False,
                        f"Der Hostname '{settings.db_host}' konnte nicht aufgelöst werden.",
                        "Prüfe DB_HOST (Schreibweise) und die Internetverbindung des Servers. " + STRATO_HINT))
        return report
    except OSError as exc:
        add(CheckResult("reachable", "Datenbankserver erreichbar", False,
                        f"Keine Verbindung zu {settings.db_host}:{settings.db_port} möglich ({exc}).",
                        "Prüfe DB_PORT, Firewall und IP-Freigaben. " + STRATO_HINT))
        return report

    # 2. Zugangsdaten
    try:
        conn = _connect(settings, with_db=False)
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else 0
        if code == 1045:
            msg, hint = ("Benutzername oder Passwort ist falsch (Zugriff verweigert).",
                         "Prüfe DB_USER und DB_PASSWORD. Bei STRATO ist der Datenbankbenutzer meist nicht "
                         "identisch mit dem Login für den Kundenbereich.")
        elif code in (1130,):
            msg, hint = ("Der Datenbankserver erlaubt keine Verbindungen von dieser IP-Adresse.", STRATO_HINT)
        else:
            msg, hint = f"Anmeldung fehlgeschlagen: {exc}", STRATO_HINT
        add(CheckResult("credentials", "Zugangsdaten korrekt", False, msg, hint))
        return report
    add(CheckResult("credentials", "Zugangsdaten korrekt", True, "Anmeldung am Datenbankserver erfolgreich."))

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            row = cur.fetchone()
            cipher = row[1] if row else ""
            cur.execute("SHOW DATABASES LIKE %s", (settings.db_name,))
            db_listed = cur.fetchone() is not None
    finally:
        conn.close()
    report.server_version = version

    # 3. Datenbank vorhanden
    try:
        conn = _connect(settings, with_db=True)
        add(CheckResult("database", "Datenbank vorhanden", True, f"Datenbank '{settings.db_name}' ist vorhanden."))
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else 0
        if code == 1049 or not db_listed:
            msg = f"Die Datenbank '{settings.db_name}' existiert nicht."
        else:
            msg = f"Kein Zugriff auf die Datenbank '{settings.db_name}' ({exc})."
        add(CheckResult("database", "Datenbank vorhanden", False, msg,
                        "Lege die Datenbank im STRATO-Kundenbereich an und prüfe DB_NAME."))
        return report

    # 4. Berechtigungen
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS _lbc_priv_check_child")
            cur.execute("DROP TABLE IF EXISTS _lbc_priv_check")
            cur.execute("CREATE TABLE _lbc_priv_check (id INT PRIMARY KEY AUTO_INCREMENT, v VARCHAR(20)) ENGINE=InnoDB")
            cur.execute("CREATE TABLE _lbc_priv_check_child (id INT PRIMARY KEY, pid INT, "
                        "CONSTRAINT fk_lbc_check FOREIGN KEY (pid) REFERENCES _lbc_priv_check(id)) ENGINE=InnoDB")
            cur.execute("ALTER TABLE _lbc_priv_check ADD INDEX ix_v (v)")
            cur.execute("INSERT INTO _lbc_priv_check (v) VALUES ('a')")
            cur.execute("UPDATE _lbc_priv_check SET v='b'")
            cur.execute("DELETE FROM _lbc_priv_check")
            cur.execute("DROP TABLE _lbc_priv_check_child")
            cur.execute("DROP TABLE _lbc_priv_check")
        conn.commit()
        add(CheckResult("privileges", "Benötigte Berechtigungen", True,
                        "CREATE, ALTER, INDEX, REFERENCES, INSERT, UPDATE, DELETE und DROP sind erlaubt."))
        privileges_ok = True
    except pymysql.err.MySQLError as exc:
        add(CheckResult("privileges", "Benötigte Berechtigungen", False,
                        f"Fehlende Berechtigung: {exc}",
                        "Der Datenbankbenutzer benötigt volle Rechte auf die eigene Datenbank "
                        "(CREATE, ALTER, INDEX, REFERENCES, INSERT, UPDATE, DELETE, DROP)."))
        privileges_ok = False
    finally:
        conn.close()

    # 5. Version
    v_ok, v_msg = version_compatible(version)
    add(CheckResult("version", "MySQL-Version kompatibel", v_ok, v_msg))

    # 6. Sichere Verbindung
    must = ssl_required(settings)
    if cipher:
        add(CheckResult("tls", "Sichere Verbindung", True, f"Verbindung ist TLS-verschlüsselt ({cipher}).",
                        required=must))
    else:
        add(CheckResult("tls", "Sichere Verbindung", False,
                        "Die Verbindung ist NICHT verschlüsselt (Server bietet kein TLS an oder DB_SSL_MODE=disabled).",
                        "Setze DB_SSL_MODE=required, sobald der Server TLS unterstützt.", required=must))

    report.ok = privileges_ok and v_ok and all(c.ok for c in report.checks if c.required)
    return report


def main(argv: list[str]) -> int:
    report = run_checks()
    if "--json" in argv:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print("Prüfung der STRATO-MySQL-Datenbank")
        print("=" * 40)
        for c in report.checks:
            mark = "OK " if c.ok else ("!! " if c.required else "-- ")
            print(f"[{mark}] {c.title}: {c.message}")
            if not c.ok and c.hint:
                print(f"       → {c.hint}")
        print("=" * 40)
        print("Ergebnis: " + ("Datenbank einsatzbereit." if report.ok else "Datenbank NICHT einsatzbereit."))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
