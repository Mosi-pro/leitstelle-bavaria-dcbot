from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends
from sqlalchemy import or_, select

from ..core.db import session_scope
from ..core.models import Bug, BugAssignment, BugAttachment, BugComment, DiscordOutbox
from ..services import bugs as svc
from ..services import settings_service
from ..services.base import Forbidden, Invalid
from ..services.team import find_member_by_discord_id, member_permissions
from .deps import Principal, paginate, require

router = APIRouter(prefix="/api/bugs", tags=["bugs"])


def _ensure_can_edit(p: Principal, bug: Bug) -> None:
    """bugs.manage darf alles; der zuständige Entwickler (bugs.claim) darf seinen eigenen Bug bearbeiten."""
    if p.has("bugs.manage"):
        return
    if p.has("bugs.claim") and bug.assignee_discord_id == p.discord_id:
        return
    raise Forbidden("Nur der zuständige Entwickler oder Berechtigte mit bugs.manage dürfen diesen Bug bearbeiten.")


@router.get("")
def list_bugs(status: Optional[str] = None, assignee: Optional[str] = None, version_id: Optional[int] = None,
              priority: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 50,
              p: Principal = Depends(require("bugs.view"))) -> dict:
    with session_scope() as s:
        query = select(Bug)
        if status == "open":
            query = query.where(Bug.status.in_(("new", "claimed", "in_progress")))
        elif status:
            query = query.where(Bug.status == status)
        if assignee == "me":
            query = query.where(Bug.assignee_discord_id == p.discord_id)
        elif assignee == "none":
            query = query.where(Bug.assignee_discord_id.is_(None))
        elif assignee:
            query = query.where(Bug.assignee_discord_id == assignee)
        if version_id:
            query = query.where(Bug.version_id == version_id)
        if priority:
            query = query.where(Bug.priority == priority)
        if q:
            like = f"%{q}%"
            conds = [Bug.title.like(like), Bug.reporter_name.like(like), Bug.description.like(like)]
            if q.lstrip("#").isdigit():
                conds.append(Bug.id == int(q.lstrip("#")))
            query = query.where(or_(*conds))
        query = query.order_by(Bug.id.desc())
        return paginate(s, query, page, page_size, svc.serialize)


@router.post("")
def create_bug(data: dict = Body(...), p: Principal = Depends(require("bugs.manage"))) -> dict:
    with session_scope() as s:
        return svc.serialize(svc.create_manual(s, p.actor(), data))


@router.get("/developers")
def developers(p: Principal = Depends(require("bugs.view"))) -> list[dict]:
    """Teammitglieder mit bugs.claim – mögliche Empfänger einer Übergabe."""
    from ..core.models import TeamMember
    with session_scope() as s:
        out = []
        for m in s.scalars(select(TeamMember).where(TeamMember.status == "active")):
            if "bugs.claim" in member_permissions(m):
                out.append({"member_id": m.id, "discord_id": m.user.discord_id,
                            "name": m.user.display_name or m.user.username})
        return out


@router.get("/{bid}")
def get_bug(bid: int, p: Principal = Depends(require("bugs.view"))) -> dict:
    with session_scope() as s:
        b = svc.get_bug(s, bid)
        data = svc.serialize(b)
        guild = settings_service.get_value(s, "discord.guild_id")
        data["thread_url"] = svc.thread_url(guild, b)
        data["attachments"] = [{"id": a.id, "filename": a.filename, "url": a.url, "content_type": a.content_type,
                                "size": a.size} for a in s.scalars(select(BugAttachment).where(BugAttachment.bug_id == b.id))]
        data["comments"] = [{"id": c.id, "author_name": c.author_name, "content": c.content, "internal": c.internal,
                             "created_at": c.created_at.isoformat()}
                            for c in s.scalars(select(BugComment).where(BugComment.bug_id == b.id).order_by(BugComment.created_at))]
        data["assignments"] = [{"developer_name": a.developer_name, "developer_discord_id": a.developer_discord_id,
                                "assigned_by": a.assigned_by, "kind": a.kind, "note": a.note,
                                "assigned_at": a.assigned_at.isoformat(),
                                "ended_at": a.ended_at.isoformat() if a.ended_at else None}
                               for a in s.scalars(select(BugAssignment).where(BugAssignment.bug_id == b.id)
                                                  .order_by(BugAssignment.assigned_at))]
        jobs = s.scalars(select(DiscordOutbox).where(DiscordOutbox.kind.like("bug.%"))
                         .order_by(DiscordOutbox.id.desc()).limit(200)).all()
        data["sync"] = [{"id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts,
                         "last_error": j.last_error, "created_at": j.created_at.isoformat()}
                        for j in jobs if (j.payload or {}).get("bug_id") == b.id][:10]
        data["can_edit"] = p.has("bugs.manage") or (p.has("bugs.claim") and b.assignee_discord_id == p.discord_id)
        data["can_claim"] = p.has("bugs.claim") and b.assignee_discord_id is None and b.status == "new"
        return data


