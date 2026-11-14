"""Dashboard, Controlling, Reports, Audit-Logs, Systemeinstellungen, Automatisierungen, Synchronisierung."""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends
from sqlalchemy import and_, func, or_, select

from ..core.config import Settings, get_settings
from ..core.db import session_scope
from ..core.models import (
    AuditLog,
    Automation,
    DiscordOutbox,
    ErrorLog,
    Notification,
    NotificationRead,
    Report,
    SystemStatus,
    utcnow,
)
from ..services import automations as auto_svc
from ..services import reports as report_svc
from ..services import settings_service
from ..services import stats as stats_svc
from ..services.base import Conflict, Forbidden, Invalid, NotFound, audit, emit, enqueue
from ..tools import dbcheck
from .deps import Principal, get_principal, paginate, require

router = APIRouter(prefix="/api", tags=["system"])


# ---------------------------------------------------------------------------- Dashboard & Controlling
@router.get("/dashboard")
def dashboard(p: Principal = Depends(require("dashboard.view"))) -> dict:
    with session_scope() as s:
        return stats_svc.dashboard(s)


@router.get("/controlling")
def controlling(days: int = 30, p: Principal = Depends(require("controlling.view"))) -> dict:
    days = max(7, min(days, 365))
    with session_scope() as s:
        return stats_svc.controlling(s, days)


@router.get("/reports")
def list_reports(p: Principal = Depends(require("controlling.view"))) -> list[dict]:
    with session_scope() as s:
        return [report_svc.serialize(r) for r in s.scalars(select(Report).order_by(Report.created_at.desc()).limit(100))]


@router.post("/reports")
def create_report(data: dict = Body(default={}), p: Principal = Depends(require("reports.manage"))) -> dict:
    with session_scope() as s:
        start = end = None
        kind = "weekly"
        if data.get("start") and data.get("end"):
            try:
                start = datetime.fromisoformat(str(data["start"])[:10])
                end = datetime.fromisoformat(str(data["end"])[:10])
            except ValueError:
                raise Invalid("Ungültiger Zeitraum.") from None
            if end <= start:
                raise Invalid("Das Ende muss nach dem Beginn liegen.")
            kind = "custom"
        return report_svc.serialize(report_svc.generate(s, p.actor(), start, end, kind=kind, post=bool(data.get("post"))))


@router.post("/reports/{rid}/post")
def post_report(rid: int, p: Principal = Depends(require("reports.manage"))) -> dict:
    with session_scope() as s:
        report_svc.post_report(s, p.actor(), rid)
    return {"ok": True}


# ---------------------------------------------------------------------------- Audit
@router.get("/audit")
def audit_logs(action: Optional[str] = None, actor: Optional[str] = None, object_type: Optional[str] = None,
               object_id: Optional[str] = None, source: Optional[str] = None, date_from: Optional[str] = None,
               date_to: Optional[str] = None, page: int = 1, page_size: int = 100,
               p: Principal = Depends(require("logs.view"))) -> dict:
    with session_scope() as s:
        q = select(AuditLog)
        if action:
            q = q.where(AuditLog.action.like(f"{action}%"))
        if actor:
            q = q.where(or_(AuditLog.actor_name.like(f"%{actor}%"), AuditLog.actor_discord_id == actor))
        if object_type:
            q = q.where(AuditLog.object_type == object_type)
        if object_id:
            q = q.where(AuditLog.object_id == object_id)
        if source:
            q = q.where(AuditLog.source == source)
        try:
            if date_from:
                q = q.where(AuditLog.created_at >= datetime.fromisoformat(date_from[:10]))
            if date_to:
                q = q.where(AuditLog.created_at < datetime.fromisoformat(date_to[:10]).replace(hour=23, minute=59, second=59))
        except ValueError:
            raise Invalid("Ungültiges Datum.") from None
        q = q.order_by(AuditLog.id.desc())
        return paginate(s, q, page, page_size, lambda a: {
            "id": a.id, "actor_discord_id": a.actor_discord_id, "actor_name": a.actor_name, "action": a.action,
            "object_type": a.object_type, "object_id": a.object_id, "details": a.details, "source": a.source,
            "ip_address": a.ip_address, "created_at": a.created_at.isoformat()})


