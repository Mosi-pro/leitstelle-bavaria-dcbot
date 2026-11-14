"""Bug-Management: Erfassung aus dem Discord-Forum, atomare Übernahme, Bearbeitung, Synchronisierung."""
from __future__ import annotations

import enum
import re
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.models import Bug, BugAssignment, BugAttachment, BugComment, GameVersion, utcnow
from . import settings_service
from .base import Actor, Conflict, Invalid, NotFound, audit, emit, enqueue, notify, upsert_discord_user
from .team import find_member_by_discord_id

STATUSES = ("new", "claimed", "in_progress", "fixed", "not_reproducible")
STATUS_LABELS = {
    "new": "Neu", "claimed": "Übernommen", "in_progress": "In Bearbeitung",
    "fixed": "Behoben", "not_reproducible": "Nicht reproduzierbar",
}
PRIORITIES = ("low", "normal", "high", "critical")
CLOSED_STATUSES = ("fixed", "not_reproducible")
ALREADY_CLAIMED_MSG = "Dieser Bug wurde bereits von einem anderen Entwickler übernommen."

VERSION_RE = re.compile(r"(?:spiel)?version\s*[:#\-]?\s*v?(\d+\.\d+(?:\.\d+)?(?:[-\w.]*)?)", re.IGNORECASE)
LOOSE_VERSION_RE = re.compile(r"^v?(\d+\.\d+(?:\.\d+)?)$", re.IGNORECASE)
REPRO_RE = re.compile(
    r"(?:reproduktion(?:sschritte)?|schritte zur reproduktion|steps to reproduce|so tritt der fehler auf)\s*[:\-]?\s*\n?(.+)",
    re.IGNORECASE | re.DOTALL,
)


class ClaimResult(enum.Enum):
    CLAIMED = "claimed"
    ALREADY_YOURS = "already_yours"
    TAKEN = "taken"
    CLOSED = "closed"
    NOT_FOUND = "not_found"


def fmt_id(bug_id: int) -> str:
    return f"#{bug_id:04d}"


def parse_post(content: str, tag_names: Optional[list[str]] = None) -> dict:
    """Extrahiert Spielversion und Reproduktionsschritte aus einem Forum-Beitrag."""
    content = content or ""
    version = None
    m = VERSION_RE.search(content)
    if m:
        version = m.group(1).rstrip(".,;")
    if not version:
        for tag in tag_names or []:
            tm = LOOSE_VERSION_RE.match(tag.strip())
            if tm:
                version = tm.group(1)
                break
    repro = None
    rm = REPRO_RE.search(content)
    if rm:
        repro = rm.group(1).strip()[:4000] or None
    return {"version": version[:30] if version else None, "reproduction_steps": repro}


def _resolve_version(session: Session, version_text: Optional[str]) -> Optional[GameVersion]:
    if not version_text:
        return None
    return session.scalar(select(GameVersion).where(GameVersion.version == version_text))


def create_from_forum(
    session: Session,
    *,
    thread_id: str,
    forum_channel_id: str,
    title: str,
    content: str,
    author_discord_id: str,
    author_name: str,
    author_avatar: Optional[str] = None,
    attachments: Optional[list[dict]] = None,
    tag_names: Optional[list[str]] = None,
) -> tuple[Bug, bool]:
    """Legt einen Bug aus einem Forum-Thread an. Rückgabe (bug, neu_angelegt). Doppelerfassung ausgeschlossen."""
    existing = session.scalar(select(Bug).where(Bug.forum_thread_id == str(thread_id)))
    if existing is not None:
        return existing, False
    reporter = upsert_discord_user(session, author_discord_id, username=author_name, avatar_url=author_avatar)
    parsed = parse_post(content, tag_names)
    version = _resolve_version(session, parsed["version"])
    bug = Bug(
        title=(title or "Ohne Titel")[:200], description=(content or "")[:20000],
        reproduction_steps=parsed["reproduction_steps"], reporter_id=reporter.id,
        reporter_discord_id=str(author_discord_id), reporter_name=author_name[:100],
        forum_channel_id=str(forum_channel_id), forum_thread_id=str(thread_id),
        version_id=version.id if version else None, version_text=parsed["version"], status="new", source="discord",
    )
    try:
        with session.begin_nested():
            session.add(bug)
            session.flush()
    except IntegrityError:
        # Paralleles Duplikat-Ereignis hat den Datensatz bereits angelegt
        existing = session.scalar(select(Bug).where(Bug.forum_thread_id == str(thread_id)))
        if existing is None:
            raise
        return existing, False
    for a in attachments or []:
        session.add(BugAttachment(bug_id=bug.id, filename=str(a.get("filename", "datei"))[:255],
                                  url=str(a.get("url", ""))[:700], content_type=a.get("content_type"),
                                  size=a.get("size")))
    actor = Actor(name=author_name, discord_id=str(author_discord_id), source="discord")
    audit(session, actor, "bugs.reported", "bug", bug.id, {"title": bug.title, "thread_id": str(thread_id)})
    notify(session, "bug", f"Neuer Bug {fmt_id(bug.id)}", bug.title, link=f"/bugs/{bug.id}", permission="bugs.view")
    enqueue(session, "bug.notify_developers", {"bug_id": bug.id}, idempotency_key=f"bug-notify-{bug.id}")
    emit(session, "bugs", bug.id)
    return bug, True


