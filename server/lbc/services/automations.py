"""Zeitgesteuerte Automatisierungen (laufen im Backend-Prozess).

Mehrfachausführung bei mehreren Prozessen wird über eine MySQL-Named-Lock (GET_LOCK) verhindert.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Callable

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from ..core.db import get_engine, session_scope
from ..core.models import (
    AuditLog,
    Automation,
    ChangeEvent,
    DiscordOutbox,
    ErrorLog,
    ModerationAction,
    ProcessedEvent,
    Ticket,
    TicketMessage,
    WebSession,
    utcnow,
)
from . import applications, community, reports, settings_service, tickets
from .base import Actor, Invalid, NotFound, audit, emit, record_error

log = logging.getLogger("lbc.automations")


def retention_cleanup(session: Session) -> dict:
    now = utcnow()
    anonymized = applications.anonymize_expired(session)
    ticket_days = int(settings_service.get_value(session, "retention.closed_ticket_days") or 365)
    audit_days = int(settings_service.get_value(session, "retention.audit_days") or 730)
    outbox_days = int(settings_service.get_value(session, "retention.outbox_days") or 14)
    old_ticket_ids = select(Ticket.id).where(Ticket.status == "closed", Ticket.closed_at < now - timedelta(days=ticket_days))
    msgs = session.execute(delete(TicketMessage).where(TicketMessage.ticket_id.in_(old_ticket_ids))).rowcount
    audits = session.execute(delete(AuditLog).where(AuditLog.created_at < now - timedelta(days=audit_days))).rowcount
    outbox = session.execute(delete(DiscordOutbox).where(DiscordOutbox.status == "done",
                                                         DiscordOutbox.processed_at < now - timedelta(days=outbox_days))).rowcount
    session.execute(delete(ProcessedEvent).where(ProcessedEvent.created_at < now - timedelta(days=30)))
    session.execute(delete(ChangeEvent).where(ChangeEvent.created_at < now - timedelta(days=2)))
    session.execute(delete(ErrorLog).where(ErrorLog.created_at < now - timedelta(days=90)))
    result = {"applications_anonymized": anonymized, "ticket_messages_deleted": msgs, "audit_logs_deleted": audits,
              "outbox_deleted": outbox}
    if any(result.values()):
        audit(session, Actor.system("Datenschutz"), "system.retention_cleanup", None, None, result)
    return result


def weekly_report(session: Session) -> dict:
    if not reports.weekly_due(session):
        return {"skipped": "nicht fällig"}
    channel = settings_service.get_value(session, "channels.reports")
    r = reports.generate(session, Actor.system("Report-Automatik"), post=bool(channel))
    return {"report_id": r.id, "posted": bool(channel)}


def timeout_expiry(session: Session) -> dict:
    n = session.execute(update(ModerationAction).where(
        ModerationAction.action == "timeout", ModerationAction.status == "executed",
        ModerationAction.expires_at < utcnow()).values(status="expired")).rowcount
    return {"expired": n}


def session_cleanup(session: Session) -> dict:
    n = session.execute(delete(WebSession).where(WebSession.expires_at < utcnow())).rowcount
    return {"deleted": n}


JOBS: dict[str, Callable[[Session], dict]] = {
    "ticket_auto_close": tickets.auto_close_run,
    "weekly_report": weekly_report,
    "retention_cleanup": retention_cleanup,
    "poll_close": lambda s: {"closed": community.close_expired_polls(s)},
    "timeout_expiry": timeout_expiry,
    "session_cleanup": session_cleanup,
}


def run_job(key: str, force: bool = False) -> dict:
    """Führt eine Automatisierung in eigener Transaktion aus und protokolliert das Ergebnis."""
    fn = JOBS.get(key)
    if fn is None:
        raise NotFound("Unbekannte Automatisierung.")
    with session_scope() as session:
        auto = session.scalar(select(Automation).where(Automation.key == key))
        if auto is None:
            raise NotFound("Automatisierung nicht gefunden.")
        if not auto.enabled and not force:
            raise Invalid("Automatisierung ist deaktiviert.")
    try:
        with session_scope() as session:
            result = fn(session)
        status, message = "ok", str(result)[:500]
    except Exception as exc:  # noqa: BLE001
        log.exception("Automatisierung %s fehlgeschlagen", key)
        result, status, message = {"error": str(exc)}, "error", str(exc)[:500]
        with session_scope() as session:
            record_error(session, "scheduler", f"Automatisierung {key}: {exc}")
    with session_scope() as session:
        auto = session.scalar(select(Automation).where(Automation.key == key))
        now = utcnow()
        auto.last_run_at = now
        auto.next_run_at = now + timedelta(minutes=auto.interval_minutes)
        auto.last_status = status
        auto.last_message = message
        emit(session, "automations")
    return result


def update_automation(session: Session, actor: Actor, key: str, data: dict) -> Automation:
    auto = session.scalar(select(Automation).where(Automation.key == key))
    if auto is None:
        raise NotFound("Automatisierung nicht gefunden.")
    changes = {}
    if "enabled" in data and bool(data["enabled"]) != auto.enabled:
        changes["enabled"] = [auto.enabled, bool(data["enabled"])]
        auto.enabled = bool(data["enabled"])
    if "interval_minutes" in data:
        iv = int(data["interval_minutes"])
        if not 1 <= iv <= 10080:
            raise Invalid("Intervall zwischen 1 Minute und 7 Tagen.")
        if iv != auto.interval_minutes:
            changes["interval_minutes"] = [auto.interval_minutes, iv]
            auto.interval_minutes = iv
            auto.next_run_at = utcnow() + timedelta(minutes=iv)
    if changes:
        audit(session, actor, "system.automation_changed", "automation", key, changes)
        emit(session, "automations")
    return auto


def serialize(a: Automation) -> dict:
    return {"key": a.key, "name": a.name, "description": a.description, "enabled": a.enabled,
            "interval_minutes": a.interval_minutes, "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
            "next_run_at": a.next_run_at.isoformat() if a.next_run_at else None, "last_status": a.last_status,
            "last_message": a.last_message}


def _due_jobs() -> list[str]:
    with session_scope() as session:
        now = utcnow()
        return [a.key for a in session.scalars(select(Automation).where(Automation.enabled.is_(True)))
                if a.key in JOBS and (a.next_run_at is None or a.next_run_at <= now)]


def _try_lock() -> object | None:
    conn = get_engine().connect()
    got = conn.execute(text("SELECT GET_LOCK('lbc_scheduler', 0)")).scalar()
    if got == 1:
        return conn
    conn.close()
    return None


async def scheduler_loop(stop: asyncio.Event, tick_seconds: int = 30) -> None:
    log.info("Scheduler gestartet")
    lock_conn = None
    while not stop.is_set():
        try:
            if lock_conn is None:
                lock_conn = await asyncio.to_thread(_try_lock)
            if lock_conn is not None:
                # Lock-Verbindung am Leben halten
                await asyncio.to_thread(lambda: lock_conn.execute(text("SELECT 1")))
                for key in await asyncio.to_thread(_due_jobs):
                    await asyncio.to_thread(run_job, key)
        except Exception:  # noqa: BLE001
            log.exception("Scheduler-Fehler")
            if lock_conn is not None:
                try:
                    lock_conn.close()
                except Exception:  # noqa: BLE001
                    pass
                lock_conn = None
        try:
            await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
        except asyncio.TimeoutError:
            pass
    if lock_conn is not None:
        lock_conn.close()
