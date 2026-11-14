"""Lesen und Schreiben der Systemeinstellungen (Geheimnisse verschlüsselt)."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import crypto
from ..core.config import get_settings
from ..core.models import SystemSetting
from ..core.settings_catalog import GROUP_LABELS, SETTINGS, SETTINGS_BY_KEY, SettingDef
from .base import Actor, Invalid, audit, emit

SNOWFLAKE_RE = re.compile(r"^\d{15,21}$")


def _env_value(d: SettingDef) -> Optional[str]:
    if not d.env:
        return None
    # Werte aus der geladenen Konfiguration (inkl. .env-Datei)
    attr = d.env.lower()
    val = getattr(get_settings(), attr, None)
    if val in (None, ""):
        val = os.environ.get(d.env)
    return str(val) if val not in (None, "") else None


def _decode(d: SettingDef, raw: Optional[str]) -> Any:
    if raw is None:
        return d.default
    if d.type == "bool":
        return raw in ("1", "true", "True", "yes")
    if d.type == "int":
        try:
            return int(raw)
        except ValueError:
            return d.default
    return raw


def get_value(session: Session, key: str) -> Any:
    d = SETTINGS_BY_KEY[key]
    row = session.get(SystemSetting, key)
    if row is not None and row.value not in (None, ""):
        raw = crypto.decrypt(row.value) if row.is_secret else row.value
        return _decode(d, raw)
    env = _env_value(d)
    if env is not None:
        return _decode(d, env)
    return d.default


def get_values(session: Session, keys: Optional[list[str]] = None) -> dict[str, Any]:
    keys = keys or [s.key for s in SETTINGS]
    rows = {r.key: r for r in session.scalars(select(SystemSetting).where(SystemSetting.key.in_(keys)))}
    out: dict[str, Any] = {}
    for key in keys:
        d = SETTINGS_BY_KEY[key]
        row = rows.get(key)
        if row is not None and row.value not in (None, ""):
            out[key] = _decode(d, crypto.decrypt(row.value) if row.is_secret else row.value)
        else:
            env = _env_value(d)
            out[key] = _decode(d, env) if env is not None else d.default
    return out


def validate(d: SettingDef, value: Any) -> Optional[str]:
    """Normalisiert und validiert einen Eingabewert. Rückgabe: zu speichernder String oder None (= löschen)."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if d.type == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        if str(value).lower() in ("true", "false", "1", "0"):
            return "true" if str(value).lower() in ("true", "1") else "false"
        raise Invalid(f"{d.label}: Ja/Nein-Wert erwartet.")
    if d.type == "int":
        try:
            num = int(value)
        except (TypeError, ValueError):
            raise Invalid(f"{d.label}: Ganzzahl erwartet.") from None
        if num < 0 or num > 100000:
            raise Invalid(f"{d.label}: Wert außerhalb des gültigen Bereichs.")
        return str(num)
    text = str(value).strip()
    if d.type == "snowflake" and not SNOWFLAKE_RE.match(text):
        raise Invalid(f"{d.label}: Eine Discord-ID besteht aus 15–21 Ziffern.")
    if d.key == "discord.redirect_uri" and not re.match(r"^https?://", text):
        raise Invalid("Die Redirect URL muss mit http:// oder https:// beginnen.")
    if len(text) > 4000:
        raise Invalid(f"{d.label}: Wert ist zu lang.")
    return text


def set_values(session: Session, actor: Actor, values: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    restart_needed = False
    for key, value in values.items():
        d = SETTINGS_BY_KEY.get(key)
        if d is None:
            raise Invalid(f"Unbekannte Einstellung: {key}")
        if d.secret and value in (None, ""):
            # Leere Eingabe bei Geheimnissen bedeutet "unverändert lassen"
            continue
        stored = validate(d, value)
        row = session.get(SystemSetting, key)
        old_plain = None
        if row is not None and row.value not in (None, ""):
            old_plain = crypto.decrypt(row.value) if row.is_secret else row.value
        if old_plain == stored:
            continue
        if row is None:
            row = SystemSetting(key=key)
            session.add(row)
        row.is_secret = d.secret
        row.value = crypto.encrypt(stored) if (d.secret and stored is not None) else stored
        row.updated_by = actor.name
        changed.append(key)
        restart_needed = restart_needed or d.restart
        audit(session, actor, "settings.changed", "setting", key,
              {"old": None if d.secret else old_plain, "new": "***" if d.secret else stored})
    if changed:
        emit(session, "settings")
    return changed


def clear_secret(session: Session, actor: Actor, key: str) -> None:
    d = SETTINGS_BY_KEY.get(key)
    if d is None or not d.secret:
        raise Invalid("Nur Geheimnisse können zurückgesetzt werden.")
    row = session.get(SystemSetting, key)
    if row is not None:
        session.delete(row)
        audit(session, actor, "settings.secret_cleared", "setting", key)
        emit(session, "settings")


def describe(session: Session) -> dict:
    """Einstellungen für das Webpanel – Geheimnisse werden NIE im Klartext ausgegeben."""
    rows = {r.key: r for r in session.scalars(select(SystemSetting))}
    groups: dict[str, dict] = {}
    for d in SETTINGS:
        row = rows.get(d.key)
        has_db = row is not None and row.value not in (None, "")
        env = _env_value(d)
        if d.secret:
            value = None
            is_set = has_db or env is not None
        else:
            raw = row.value if has_db else env
            value = _decode(d, raw) if raw is not None else d.default
            is_set = raw is not None
        g = groups.setdefault(d.group, {"key": d.group, "label": GROUP_LABELS.get(d.group, d.group), "items": []})
        g["items"].append({
            "key": d.key, "label": d.label, "type": d.type, "secret": d.secret, "value": value,
            "is_set": is_set, "source": "panel" if has_db else ("env" if env is not None else "default"),
            "help": d.help, "restart": d.restart,
            "updated_at": row.updated_at.isoformat() if has_db and row else None,
            "updated_by": row.updated_by if has_db and row else None,
        })
    return {"groups": list(groups.values())}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
