"""Gemeinsame Bausteine der Fachlogik: Fehler, Akteur, Audit, Änderungsereignisse, Outbox."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.models import (
    AuditLog,
    ChangeEvent,
    DiscordOutbox,
    DiscordUser,
    ErrorLog,
    Notification,
    ProcessedEvent,
    utcnow,
)

log = logging.getLogger("lbc.services")


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class Forbidden(ServiceError):
    status_code = 403


class Invalid(ServiceError):
    status_code = 422


@dataclass
class Actor:
    """Wer eine Aktion ausführt (Teammitglied im Panel, Discord-Nutzer oder das System)."""

    name: str
    discord_id: Optional[str] = None
    member_id: Optional[int] = None
    source: str = "panel"  # panel | discord | system
    ip: Optional[str] = None

    @classmethod
    def system(cls, name: str = "System") -> "Actor":
        return cls(name=name, source="system")


def get_or_404(session: Session, model, obj_id: Any, label: str = "Datensatz"):
    obj = session.get(model, obj_id)
    if obj is None:
        raise NotFound(f"{label} wurde nicht gefunden.")
    return obj


# ---------------------------------------------------------------------------
def emit(session: Session, topic: str, object_id: Any = None) -> None:
    """Änderungsereignis für Live-Aktualisierung des Webpanels (WebSocket)."""
    session.add(ChangeEvent(topic=topic, object_id=str(object_id) if object_id is not None else None))


def audit(
    session: Session,
    actor: Actor,
    action: str,
    object_type: Optional[str] = None,
    object_id: Any = None,
    details: Optional[dict] = None,
) -> None:
    session.add(
        AuditLog(
            actor_discord_id=actor.discord_id,
            actor_name=actor.name[:100],
            action=action,
            object_type=object_type,
            object_id=str(object_id) if object_id is not None else None,
            details=details or None,
            source=actor.source if actor.source in ("panel", "discord", "system") else "system",
            ip_address=actor.ip,
        )
    )
    emit(session, "audit")


def notify(
    session: Session,
    kind: str,
    title: str,
    body: Optional[str] = None,
    link: Optional[str] = None,
    permission: Optional[str] = None,
    member_id: Optional[int] = None,
) -> None:
    session.add(Notification(kind=kind, title=title[:200], body=body, link=link, permission=permission,
                             member_id=member_id))
    emit(session, "notifications")


def record_error(session: Session, component: str, message: str, context: Optional[str] = None) -> None:
    session.add(ErrorLog(component=component, message=message[:5000], context=(context or "")[:200]))


# ---------------------------------------------------------------------------
# Outbox (Webpanel → Discord)
# ---------------------------------------------------------------------------
def enqueue(
    session: Session,
    kind: str,
    payload: dict,
    idempotency_key: Optional[str] = None,
    max_attempts: int = 8,
    delay_seconds: int = 0,
) -> Optional[DiscordOutbox]:
    """Legt einen Discord-Auftrag an. Ein vorhandener Auftrag mit gleichem Schlüssel wird nicht dupliziert."""
    if idempotency_key:
        existing = session.scalar(select(DiscordOutbox).where(DiscordOutbox.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.status in ("done", "failed"):
                # Erneut ausführen (z. B. weitere Aktualisierung derselben Nachricht)
                existing.payload = payload
                existing.status = "pending"
                existing.attempts = 0
                existing.next_attempt_at = utcnow() + timedelta(seconds=delay_seconds)
                existing.last_error = None
            else:
                existing.payload = payload
            return existing
    job = DiscordOutbox(
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        next_attempt_at=utcnow() + timedelta(seconds=delay_seconds),
    )
    session.add(job)
    session.flush()
    emit(session, "outbox")
    return job


def claim_outbox_jobs(session: Session, worker_id: str, limit: int = 10, lock_timeout_s: int = 300) -> list[DiscordOutbox]:
    """Reserviert fällige Aufträge atomar für einen Worker (FOR UPDATE SKIP LOCKED-ähnlich über UPDATE)."""
    now = utcnow()
    stale = now - timedelta(seconds=lock_timeout_s)
    # Hängengebliebene Aufträge (Bot-Absturz während der Verarbeitung) wieder freigeben
    session.execute(
        update(DiscordOutbox)
        .where(DiscordOutbox.status == "processing", DiscordOutbox.locked_at < stale)
        .values(status="pending", locked_by=None, locked_at=None)
    )
    ids = session.scalars(
        select(DiscordOutbox.id)
        .where(DiscordOutbox.status == "pending", DiscordOutbox.next_attempt_at <= now)
        .order_by(DiscordOutbox.id)
        .limit(limit)
    ).all()
    claimed: list[DiscordOutbox] = []
    for job_id in ids:
        res = session.execute(
            update(DiscordOutbox)
            .where(DiscordOutbox.id == job_id, DiscordOutbox.status == "pending")
            .values(status="processing", locked_by=worker_id, locked_at=now, attempts=DiscordOutbox.attempts + 1)
        )
        if res.rowcount == 1:
            claimed.append(job_id)
    session.commit()
    if not claimed:
        return []
    return list(session.scalars(select(DiscordOutbox).where(DiscordOutbox.id.in_(claimed)).order_by(DiscordOutbox.id)))


def finish_outbox_job(session: Session, job_id: int, error: Optional[str] = None, permanent: bool = False) -> str:
    job = session.get(DiscordOutbox, job_id)
    if job is None:
        return "missing"
    job.locked_by = None
    job.locked_at = None
    if error is None:
        job.status = "done"
        job.last_error = None
        job.processed_at = utcnow()
    elif permanent or job.attempts >= job.max_attempts:
        job.status = "failed"
        job.last_error = error[:4000]
        job.processed_at = utcnow()
        record_error(session, "bot", f"Discord-Auftrag {job.kind} #{job.id} endgültig fehlgeschlagen: {error}")
    else:
        job.status = "pending"
        job.last_error = error[:4000]
        backoff = min(15 * (2 ** (job.attempts - 1)), 3600)
        job.next_attempt_at = utcnow() + timedelta(seconds=backoff)
    emit(session, "outbox", job.id)
    return job.status


# ---------------------------------------------------------------------------
def mark_event_processed(session: Session, event_key: str) -> bool:
    """True, wenn das Ereignis neu ist; False bei Doppelzustellung. Nutzt einen eindeutigen Index."""
    try:
        with session.begin_nested():
            session.add(ProcessedEvent(event_key=event_key[:120]))
            session.flush()
        return True
    except IntegrityError:
        return False


def upsert_discord_user(
    session: Session,
    discord_id: str,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> DiscordUser:
    discord_id = str(discord_id)
    user = session.scalar(select(DiscordUser).where(DiscordUser.discord_id == discord_id))
    if user is None:
        user = DiscordUser(discord_id=discord_id, username=username or discord_id)
        try:
            with session.begin_nested():
                session.add(user)
                session.flush()
        except IntegrityError:
            user = session.scalar(select(DiscordUser).where(DiscordUser.discord_id == discord_id))
            assert user is not None
    if username:
        user.username = username[:100]
    if display_name is not None:
        user.display_name = display_name[:100] if display_name else None
    if avatar_url is not None:
        user.avatar_url = avatar_url[:300] if avatar_url else None
    return user


def user_label(user: Optional[DiscordUser]) -> str:
    if user is None:
        return "Unbekannt"
    return user.display_name or user.username or user.discord_id
