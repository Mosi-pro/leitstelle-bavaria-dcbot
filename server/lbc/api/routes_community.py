"""Community: Moderation, Verwarnungen, Auto-Moderation, Ankündigungen, Vorlagen, Umfragen, Mitglieder."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends
from sqlalchemy import or_, select

from ..core.db import session_scope
from ..core.models import (
    Announcement,
    AutoModRule,
    DiscordUser,
    MessageTemplate,
    ModerationAction,
    Poll,
    Warning,
)
from ..services import community as svc
from ..services import stats as stats_svc
from .deps import Principal, paginate, require

router = APIRouter(prefix="/api/community", tags=["community"])


@router.get("/overview")
def overview(p: Principal = Depends(require("community.view"))) -> dict:
    with session_scope() as s:
        return stats_svc.community_stats(s, 30)


@router.get("/members")
def members(q: Optional[str] = None, verified: Optional[bool] = None, page: int = 1, page_size: int = 50,
            p: Principal = Depends(require("community.view"))) -> dict:
    with session_scope() as s:
        query = select(DiscordUser).where(DiscordUser.is_member.is_(True))
        if q:
            query = query.where(or_(DiscordUser.username.like(f"%{q}%"), DiscordUser.discord_id == q,
                                    DiscordUser.display_name.like(f"%{q}%")))
        if verified is not None:
            query = query.where(DiscordUser.verified.is_(verified))
        query = query.order_by(DiscordUser.joined_guild_at.desc())
        return paginate(s, query, page, page_size, lambda u: {
            "discord_id": u.discord_id, "username": u.username, "display_name": u.display_name,
            "avatar_url": u.avatar_url, "verified": u.verified,
            "joined_guild_at": u.joined_guild_at.isoformat() if u.joined_guild_at else None})


@router.get("/moderation")
def moderation_list(action: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 50,
                    p: Principal = Depends(require("community.view"))) -> dict:
    with session_scope() as s:
        query = select(ModerationAction)
        if action:
            query = query.where(ModerationAction.action == action)
        if q:
            query = query.where(ModerationAction.target.has(or_(DiscordUser.username.like(f"%{q}%"),
                                                                DiscordUser.discord_id == q)))
        query = query.order_by(ModerationAction.created_at.desc())
        return paginate(s, query, page, page_size, svc.serialize_mod)


@router.post("/moderation")
def moderation_create(data: dict = Body(...), p: Principal = Depends(require("community.moderate"))) -> dict:
    with session_scope() as s:
        ma = svc.moderate(s, p.actor(), target_discord_id=data.get("discord_id", ""), action=data.get("action", ""),
                          reason=data.get("reason", ""), duration_minutes=data.get("duration_minutes"),
                          delete_message_days=int(data.get("delete_message_days") or 0))
        return svc.serialize_mod(ma)


@router.get("/moderation/user/{discord_id}")
def moderation_user(discord_id: str, p: Principal = Depends(require("community.view"))) -> dict:
    with session_scope() as s:
        return svc.user_history(s, discord_id)


@router.get("/warnings")
def warnings(active: Optional[bool] = None, page: int = 1, page_size: int = 50,
             p: Principal = Depends(require("community.view"))) -> dict:
    with session_scope() as s:
        query = select(Warning)
        if active is not None:
            query = query.where(Warning.active.is_(active))
        return paginate(s, query.order_by(Warning.created_at.desc()), page, page_size, svc.serialize_warning)


@router.post("/warnings/{wid}/revoke")
def revoke_warning(wid: int, p: Principal = Depends(require("community.moderate"))) -> dict:
    with session_scope() as s:
        return svc.serialize_warning(svc.revoke_warning(s, p.actor(), wid))


@router.get("/automod")
def automod_rules(p: Principal = Depends(require("community.view"))) -> list[dict]:
    with session_scope() as s:
        return [svc.serialize_rule(r) for r in s.scalars(select(AutoModRule).order_by(AutoModRule.id))]


@router.post("/automod")
def automod_create(data: dict = Body(...), p: Principal = Depends(require("community.moderate"))) -> dict:
    with session_scope() as s:
        return svc.serialize_rule(svc.save_rule(s, p.actor(), data))


@router.put("/automod/{rid}")
def automod_update(rid: int, data: dict = Body(...), p: Principal = Depends(require("community.moderate"))) -> dict:
    with session_scope() as s:
        return svc.serialize_rule(svc.save_rule(s, p.actor(), data, rid))


@router.delete("/automod/{rid}")
def automod_delete(rid: int, p: Principal = Depends(require("community.moderate"))) -> dict:
    with session_scope() as s:
        svc.delete_rule(s, p.actor(), rid)
    return {"ok": True}


@router.get("/announcements")
def announcements(p: Principal = Depends(require("community.view"))) -> list[dict]:
    with session_scope() as s:
        return [svc.serialize_announcement(a) for a in
                s.scalars(select(Announcement).order_by(Announcement.created_at.desc()).limit(200))]


@router.post("/announcements")
def announcement_create(data: dict = Body(...), p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        return svc.serialize_announcement(svc.create_announcement(s, p.actor(), data, bool(data.get("publish"))))


@router.put("/announcements/{aid}")
def announcement_update(aid: int, data: dict = Body(...), p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        return svc.serialize_announcement(svc.update_announcement(s, p.actor(), aid, data))


@router.post("/announcements/{aid}/publish")
def announcement_publish(aid: int, p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        return svc.serialize_announcement(svc.publish_announcement(s, p.actor(), aid))


@router.delete("/announcements/{aid}")
def announcement_delete(aid: int, p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        svc.delete_announcement(s, p.actor(), aid)
    return {"ok": True}


@router.get("/templates")
def templates(p: Principal = Depends(require("community.view"))) -> list[dict]:
    with session_scope() as s:
        return [{"id": t.id, "name": t.name, "title": t.title, "content": t.content, "color": t.color,
                 "as_embed": t.as_embed} for t in s.scalars(select(MessageTemplate).order_by(MessageTemplate.name))]


@router.post("/templates")
def template_create(data: dict = Body(...), p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        return {"id": svc.save_template(s, p.actor(), data).id}


@router.put("/templates/{tid}")
def template_update(tid: int, data: dict = Body(...), p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        return {"id": svc.save_template(s, p.actor(), data, tid).id}


@router.delete("/templates/{tid}")
def template_delete(tid: int, p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        svc.delete_template(s, p.actor(), tid)
    return {"ok": True}


@router.get("/polls")
def polls(p: Principal = Depends(require("community.view"))) -> list[dict]:
    with session_scope() as s:
        return [svc.serialize_poll(s, x) for x in s.scalars(select(Poll).order_by(Poll.created_at.desc()).limit(100))]


@router.post("/polls")
def poll_create(data: dict = Body(...), p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        return svc.serialize_poll(s, svc.create_poll(s, p.actor(), data, bool(data.get("publish"))))


@router.post("/polls/{pid}/publish")
def poll_publish(pid: int, p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        return svc.serialize_poll(s, svc.publish_poll(s, p.actor(), pid))


@router.post("/polls/{pid}/close")
def poll_close(pid: int, p: Principal = Depends(require("community.announce"))) -> dict:
    with session_scope() as s:
        return svc.serialize_poll(s, svc.close_poll(s, p.actor(), pid))
