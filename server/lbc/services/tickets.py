"""Ticket-System: Erstellung, Zuweisung, Status, interne Notizen, Transkripte, Auto-Schließung."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ..core.models import (
    TeamMember,
    Ticket,
    TicketCategory,
    TicketMessage,
    TicketNote,
    TicketTranscript,
    utcnow,
)
from . import settings_service
from .base import Actor, Conflict, Forbidden, Invalid, NotFound, audit, emit, enqueue, notify, upsert_discord_user

STATUSES = ("open", "in_progress", "waiting", "closed")
PRIORITIES = ("low", "normal", "high", "urgent")
STATUS_LABELS = {"open": "Offen", "in_progress": "In Bearbeitung", "waiting": "Wartend", "closed": "Geschlossen"}
PRIORITY_LABELS = {"low": "Niedrig", "normal": "Normal", "high": "Hoch", "urgent": "Dringend"}


def next_number(session: Session, name: str) -> int:
    """Atomare fortlaufende Nummer – ein einziges Statement (MySQL-Idiom mit LAST_INSERT_ID)."""
    session.execute(text(
        "INSERT INTO counters (name, value) VALUES (:n, LAST_INSERT_ID(1)) "
        "ON DUPLICATE KEY UPDATE value = LAST_INSERT_ID(value + 1)"), {"n": name})
    return int(session.execute(text("SELECT LAST_INSERT_ID()")).scalar())


def channel_name(ticket: Ticket) -> str:
    return f"ticket-{ticket.number:04d}"


# ---------------------------------------------------------------------------
def list_categories(session: Session, only_active: bool = False) -> list[TicketCategory]:
    q = select(TicketCategory).order_by(TicketCategory.sort_order, TicketCategory.id)
    if only_active:
        q = q.where(TicketCategory.active.is_(True))
    return list(session.scalars(q))


def serialize_category(c: TicketCategory) -> dict:
    return {"id": c.id, "name": c.name, "description": c.description, "emoji": c.emoji,
            "discord_category_id": c.discord_category_id, "support_role_id": c.support_role_id,
            "active": c.active, "sort_order": c.sort_order}


def save_category(session: Session, actor: Actor, data: dict, category_id: Optional[int] = None) -> TicketCategory:
    name = (data.get("name") or "").strip()
    if not name:
        raise Invalid("Name der Kategorie fehlt.")
    dup = session.scalar(select(TicketCategory).where(TicketCategory.name == name, TicketCategory.id != (category_id or 0)))
    if dup:
        raise Conflict("Eine Kategorie mit diesem Namen existiert bereits.")
    for f in ("discord_category_id", "support_role_id"):
        v = data.get(f)
        if v and not (str(v).isdigit() and 15 <= len(str(v)) <= 21):
            raise Invalid("Discord-IDs bestehen aus 15–21 Ziffern.")
    if category_id:
        cat = session.get(TicketCategory, category_id)
        if cat is None:
            raise NotFound("Kategorie nicht gefunden.")
        action = "tickets.category_updated"
    else:
        cat = TicketCategory(name=name)
        session.add(cat)
        action = "tickets.category_created"
    cat.name = name[:80]
    cat.description = (data.get("description") or None)
    cat.emoji = data.get("emoji") or None
    cat.discord_category_id = data.get("discord_category_id") or None
    cat.support_role_id = data.get("support_role_id") or None
    cat.active = bool(data.get("active", True))
    cat.sort_order = int(data.get("sort_order") or 100)
    session.flush()
    audit(session, actor, action, "ticket_category", cat.id, {"name": cat.name, "active": cat.active})
    emit(session, "ticket_categories")
    enqueue(session, "ticket.refresh_panel", {}, idempotency_key="ticket-panel-refresh")
    return cat


def delete_category(session: Session, actor: Actor, category_id: int) -> None:
    cat = session.get(TicketCategory, category_id)
    if cat is None:
        raise NotFound("Kategorie nicht gefunden.")
    used = session.scalar(select(func.count(Ticket.id)).where(Ticket.category_id == cat.id))
    if used:
        cat.active = False
        audit(session, actor, "tickets.category_deactivated", "ticket_category", cat.id, {"name": cat.name})
    else:
        audit(session, actor, "tickets.category_deleted", "ticket_category", cat.id, {"name": cat.name})
        session.delete(cat)
    emit(session, "ticket_categories")
    enqueue(session, "ticket.refresh_panel", {}, idempotency_key="ticket-panel-refresh")


# ---------------------------------------------------------------------------
def create_ticket(
    session: Session,
    creator_discord_id: str,
    creator_name: str,
    category_id: Optional[int],
    subject: str,
    avatar_url: Optional[str] = None,
) -> Ticket:
    user = upsert_discord_user(session, creator_discord_id, username=creator_name, avatar_url=avatar_url)
    session.flush()
    max_open = int(settings_service.get_value(session, "tickets.max_open_per_user") or 0)
    if max_open:
        open_count = session.scalar(
            select(func.count(Ticket.id)).where(Ticket.creator_id == user.id, Ticket.status != "closed")
        )
        if open_count >= max_open:
            raise Conflict(f"Du hast bereits {open_count} offene Tickets. Bitte schließe zuerst ein bestehendes Ticket.")
    category = session.get(TicketCategory, category_id) if category_id else None
    if category_id and (category is None or not category.active):
        raise Invalid("Diese Ticketkategorie ist nicht verfügbar.")
    ticket = Ticket(
        number=next_number(session, "ticket"),
        category_id=category.id if category else None,
        creator_id=user.id,
        subject=(subject or (category.name if category else "Ticket"))[:200],
        status="open",
        priority="normal",
    )
    session.add(ticket)
    session.flush()
    actor = Actor(name=creator_name, discord_id=str(creator_discord_id), source="discord")
    audit(session, actor, "tickets.created", "ticket", ticket.id,
          {"number": ticket.number, "category": category.name if category else None})
    notify(session, "ticket", f"Neues Ticket #{ticket.number:04d}", ticket.subject,
           link=f"/tickets/{ticket.id}", permission="tickets.view")
    emit(session, "tickets", ticket.id)
    return ticket


def set_channel(session: Session, ticket_id: int, channel_id: str) -> None:
    t = session.get(Ticket, ticket_id)
    if t is None:
        raise NotFound("Ticket nicht gefunden.")
    t.channel_id = str(channel_id)
    emit(session, "tickets", t.id)


def mark_creation_failed(session: Session, ticket_id: int, reason: str) -> None:
    t = session.get(Ticket, ticket_id)
    if t is not None:
        t.status = "closed"
        t.closed_at = utcnow()
        t.closed_by = "System"
        t.close_reason = ("Ticket-Channel konnte nicht erstellt werden: " + reason)[:300]
        emit(session, "tickets", t.id)


def get_ticket(session: Session, ticket_id: int) -> Ticket:
    t = session.get(Ticket, ticket_id)
    if t is None:
        raise NotFound("Ticket nicht gefunden.")
    return t


def get_by_channel(session: Session, channel_id: str) -> Optional[Ticket]:
    return session.scalar(select(Ticket).where(Ticket.channel_id == str(channel_id)))


def log_message(
    session: Session,
    channel_id: str,
    discord_message_id: str,
    author_discord_id: str,
    author_name: str,
    content: str,
    attachments: Optional[list[dict]] = None,
    is_staff: bool = False,
) -> Optional[TicketMessage]:
    """Protokolliert eine Discord-Nachricht im Ticket (idempotent über discord_message_id)."""
    ticket = get_by_channel(session, channel_id)
    if ticket is None:
        return None
    existing = session.scalar(select(TicketMessage).where(TicketMessage.discord_message_id == str(discord_message_id)))
    if existing is not None:
        return existing
    now = utcnow()
    msg = TicketMessage(
        ticket_id=ticket.id, discord_message_id=str(discord_message_id), author_discord_id=str(author_discord_id),
        author_name=author_name[:100], content=content or "", attachments=attachments or None, is_staff=is_staff,
        source="discord", created_at=now,
    )
    session.add(msg)
    ticket.last_activity_at = now
    ticket.auto_close_warned_at = None
    is_creator = ticket.creator.discord_id == str(author_discord_id)
    if is_staff and not is_creator and ticket.first_response_at is None:
        ticket.first_response_at = now
    if is_creator and ticket.status == "waiting":
        ticket.status = "in_progress" if ticket.assignee_id else "open"
    emit(session, "ticket_messages", ticket.id)
    return msg


def mark_message_edited(session: Session, discord_message_id: str, content: str) -> None:
    msg = session.scalar(select(TicketMessage).where(TicketMessage.discord_message_id == str(discord_message_id)))
    if msg:
        msg.content = content
        msg.edited_at = utcnow()
        emit(session, "ticket_messages", msg.ticket_id)


def mark_message_deleted(session: Session, discord_message_id: str) -> None:
    msg = session.scalar(select(TicketMessage).where(TicketMessage.discord_message_id == str(discord_message_id)))
    if msg:
        msg.deleted = True
        emit(session, "ticket_messages", msg.ticket_id)


def claim(session: Session, actor: Actor, ticket_id: int, member_id: int) -> Ticket:
    """Atomare Übernahme: nur erfolgreich, wenn das Ticket noch niemandem zugewiesen ist."""
    now = utcnow()
    res = session.execute(
        update(Ticket)
        .where(Ticket.id == ticket_id, Ticket.assignee_id.is_(None), Ticket.status != "closed")
        .values(assignee_id=member_id, claimed_at=now, status="in_progress", last_activity_at=now)
        .execution_options(synchronize_session=False)
    )
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFound("Ticket nicht gefunden.")
    if res.rowcount != 1:
        session.refresh(ticket)
        if ticket.status == "closed":
            raise Conflict("Das Ticket ist bereits geschlossen.")
        if ticket.assignee_id == member_id:
            return ticket
        raise Conflict("Dieses Ticket wurde bereits von einem anderen Teammitglied übernommen.")
    session.refresh(ticket)
    audit(session, actor, "tickets.claimed", "ticket", ticket.id, {"number": ticket.number})
    _sync_channel(session, ticket, f"{actor.name} hat das Ticket übernommen.")
    emit(session, "tickets", ticket.id)
    return ticket


def assign(session: Session, actor: Actor, ticket_id: int, member_id: Optional[int]) -> Ticket:
    ticket = get_ticket(session, ticket_id)
    member = None
    if member_id:
        member = session.get(TeamMember, member_id)
        if member is None or member.status != "active":
            raise Invalid("Teammitglied nicht gefunden oder nicht aktiv.")
    old = ticket.assignee.user.username if ticket.assignee else None
    ticket.assignee_id = member.id if member else None
    ticket.claimed_at = utcnow() if member else None
    if member and ticket.status == "open":
        ticket.status = "in_progress"
    session.flush()
    session.refresh(ticket)
    new = member.user.username if member else None
    audit(session, actor, "tickets.assigned", "ticket", ticket.id, {"number": ticket.number, "from": old, "to": new})
    if member:
        notify(session, "ticket", f"Ticket #{ticket.number:04d} wurde dir zugewiesen", ticket.subject,
               link=f"/tickets/{ticket.id}", member_id=member.id)
    _sync_channel(session, ticket, f"Zuständig: {member.user.display_name or member.user.username}" if member
                  else "Die Zuweisung wurde aufgehoben.")
    emit(session, "tickets", ticket.id)
    return ticket


def set_status(session: Session, actor: Actor, ticket_id: int, status: str) -> Ticket:
    if status not in STATUSES:
        raise Invalid("Ungültiger Ticketstatus.")
    ticket = get_ticket(session, ticket_id)
    if status == "closed":
        return close(session, actor, ticket_id, reason=None)
    if ticket.status == "closed":
        return reopen(session, actor, ticket_id)
    if ticket.status == status:
        return ticket
    old = ticket.status
    ticket.status = status
    ticket.last_activity_at = utcnow()
    audit(session, actor, "tickets.status_changed", "ticket", ticket.id,
          {"number": ticket.number, "from": old, "to": status})
    _sync_channel(session, ticket, f"Status geändert: {STATUS_LABELS[old]} → {STATUS_LABELS[status]}")
    emit(session, "tickets", ticket.id)
    return ticket


def set_priority(session: Session, actor: Actor, ticket_id: int, priority: str) -> Ticket:
    if priority not in PRIORITIES:
        raise Invalid("Ungültige Priorität.")
    ticket = get_ticket(session, ticket_id)
    if ticket.priority != priority:
        audit(session, actor, "tickets.priority_changed", "ticket", ticket.id,
              {"number": ticket.number, "from": ticket.priority, "to": priority})
        ticket.priority = priority
        emit(session, "tickets", ticket.id)
    return ticket


def add_note(session: Session, actor: Actor, ticket_id: int, content: str) -> TicketNote:
    content = (content or "").strip()
    if not content:
        raise Invalid("Die Notiz ist leer.")
    ticket = get_ticket(session, ticket_id)
    note = TicketNote(ticket_id=ticket.id, author_id=actor.member_id, author_name=actor.name, content=content[:5000])
    session.add(note)
    session.flush()
    audit(session, actor, "tickets.note_added", "ticket", ticket.id, {"number": ticket.number, "note_id": note.id})
    emit(session, "ticket_notes", ticket.id)
    return note


def reply_from_panel(session: Session, actor: Actor, ticket_id: int, content: str) -> None:
    content = (content or "").strip()
    if not content:
        raise Invalid("Die Nachricht ist leer.")
    ticket = get_ticket(session, ticket_id)
    if ticket.status == "closed" or not ticket.channel_id:
        raise Conflict("Das Ticket ist geschlossen oder besitzt keinen Discord-Channel.")
    enqueue(session, "ticket.send_message", {
        "ticket_id": ticket.id, "channel_id": ticket.channel_id, "content": content[:1900], "author": actor.name,
        "author_discord_id": actor.discord_id,
    })
    audit(session, actor, "tickets.reply_sent", "ticket", ticket.id, {"number": ticket.number})


def build_transcript(session: Session, ticket: Ticket) -> TicketTranscript:
    msgs = session.scalars(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket.id).order_by(TicketMessage.created_at, TicketMessage.id)
    ).all()
    lines = [
        f"LEITSTELLE BAVARIA – TICKET #{ticket.number:04d}",
        f"Betreff: {ticket.subject}",
        f"Kategorie: {ticket.category.name if ticket.category else '-'}",
        f"Ersteller: {ticket.creator.username} ({ticket.creator.discord_id})",
        f"Erstellt: {ticket.created_at:%d.%m.%Y %H:%M} UTC",
        "-" * 60,
    ]
    for m in msgs:
        line = f"[{m.created_at:%d.%m.%Y %H:%M}] {m.author_name}{' (Team)' if m.is_staff else ''}: {m.content}"
        if m.deleted:
            line += " [gelöscht]"
        if m.attachments:
            line += " | Anhänge: " + ", ".join(a.get("url", "") for a in m.attachments)
        lines.append(line)
    transcript = TicketTranscript(ticket_id=ticket.id, content="\n".join(lines), message_count=len(msgs))
    session.add(transcript)
    session.flush()
    return transcript


def close(session: Session, actor: Actor, ticket_id: int, reason: Optional[str], auto: bool = False) -> Ticket:
    ticket = get_ticket(session, ticket_id)
    if ticket.status == "closed":
        raise Conflict("Das Ticket ist bereits geschlossen.")
    old = ticket.status
    ticket.status = "closed"
    ticket.closed_at = utcnow()
    ticket.closed_by = actor.name[:100]
    ticket.close_reason = (reason or ("Automatisch wegen Inaktivität geschlossen" if auto else None))
    transcript = build_transcript(session, ticket)
    audit(session, actor, "tickets.auto_closed" if auto else "tickets.closed", "ticket", ticket.id,
          {"number": ticket.number, "from": old, "reason": ticket.close_reason})
    if ticket.channel_id:
        enqueue(session, "ticket.close_channel", {
            "ticket_id": ticket.id, "channel_id": ticket.channel_id, "transcript_id": transcript.id,
            "closed_by": actor.name, "reason": ticket.close_reason,
            "creator_discord_id": ticket.creator.discord_id, "number": ticket.number,
        })
    emit(session, "tickets", ticket.id)
    return ticket


def reopen(session: Session, actor: Actor, ticket_id: int) -> Ticket:
    ticket = get_ticket(session, ticket_id)
    if ticket.status != "closed":
        raise Conflict("Das Ticket ist nicht geschlossen.")
    ticket.status = "in_progress" if ticket.assignee_id else "open"
    ticket.closed_at = None
    ticket.closed_by = None
    ticket.close_reason = None
    ticket.reopen_count += 1
    ticket.last_activity_at = utcnow()
    ticket.auto_close_warned_at = None
    audit(session, actor, "tickets.reopened", "ticket", ticket.id, {"number": ticket.number})
    enqueue(session, "ticket.reopen_channel", {
        "ticket_id": ticket.id, "channel_id": ticket.channel_id, "creator_discord_id": ticket.creator.discord_id,
        "number": ticket.number, "reopened_by": actor.name,
    })
    emit(session, "tickets", ticket.id)
    return ticket


def _sync_channel(session: Session, ticket: Ticket, text: str) -> None:
    if ticket.channel_id and ticket.status != "closed":
        enqueue(session, "ticket.system_message", {"channel_id": ticket.channel_id, "ticket_id": ticket.id, "content": text})


def ensure_staff_can_close(member: Optional[TeamMember], is_creator: bool) -> None:
    if member is None and not is_creator:
        raise Forbidden("Nur der Ersteller oder das Team können dieses Ticket schließen.")


# ---------------------------------------------------------------------------
def auto_close_run(session: Session) -> dict:
    """Vorwarnung und automatische Schließung inaktiver Tickets."""
    if not settings_service.get_value(session, "tickets.auto_close_enabled"):
        return {"warned": 0, "closed": 0, "skipped": "deaktiviert"}
    hours = int(settings_service.get_value(session, "tickets.auto_close_hours") or 72)
    warn_hours = int(settings_service.get_value(session, "tickets.auto_close_warning_hours") or 0)
    now = utcnow()
    close_before = now - timedelta(hours=hours)
    warned = closed = 0
    candidates = session.scalars(
        select(Ticket).where(Ticket.status.in_(("open", "in_progress", "waiting")))
        .where(Ticket.last_activity_at < now - timedelta(hours=max(hours - warn_hours, 1)))
    ).all()
    for t in candidates:
        if t.last_activity_at <= close_before and (warn_hours == 0 or (
                t.auto_close_warned_at and t.auto_close_warned_at <= now - timedelta(hours=warn_hours))):
            close(session, Actor.system("Automatische Schließung"), t.id, None, auto=True)
            closed += 1
        elif warn_hours and t.auto_close_warned_at is None:
            t.auto_close_warned_at = now
            if t.channel_id:
                enqueue(session, "ticket.system_message", {
                    "channel_id": t.channel_id, "ticket_id": t.id, "mention_creator": t.creator.discord_id,
                    "content": f"Dieses Ticket ist seit längerer Zeit inaktiv und wird in ca. {warn_hours} Stunden "
                               "automatisch geschlossen, sofern keine neue Nachricht eingeht.",
                }, idempotency_key=f"ticket-autoclose-warn-{t.id}-{t.reopen_count}")
            audit(session, Actor.system("Automatische Schließung"), "tickets.auto_close_warned", "ticket", t.id,
                  {"number": t.number})
            warned += 1
    return {"warned": warned, "closed": closed}


# ---------------------------------------------------------------------------
def serialize(t: Ticket, detail: bool = False) -> dict:
    data = {
        "id": t.id, "number": t.number, "subject": t.subject, "status": t.status, "priority": t.priority,
        "category": {"id": t.category.id, "name": t.category.name} if t.category else None,
        "creator": {"discord_id": t.creator.discord_id, "username": t.creator.username,
                    "display_name": t.creator.display_name, "avatar_url": t.creator.avatar_url},
        "assignee": {"id": t.assignee.id, "username": t.assignee.user.username,
                     "display_name": t.assignee.user.display_name} if t.assignee else None,
        "channel_id": t.channel_id, "created_at": t.created_at.isoformat(),
        "last_activity_at": t.last_activity_at.isoformat(),
        "first_response_at": t.first_response_at.isoformat() if t.first_response_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None, "closed_by": t.closed_by,
        "close_reason": t.close_reason, "reopen_count": t.reopen_count,
        "auto_close_warned_at": t.auto_close_warned_at.isoformat() if t.auto_close_warned_at else None,
    }
    return data


def serialize_message(m: TicketMessage) -> dict:
    return {"id": m.id, "author_discord_id": m.author_discord_id, "author_name": m.author_name, "is_staff": m.is_staff,
            "content": m.content, "attachments": m.attachments or [], "source": m.source,
            "created_at": m.created_at.isoformat(), "edited_at": m.edited_at.isoformat() if m.edited_at else None,
            "deleted": m.deleted}


def serialize_note(n: TicketNote) -> dict:
    return {"id": n.id, "author_name": n.author_name, "content": n.content, "created_at": n.created_at.isoformat()}
