"""Bewerbungen, Beta-Tester, Versionen, Teamverwaltung."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends
from sqlalchemy import or_, select

from ..core.db import session_scope
from ..core.models import Application, ApplicationType, BetaTester, DiscordUser, TeamMember, TesterGroup
from ..core.permissions import PERMISSIONS
from ..services import applications as app_svc
from ..services import beta as beta_svc
from ..services import stats as stats_svc
from ..services import team as team_svc
from ..services import versions as ver_svc
from ..services.base import NotFound
from .deps import Principal, paginate, require

router = APIRouter(prefix="/api", tags=["people"])


# ---------------------------------------------------------------------------- Bewerbungen
@router.get("/applications")
def list_applications(status: Optional[str] = None, type_id: Optional[int] = None, q: Optional[str] = None,
                      page: int = 1, page_size: int = 50, p: Principal = Depends(require("applications.view"))) -> dict:
    with session_scope() as s:
        query = select(Application)
        if status == "open":
            query = query.where(Application.status.in_(app_svc.OPEN_STATUSES))
        elif status:
            query = query.where(Application.status == status)
        if type_id:
            query = query.where(Application.type_id == type_id)
        if q:
            query = query.where(Application.applicant.has(or_(DiscordUser.username.like(f"%{q}%"),
                                                              DiscordUser.discord_id == q)))
        query = query.order_by(Application.submitted_at.desc())
        return paginate(s, query, page, page_size, app_svc.serialize)


@router.get("/applications/types")
def list_types(p: Principal = Depends(require("applications.view"))) -> list[dict]:
    with session_scope() as s:
        return [app_svc.serialize_type(t) for t in s.scalars(select(ApplicationType).order_by(ApplicationType.name))]


@router.post("/applications/types")
def create_type(data: dict = Body(...), p: Principal = Depends(require("applications.configure"))) -> dict:
    with session_scope() as s:
        return app_svc.serialize_type(app_svc.save_type(s, p.actor(), data))


@router.put("/applications/types/{tid}")
def update_type(tid: int, data: dict = Body(...), p: Principal = Depends(require("applications.configure"))) -> dict:
    with session_scope() as s:
        return app_svc.serialize_type(app_svc.save_type(s, p.actor(), data, tid))


@router.delete("/applications/types/{tid}")
def delete_type(tid: int, p: Principal = Depends(require("applications.configure"))) -> dict:
    with session_scope() as s:
        app_svc.delete_type(s, p.actor(), tid)
    return {"ok": True}


@router.get("/applications/{aid}")
def get_application(aid: int, p: Principal = Depends(require("applications.view"))) -> dict:
    with session_scope() as s:
        return app_svc.serialize(app_svc.get_application(s, aid), detail=True, session=s)


@router.post("/applications/{aid}/status")
def application_status(aid: int, data: dict = Body(...), p: Principal = Depends(require("applications.review"))) -> dict:
    with session_scope() as s:
        app = app_svc.set_status(s, p.actor(), aid, data.get("status", ""), data.get("comment"), data.get("message"))
        return app_svc.serialize(app)


@router.post("/applications/{aid}/reviewer")
def application_reviewer(aid: int, data: dict = Body(...), p: Principal = Depends(require("applications.review"))) -> dict:
    with session_scope() as s:
        return app_svc.serialize(app_svc.assign_reviewer(s, p.actor(), aid, data.get("member_id")))


@router.post("/applications/{aid}/notes")
def application_note(aid: int, data: dict = Body(...), p: Principal = Depends(require("applications.review"))) -> dict:
    with session_scope() as s:
        n = app_svc.add_note(s, p.actor(), aid, data.get("content", ""))
        return {"id": n.id}


@router.post("/applications/{aid}/rating")
def application_rating(aid: int, data: dict = Body(...), p: Principal = Depends(require("applications.review"))) -> dict:
    with session_scope() as s:
        r = app_svc.rate(s, p.actor(), aid, int(data.get("score", 0)), data.get("comment"))
        return {"id": r.id, "score": r.score}


# ---------------------------------------------------------------------------- Beta
@router.get("/beta/testers")
def list_testers(status: Optional[str] = None, group_id: Optional[int] = None, q: Optional[str] = None,
                 page: int = 1, page_size: int = 100, p: Principal = Depends(require("beta.view"))) -> dict:
    with session_scope() as s:
        query = select(BetaTester)
        if status:
            query = query.where(BetaTester.status == status)
        if group_id:
            query = query.where(BetaTester.id.in_(select(TesterGroup.tester_id).where(TesterGroup.group_id == group_id)))
        if q:
            query = query.where(BetaTester.user.has(or_(DiscordUser.username.like(f"%{q}%"), DiscordUser.discord_id == q)))
        query = query.order_by(BetaTester.joined_at.desc())
        return paginate(s, query, page, page_size, lambda t: beta_svc.serialize(t, s))


@router.post("/beta/testers")
def add_tester(data: dict = Body(...), p: Principal = Depends(require("beta.manage"))) -> dict:
    with session_scope() as s:
        return beta_svc.serialize(beta_svc.add_tester(s, p.actor(), data), s)


@router.get("/beta/testers/{tid}")
def get_tester(tid: int, p: Principal = Depends(require("beta.view"))) -> dict:
    with session_scope() as s:
        return beta_svc.serialize(beta_svc.get_tester(s, tid), s, detail=True)


@router.put("/beta/testers/{tid}")
def update_tester(tid: int, data: dict = Body(...), p: Principal = Depends(require("beta.manage"))) -> dict:
    with session_scope() as s:
        return beta_svc.serialize(beta_svc.update_tester(s, p.actor(), tid, data), s, detail=True)


@router.delete("/beta/testers/{tid}")
def delete_tester(tid: int, p: Principal = Depends(require("beta.manage"))) -> dict:
    with session_scope() as s:
        beta_svc.remove_tester(s, p.actor(), tid)
    return {"ok": True}


@router.get("/beta/groups")
def list_groups(p: Principal = Depends(require("beta.view"))) -> list[dict]:
    with session_scope() as s:
        return beta_svc.list_groups(s)


@router.post("/beta/groups")
def create_group(data: dict = Body(...), p: Principal = Depends(require("beta.manage"))) -> dict:
    with session_scope() as s:
        g = beta_svc.save_group(s, p.actor(), data)
        return {"id": g.id}


@router.put("/beta/groups/{gid}")
def update_group(gid: int, data: dict = Body(...), p: Principal = Depends(require("beta.manage"))) -> dict:
    with session_scope() as s:
        g = beta_svc.save_group(s, p.actor(), data, gid)
        return {"id": g.id}


@router.delete("/beta/groups/{gid}")
def delete_group(gid: int, p: Principal = Depends(require("beta.manage"))) -> dict:
    with session_scope() as s:
        beta_svc.delete_group(s, p.actor(), gid)
    return {"ok": True}


@router.get("/beta/invitations")
def list_invitations(p: Principal = Depends(require("beta.view"))) -> list[dict]:
    with session_scope() as s:
        return beta_svc.list_invitations(s)


@router.post("/beta/invitations")
def create_invitation(data: dict = Body(...), p: Principal = Depends(require("beta.manage"))) -> dict:
    with session_scope() as s:
        inv = beta_svc.create_invitation(s, p.actor(), data)
        return {"id": inv.id}


# ---------------------------------------------------------------------------- Versionen
@router.get("/versions")
def list_versions(p: Principal = Depends(require("versions.view", "bugs.view", "beta.view", any_of=True))) -> list[dict]:
    with session_scope() as s:
        return [ver_svc.serialize(v, s) for v in ver_svc.list_versions(s)]


@router.post("/versions")
def create_version(data: dict = Body(...), p: Principal = Depends(require("versions.manage"))) -> dict:
    with session_scope() as s:
        return ver_svc.serialize(ver_svc.save(s, p.actor(), data), s, detail=True)


@router.get("/versions/{vid}")
def get_version(vid: int, p: Principal = Depends(require("versions.view"))) -> dict:
    with session_scope() as s:
        from ..core.models import GameVersion
        v = s.get(GameVersion, vid)
        if v is None:
            raise NotFound("Version nicht gefunden.")
        return ver_svc.serialize(v, s, detail=True)


@router.put("/versions/{vid}")
def update_version(vid: int, data: dict = Body(...), p: Principal = Depends(require("versions.manage"))) -> dict:
    with session_scope() as s:
        return ver_svc.serialize(ver_svc.save(s, p.actor(), data, vid), s, detail=True)


@router.delete("/versions/{vid}")
def delete_version(vid: int, p: Principal = Depends(require("versions.manage"))) -> dict:
    with session_scope() as s:
        ver_svc.delete_version(s, p.actor(), vid)
    return {"ok": True}


@router.post("/versions/{vid}/bugs")
def assign_version_bugs(vid: int, data: dict = Body(...), p: Principal = Depends(require("versions.manage", "bugs.manage"))) -> dict:
    with session_scope() as s:
        n = ver_svc.assign_bugs(s, p.actor(), vid, data.get("bug_ids") or [], data.get("field", "version_id"))
    return {"assigned": n}


# ---------------------------------------------------------------------------- Team
@router.get("/team/permissions")
def permission_catalog(p: Principal = Depends(require("team.view"))) -> list[dict]:
    return [{"key": k, "label": v} for k, v in PERMISSIONS.items()]


@router.get("/team/members")
def list_members(p: Principal = Depends(require("team.view"))) -> list[dict]:
    with session_scope() as s:
        stats = {m["member_id"]: m for m in stats_svc.team_stats(s)["members"]}
        members = s.scalars(select(TeamMember).order_by(TeamMember.status, TeamMember.id)).all()
        return [team_svc.serialize_member(m, stats.get(m.id)) for m in members]


@router.get("/team/directory")
def directory(p: Principal = Depends(require("dashboard.view"))) -> list[dict]:
    """Minimale Liste aktiver Teammitglieder (für Zuweisungs-Auswahlfelder)."""
    with session_scope() as s:
        return [{"id": m.id, "discord_id": m.user.discord_id, "name": m.user.display_name or m.user.username,
                 "role": m.role.name if m.role else None}
                for m in s.scalars(select(TeamMember).where(TeamMember.status == "active"))]


@router.post("/team/members")
def add_member(data: dict = Body(...), p: Principal = Depends(require("team.manage"))) -> dict:
    with session_scope() as s:
        return team_svc.serialize_member(team_svc.add_member(s, p.actor(), data))


@router.put("/team/members/{mid}")
def update_member(mid: int, data: dict = Body(...), p: Principal = Depends(require("team.manage"))) -> dict:
    with session_scope() as s:
        return team_svc.serialize_member(team_svc.update_member(s, p.actor(), mid, data))


@router.delete("/team/members/{mid}")
def remove_member(mid: int, p: Principal = Depends(require("team.manage"))) -> dict:
    with session_scope() as s:
        team_svc.remove_member(s, p.actor(), mid)
    return {"ok": True}


@router.get("/team/roles")
def list_roles(p: Principal = Depends(require("team.view"))) -> list[dict]:
    with session_scope() as s:
        return team_svc.list_roles(s)


@router.post("/team/roles")
def create_role(data: dict = Body(...), p: Principal = Depends(require("team.manage"))) -> dict:
    with session_scope() as s:
        return team_svc.serialize_role(team_svc.create_role(s, p.actor(), data))


@router.put("/team/roles/{rid}")
def update_role(rid: int, data: dict = Body(...), p: Principal = Depends(require("team.manage"))) -> dict:
    with session_scope() as s:
        return team_svc.serialize_role(team_svc.update_role(s, p.actor(), rid, data))


@router.delete("/team/roles/{rid}")
def delete_role(rid: int, p: Principal = Depends(require("team.manage"))) -> dict:
    with session_scope() as s:
        team_svc.delete_role(s, p.actor(), rid)
    return {"ok": True}