def create_manual(session: Session, actor: Actor, data: dict) -> Bug:
    title = (data.get("title") or "").strip()
    if not title:
        raise Invalid("Titel fehlt.")
    version = session.get(GameVersion, data["version_id"]) if data.get("version_id") else None
    bug = Bug(
        title=title[:200], description=(data.get("description") or "")[:20000],
        reproduction_steps=data.get("reproduction_steps") or None, reporter_discord_id=actor.discord_id,
        reporter_name=actor.name, version_id=version.id if version else None,
        version_text=version.version if version else None, priority=data.get("priority") or "normal",
        status="new", source="panel",
    )
    if bug.priority not in PRIORITIES:
        raise Invalid("Ungültige Priorität.")
    session.add(bug)
    session.flush()
    audit(session, actor, "bugs.created", "bug", bug.id, {"title": bug.title})
    if data.get("notify_developers", True):
        enqueue(session, "bug.notify_developers", {"bug_id": bug.id}, idempotency_key=f"bug-notify-{bug.id}")
    emit(session, "bugs", bug.id)
    return bug


def get_bug(session: Session, bug_id: int) -> Bug:
    bug = session.get(Bug, bug_id)
    if bug is None:
        raise NotFound("Bug nicht gefunden.")
    return bug


def set_dev_message(session: Session, bug_id: int, channel_id: str, message_id: str) -> None:
    bug = session.get(Bug, bug_id)
    if bug is not None:
        bug.dev_channel_id = str(channel_id)
        bug.dev_message_id = str(message_id)


def _queue_sync(session: Session, bug: Bug) -> None:
    bug.revision += 1
    if bug.dev_message_id:
        enqueue(session, "bug.sync_message", {"bug_id": bug.id}, idempotency_key=f"bug-sync-{bug.id}")
    emit(session, "bugs", bug.id)


def claim(session: Session, bug_id: int, developer_discord_id: str, developer_name: str, source: str = "discord",
          ip: Optional[str] = None) -> tuple[ClaimResult, Optional[Bug]]:
    """Atomare Übernahme. Nur EIN UPDATE kann die Bedingung 'assignee IS NULL' erfüllen (InnoDB-Zeilensperre).

    Die Berechtigungsprüfung (Developer-Rolle bzw. bugs.claim) erfolgt vorher durch den Aufrufer.
    """
    now = utcnow()
    res = session.execute(
        update(Bug)
        .where(Bug.id == bug_id, Bug.assignee_discord_id.is_(None), Bug.status == "new")
        .values(assignee_discord_id=str(developer_discord_id), assignee_name=developer_name[:100],
                claimed_at=now, status="claimed", updated_at=now)
        .execution_options(synchronize_session=False)
    )
    bug = session.get(Bug, bug_id)
    if bug is None:
        return ClaimResult.NOT_FOUND, None
    session.refresh(bug)
    if res.rowcount != 1:
        if bug.assignee_discord_id == str(developer_discord_id):
            return ClaimResult.ALREADY_YOURS, bug
        if bug.status in CLOSED_STATUSES and not bug.assignee_discord_id:
            return ClaimResult.CLOSED, bug
        return ClaimResult.TAKEN, bug
    session.add(BugAssignment(bug_id=bug.id, developer_discord_id=str(developer_discord_id),
                              developer_name=developer_name[:100], assigned_by=developer_name[:100], kind="claim"))
    actor = Actor(name=developer_name, discord_id=str(developer_discord_id), source=source, ip=ip)
    audit(session, actor, "bugs.claimed", "bug", bug.id, {"title": bug.title})
    _queue_sync(session, bug)
    return ClaimResult.CLAIMED, bug