@router.get("/audit/actions")
def audit_actions(p: Principal = Depends(require("logs.view"))) -> list[str]:
    with session_scope() as s:
        return list(s.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)))


# ---------------------------------------------------------------------------- Benachrichtigungen
def _visible_notifications(p: Principal):
    conds = [Notification.member_id == p.member_id,
             and_(Notification.member_id.is_(None), Notification.permission.is_(None))]
    if p.permissions:
        conds.append(and_(Notification.member_id.is_(None), Notification.permission.in_(sorted(p.permissions))))
    return or_(*conds)


@router.get("/notifications")
def notifications(p: Principal = Depends(get_principal)) -> dict:
    with session_scope() as s:
        rows = s.scalars(select(Notification).where(_visible_notifications(p))
                         .order_by(Notification.created_at.desc()).limit(50)).all()
        read = set(s.scalars(select(NotificationRead.notification_id).where(
            NotificationRead.member_id == p.member_id, NotificationRead.notification_id.in_([r.id for r in rows] or [0]))))
        items = [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link,
                  "created_at": n.created_at.isoformat(), "read": n.id in read} for n in rows]
        return {"items": items, "unread": sum(1 for i in items if not i["read"])}


@router.post("/notifications/read")
def notifications_read(data: dict = Body(default={}), p: Principal = Depends(get_principal)) -> dict:
    with session_scope() as s:
        ids = data.get("ids")
        q = select(Notification.id).where(_visible_notifications(p))
        if ids:
            q = q.where(Notification.id.in_([int(i) for i in ids]))
        targets = set(s.scalars(q.order_by(Notification.id.desc()).limit(500)))
        done = set(s.scalars(select(NotificationRead.notification_id).where(
            NotificationRead.member_id == p.member_id, NotificationRead.notification_id.in_(targets or {0}))))
        for nid in targets - done:
            s.add(NotificationRead(notification_id=nid, member_id=p.member_id))
    return {"ok": True}