@router.put("/{bid}")
def update_bug(bid: int, data: dict = Body(...), p: Principal = Depends(require("bugs.view"))) -> dict:
    with session_scope() as s:
        _ensure_can_edit(p, svc.get_bug(s, bid))
        return svc.serialize(svc.update_bug(s, p.actor(), bid, data))


@router.post("/{bid}/claim")
def claim(bid: int, p: Principal = Depends(require("bugs.claim"))) -> dict:
    with session_scope() as s:
        result, bug = svc.claim(s, bid, p.discord_id, p.name, source="panel", ip=p.ip)
        if result == svc.ClaimResult.NOT_FOUND:
            raise Invalid("Bug nicht gefunden.")
        if result == svc.ClaimResult.TAKEN:
            from ..services.base import Conflict
            raise Conflict(svc.ALREADY_CLAIMED_MSG)
        if result == svc.ClaimResult.CLOSED:
            from ..services.base import Conflict
            raise Conflict("Dieser Bug ist bereits abgeschlossen.")
        return svc.serialize(bug)


@router.post("/{bid}/status")
def set_status(bid: int, data: dict = Body(...), p: Principal = Depends(require("bugs.view"))) -> dict:
    with session_scope() as s:
        _ensure_can_edit(p, svc.get_bug(s, bid))
        return svc.serialize(svc.set_status(s, p.actor(), bid, data.get("status", ""), data.get("comment")))


@router.post("/{bid}/handover")
def handover(bid: int, data: dict = Body(...), p: Principal = Depends(require("bugs.view"))) -> dict:
    with session_scope() as s:
        _ensure_can_edit(p, svc.get_bug(s, bid))
        target = find_member_by_discord_id(s, str(data.get("discord_id", "")))
        if target is None or "bugs.claim" not in member_permissions(target):
            raise Invalid("Der Empfänger muss ein aktives Teammitglied mit dem Recht bugs.claim sein.")
        return svc.serialize(svc.handover(s, p.actor(), bid, target.user.discord_id,
                                          target.user.display_name or target.user.username, data.get("note")))


@router.post("/{bid}/unassign")
def unassign(bid: int, p: Principal = Depends(require("bugs.manage"))) -> dict:
    with session_scope() as s:
        return svc.serialize(svc.unassign(s, p.actor(), bid))


@router.post("/{bid}/comments")
def comment(bid: int, data: dict = Body(...), p: Principal = Depends(require("bugs.view"))) -> dict:
    with session_scope() as s:
        internal = bool(data.get("internal", True))
        if not internal:
            _ensure_can_edit(p, svc.get_bug(s, bid))
        c = svc.add_comment(s, p.actor(), bid, data.get("content", ""), internal)
        return {"id": c.id}


@router.post("/{bid}/resync")
def resync(bid: int, p: Principal = Depends(require("bugs.manage"))) -> dict:
    """Erzwingt eine erneute Synchronisierung mit Discord (Entwickler-Nachricht neu aufbauen bzw. senden)."""
    from ..services.base import audit, enqueue
    with session_scope() as s:
        b = svc.get_bug(s, bid)
        if b.dev_message_id:
            enqueue(s, "bug.sync_message", {"bug_id": b.id}, idempotency_key=f"bug-sync-{b.id}")
        else:
            enqueue(s, "bug.notify_developers", {"bug_id": b.id}, idempotency_key=f"bug-notify-{b.id}")
        audit(s, p.actor(), "bugs.resync", "bug", b.id)
    return {"ok": True}