def _close_assignment(session: Session, bug: Bug) -> None:
    current = session.scalar(
        select(BugAssignment).where(BugAssignment.bug_id == bug.id, BugAssignment.ended_at.is_(None))
        .order_by(BugAssignment.id.desc())
    )
    if current:
        current.ended_at = utcnow()


def handover(session: Session, actor: Actor, bug_id: int, to_discord_id: str, to_name: str,
             note: Optional[str] = None) -> Bug:
    bug = get_bug(session, bug_id)
    if not str(to_discord_id).isdigit():
        raise Invalid("Ungültige Discord-ID des Entwicklers.")
    if bug.assignee_discord_id == str(to_discord_id):
        raise Conflict("Dieser Entwickler ist bereits zuständig.")
    old = bug.assignee_name
    _close_assignment(session, bug)
    bug.assignee_discord_id = str(to_discord_id)
    bug.assignee_name = to_name[:100]
    bug.claimed_at = utcnow()
    if bug.status == "new":
        bug.status = "claimed"
    session.add(BugAssignment(bug_id=bug.id, developer_discord_id=str(to_discord_id), developer_name=to_name[:100],
                              assigned_by=actor.name[:100], kind="handover" if old else "assign", note=note))
    audit(session, actor, "bugs.developer_changed", "bug", bug.id, {"from": old, "to": to_name, "note": note})
    member = find_member_by_discord_id(session, str(to_discord_id))
    if member:
        notify(session, "bug", f"Bug {fmt_id(bug.id)} wurde dir übergeben", bug.title, link=f"/bugs/{bug.id}",
               member_id=member.id)
    enqueue(session, "bug.dm_handover", {"bug_id": bug.id, "to_discord_id": str(to_discord_id), "by": actor.name,
                                         "note": note})
    _queue_sync(session, bug)
    return bug


def unassign(session: Session, actor: Actor, bug_id: int) -> Bug:
    bug = get_bug(session, bug_id)
    if not bug.assignee_discord_id:
        raise Conflict("Dem Bug ist kein Entwickler zugewiesen.")
    old = bug.assignee_name
    _close_assignment(session, bug)
    bug.assignee_discord_id = None
    bug.assignee_name = None
    bug.claimed_at = None
    if bug.status in ("claimed", "in_progress"):
        bug.status = "new"
    audit(session, actor, "bugs.developer_removed", "bug", bug.id, {"from": old})
    _queue_sync(session, bug)
    return bug


def set_status(session: Session, actor: Actor, bug_id: int, status: str, comment: Optional[str] = None) -> Bug:
    if status not in STATUSES:
        raise Invalid("Ungültiger Bugstatus.")
    bug = get_bug(session, bug_id)
    if bug.status == status:
        return bug
    if status in ("claimed", "in_progress") and not bug.assignee_discord_id:
        raise Conflict("Für diesen Status muss zuerst ein Entwickler zuständig sein.")
    old = bug.status
    bug.status = status
    if status in CLOSED_STATUSES:
        bug.resolved_at = utcnow()
        if bug.assignee_discord_id:
            _close_assignment(session, bug)
    elif old in CLOSED_STATUSES:
        bug.resolved_at = None
    if status == "new":
        bug.assignee_discord_id = None
        bug.assignee_name = None
        bug.claimed_at = None
    audit(session, actor, "bugs.status_changed", "bug", bug.id,
          {"from": old, "to": status, "comment": comment})
    if comment:
        session.add(BugComment(bug_id=bug.id, author_discord_id=actor.discord_id, author_name=actor.name,
                               content=comment[:5000], internal=True))
    _queue_sync(session, bug)
    if status in CLOSED_STATUSES and bug.forum_thread_id and settings_service.get_value(
            session, "bugs.post_resolution_in_thread"):
        enqueue(session, "bug.thread_message", {"bug_id": bug.id, "status": status},
                idempotency_key=f"bug-thread-{bug.id}-{status}-{bug.revision}")
    return bug