# ---------------------------------------------------------------------------- Einstellungen
@router.get("/settings")
def get_settings_view(p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        data = settings_service.describe(s)
        guild = s.get(SystemStatus, "guild")
        data["guild"] = guild.details if guild else None
        return data


@router.put("/settings")
def update_settings(data: dict = Body(...), p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        changed = settings_service.set_values(s, p.actor(), data.get("values") or {})
        restart = [k for k in changed if settings_service.SETTINGS_BY_KEY[k].restart]
        if restart:
            enqueue(s, "bot.restart", {"reason": "Einstellungen geändert: " + ", ".join(restart)},
                    idempotency_key=f"bot-restart-{int(time.time())}")
        return {"changed": changed, "bot_restart": bool(restart)}


@router.delete("/settings/{key}")
def clear_secret(key: str, p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        settings_service.clear_secret(s, p.actor(), key)
    return {"ok": True}


@router.get("/guild")
def guild_info(p: Principal = Depends(require("dashboard.view"))) -> dict:
    """Channels und Rollen des Discord-Servers (vom Bot bereitgestellt) für Auswahlfelder."""
    with session_scope() as s:
        g = s.get(SystemStatus, "guild")
        return g.details if g and g.details else {"channels": [], "roles": [], "name": None}


PANELS = {"ticket": "ticket.refresh_panel", "applications": "applications.refresh_panel",
          "verification": "verification.refresh_panel"}


@router.post("/discord/panels/{panel}")
def publish_panel(panel: str, p: Principal = Depends(require("settings.manage"))) -> dict:
    kind = PANELS.get(panel)
    if kind is None:
        raise NotFound("Unbekanntes Panel.")
    with session_scope() as s:
        enqueue(s, kind, {"force_new": True})
        audit(s, p.actor(), "system.panel_published", "discord_panel", panel)
    return {"ok": True}


@router.post("/discord/bot/restart")
def restart_bot(p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        enqueue(s, "bot.restart", {"reason": f"Manueller Neustart durch {p.name}"})
        audit(s, p.actor(), "system.bot_restart_requested", "bot", None)
    return {"ok": True}


# ---------------------------------------------------------------------------- Datenbank
@router.get("/system/status")
def system_status(p: Principal = Depends(require("dashboard.view"))) -> dict:
    with session_scope() as s:
        return stats_svc.system_status(s)


@router.get("/system/database")
def database_info(p: Principal = Depends(require("settings.manage"))) -> dict:
    cfg = get_settings()
    report = dbcheck.run_checks(cfg)
    return {"host": cfg.db_host, "port": cfg.db_port, "name": cfg.db_name, "user": cfg.db_user,
            "ssl_mode": cfg.db_ssl_mode, "env_file_writable": _env_writable(), **report.as_dict()}


def _env_writable() -> bool:
    path = get_settings().env_file_path
    return os.path.isfile(path) and os.access(path, os.W_OK)


ENV_KEYS = {"host": "DB_HOST", "port": "DB_PORT", "name": "DB_NAME", "user": "DB_USER", "password": "DB_PASSWORD",
            "ssl_mode": "DB_SSL_MODE"}


def _candidate_settings(data: dict) -> Settings:
    cur = get_settings()
    values = {
        "DB_HOST": str(data.get("host") or cur.db_host).strip(),
        "DB_PORT": int(data.get("port") or cur.db_port),
        "DB_NAME": str(data.get("name") or cur.db_name).strip(),
        "DB_USER": str(data.get("user") or cur.db_user).strip(),
        "DB_PASSWORD": data.get("password") if data.get("password") else cur.db_password,
        "DB_SSL_MODE": str(data.get("ssl_mode") or cur.db_ssl_mode),
        "DB_SSL_CA": cur.db_ssl_ca,
    }
    for k, v in values.items():
        if isinstance(v, str) and ("\n" in v or "\r" in v):
            raise Invalid(f"{k}: Zeilenumbrüche sind nicht erlaubt.")
    return cur.model_copy(update={"db_host": values["DB_HOST"], "db_port": values["DB_PORT"],
                                  "db_name": values["DB_NAME"], "db_user": values["DB_USER"],
                                  "db_password": values["DB_PASSWORD"], "db_ssl_mode": values["DB_SSL_MODE"]})


@router.post("/system/database/test")
def database_test(data: dict = Body(default={}), p: Principal = Depends(require("settings.manage"))) -> dict:
    return dbcheck.run_checks(_candidate_settings(data)).as_dict()


def write_env_values(path: str, updates: dict[str, str]) -> None:
    """Ersetzt Schlüssel in der .env-Datei atomar und mit Dateirechten 600."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    seen = set()
    out = []
    for line in lines:
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(f"{key}={_env_quote(updates[key])}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={_env_quote(val)}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    # Inhalt in-place schreiben (Bind-Mount einer einzelnen Datei verträgt kein rename)
    with open(tmp, encoding="utf-8") as src, open(path, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    os.remove(tmp)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Datei gehört einem anderen Benutzer (Bind-Mount) – Rechte wurden bei der Installation gesetzt


def _env_quote(v: str) -> str:
    """Quoting, das von Docker Compose, python-dotenv und bash gleich verstanden wird."""
    v = str(v)
    if not re.search(r"[\s#'\"$`\\]", v):
        return v
    if "'" not in v:
        return "'" + v + "'"
    if "$" in v or "`" in v:
        raise Invalid("Werte mit Hochkomma (') UND Dollarzeichen ($) werden nicht unterstützt.")
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


@router.put("/system/database")
def database_update(data: dict = Body(...), p: Principal = Depends(require("settings.manage"))) -> dict:
    if not p.is_owner:
        raise Forbidden("Nur der Inhaber kann die Datenbankverbindung ändern.")
    if not _env_writable():
        raise Conflict("Die Konfigurationsdatei ist nicht beschreibbar. Bitte ./install.sh --reconfigure ausführen.")
    cand = _candidate_settings(data)
    report = dbcheck.run_checks(cand)
    if not report.ok:
        return {"saved": False, **report.as_dict()}
    write_env_values(get_settings().env_file_path, {
        "DB_HOST": cand.db_host, "DB_PORT": str(cand.db_port), "DB_NAME": cand.db_name, "DB_USER": cand.db_user,
        "DB_PASSWORD": cand.db_password, "DB_SSL_MODE": cand.db_ssl_mode})
    with session_scope() as s:
        audit(s, p.actor(), "settings.database_changed", "setting", "database",
              {"host": cand.db_host, "name": cand.db_name, "user": cand.db_user, "ssl_mode": cand.db_ssl_mode})
        enqueue(s, "bot.restart", {"reason": "Datenbankverbindung geändert"})
    # Backend nach Antwort neu starten (Docker-Restart-Policy startet den Container neu)
    threading.Timer(2.0, lambda: os._exit(0)).start()
    return {"saved": True, "restarting": True, **report.as_dict()}


# ---------------------------------------------------------------------------- Automatisierungen & Outbox
@router.get("/system/automations")
def automations(p: Principal = Depends(require("settings.manage"))) -> list[dict]:
    with session_scope() as s:
        return [auto_svc.serialize(a) for a in s.scalars(select(Automation).order_by(Automation.id))]


@router.put("/system/automations/{key}")
def automation_update(key: str, data: dict = Body(...), p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        return auto_svc.serialize(auto_svc.update_automation(s, p.actor(), key, data))


@router.post("/system/automations/{key}/run")
def automation_run(key: str, p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        audit(s, p.actor(), "system.automation_run", "automation", key)
    return {"result": auto_svc.run_job(key, force=True)}


@router.get("/system/outbox")
def outbox(status: Optional[str] = None, page: int = 1, page_size: int = 50,
           p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        q = select(DiscordOutbox)
        if status:
            q = q.where(DiscordOutbox.status == status)
        counts = dict(s.execute(select(DiscordOutbox.status, func.count()).group_by(DiscordOutbox.status)).all())
        data = paginate(s, q.order_by(DiscordOutbox.id.desc()), page, page_size, lambda j: {
            "id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts, "max_attempts": j.max_attempts,
            "payload": j.payload, "last_error": j.last_error, "created_at": j.created_at.isoformat(),
            "next_attempt_at": j.next_attempt_at.isoformat(),
            "processed_at": j.processed_at.isoformat() if j.processed_at else None})
        data["counts"] = counts
        return data


@router.post("/system/outbox/{jid}/retry")
def outbox_retry(jid: int, p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        j = s.get(DiscordOutbox, jid)
        if j is None:
            raise NotFound("Auftrag nicht gefunden.")
        if j.status not in ("failed", "pending"):
            raise Conflict("Nur fehlgeschlagene oder wartende Aufträge können erneut ausgeführt werden.")
        j.status, j.attempts, j.next_attempt_at, j.last_error = "pending", 0, utcnow(), None
        audit(s, p.actor(), "system.outbox_retry", "outbox", j.id, {"kind": j.kind})
        emit(s, "outbox", j.id)
    return {"ok": True}


@router.post("/system/outbox/{jid}/cancel")
def outbox_cancel(jid: int, p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        j = s.get(DiscordOutbox, jid)
        if j is None:
            raise NotFound("Auftrag nicht gefunden.")
        if j.status != "pending":
            raise Conflict("Nur wartende Aufträge können abgebrochen werden.")
        j.status, j.last_error, j.processed_at = "failed", f"Abgebrochen durch {p.name}", utcnow()
        audit(s, p.actor(), "system.outbox_cancel", "outbox", j.id, {"kind": j.kind})
        emit(s, "outbox", j.id)
    return {"ok": True}


@router.get("/system/errors")
def errors(page: int = 1, page_size: int = 50, p: Principal = Depends(require("settings.manage"))) -> dict:
    with session_scope() as s:
        return paginate(s, select(ErrorLog).order_by(ErrorLog.id.desc()), page, page_size, lambda e: {
            "id": e.id, "component": e.component, "message": e.message, "context": e.context,
            "created_at": e.created_at.isoformat()})


@router.get("/system/backups")
def backups(p: Principal = Depends(require("settings.manage"))) -> list[dict]:
    from ..tools.backup import list_backups
    return list_backups()