def update_bug(session: Session, actor: Actor, bug_id: int, data: dict) -> Bug:
    bug = get_bug(session, bug_id)
    changes: dict = {}
    for field, maxlen in (("title", 200), ("description", 20000), ("reproduction_steps", 4000)):
        if field in data and data[field] is not None and data[field] != getattr(bug, field):
            if field == "title" and not str(data[field]).strip():
                raise Invalid("Titel darf nicht leer sein.")
            changes[field] = True
            setattr(bug, field, str(data[field])[:maxlen])
    if "priority" in data and data["priority"] != bug.priority:
        if data["priority"] not in PRIORITIES:
            raise Invalid("Ungültige Priorität.")
        changes["priority"] = [bug.priority, data["priority"]]
        bug.priority = data["priority"]
    for field in ("version_id", "fixed_in_version_id"):
        if field in data and data[field] != getattr(bug, field):
            v = session.get(GameVersion, data[field]) if data[field] else None
            if data[field] and v is None:
                raise NotFound("Version nicht gefunden.")
            changes[field] = [getattr(bug, field), data[field]]
            setattr(bug, field, v.id if v else None)
            if field == "version_id":
                bug.version_text = v.version if v else bug.version_text
    if changes:
        audit(session, actor, "bugs.updated", "bug", bug.id, changes)
        _queue_sync(session, bug)
        session.flush()
        session.expire(bug, ["version", "fixed_in_version"])
    return bug


def add_comment(session: Session, actor: Actor, bug_id: int, content: str, internal: bool = True) -> BugComment:
    content = (content or "").strip()
    if not content:
        raise Invalid("Kommentar ist leer.")
    bug = get_bug(session, bug_id)
    c = BugComment(bug_id=bug.id, author_discord_id=actor.discord_id, author_name=actor.name, content=content[:5000],
                   internal=internal)
    session.add(c)
    session.flush()
    audit(session, actor, "bugs.comment_added", "bug", bug.id, {"internal": internal, "comment_id": c.id})
    if not internal and bug.forum_thread_id:
        enqueue(session, "bug.thread_comment", {"bug_id": bug.id, "comment_id": c.id})
    emit(session, "bug_comments", bug.id)
    return c


# ---------------------------------------------------------------------------
def serialize(b: Bug) -> dict:
    return {
        "id": b.id, "ref": fmt_id(b.id), "title": b.title, "description": b.description,
        "reproduction_steps": b.reproduction_steps, "status": b.status, "priority": b.priority,
        "reporter": {"discord_id": b.reporter_discord_id, "name": b.reporter_name},
        "version": {"id": b.version.id, "version": b.version.version} if b.version else None,
        "version_text": b.version_text,
        "fixed_in_version": {"id": b.fixed_in_version.id, "version": b.fixed_in_version.version}
        if b.fixed_in_version else None,
        "assignee": {"discord_id": b.assignee_discord_id, "name": b.assignee_name} if b.assignee_discord_id else None,
        "claimed_at": b.claimed_at.isoformat() if b.claimed_at else None,
        "resolved_at": b.resolved_at.isoformat() if b.resolved_at else None,
        "forum_thread_id": b.forum_thread_id, "forum_channel_id": b.forum_channel_id,
        "dev_message_id": b.dev_message_id, "source": b.source, "revision": b.revision,
        "created_at": b.created_at.isoformat(), "updated_at": b.updated_at.isoformat(),
    }


def thread_url(guild_id: Optional[str], bug: Bug) -> Optional[str]:
    if guild_id and bug.forum_thread_id:
        return f"https://discord.com/channels/{guild_id}/{bug.forum_thread_id}"
    return None
