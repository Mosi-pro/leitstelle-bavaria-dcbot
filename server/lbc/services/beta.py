"""Beta-Tester-Management: Tester, Testgruppen, Versionen, Historie, Einladungen."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.models import (
    BetaInvitation,
    BetaInvitationRecipient,
    BetaTester,
    GameVersion,
    TesterGroup,
    TesterHistory,
    TesterVersion,
    TestGroup,
    utcnow,
)
from . import settings_service
from .base import Actor, Conflict, Invalid, NotFound, audit, emit, enqueue, upsert_discord_user

STATUSES = ("active", "inactive", "blocked", "retired")
STATUS_LABELS = {"active": "Aktiv", "inactive": "Inaktiv", "blocked": "Gesperrt", "retired": "Ausgeschieden"}


def _history(session: Session, tester: BetaTester, actor: Actor, action: str, details: Optional[str] = None) -> None:
    session.add(TesterHistory(tester_id=tester.id, actor_name=actor.name[:100], action=action, details=details))


def _valid_snowflake(v: str) -> bool:
    return v.isdigit() and 15 <= len(v) <= 21


def ensure_tester(session: Session, actor: Actor, discord_id: str, username: Optional[str] = None,
                  application_id: Optional[int] = None) -> BetaTester:
    user = upsert_discord_user(session, discord_id, username=username)
    session.flush()
    tester = session.scalar(select(BetaTester).where(BetaTester.user_id == user.id))
    if tester is None:
        tester = BetaTester(user_id=user.id, status="active", application_id=application_id)
        session.add(tester)
        session.flush()
        _history(session, tester, actor, "created", "Aus Bewerbung übernommen" if application_id else None)
        audit(session, actor, "beta.tester_added", "beta_tester", tester.id, {"discord_id": discord_id})
    elif tester.status != "active":
        old = tester.status
        tester.status = "active"
        tester.left_at = None
        _history(session, tester, actor, "status_changed", f"{STATUS_LABELS[old]} → Aktiv")
    beta_role = settings_service.get_value(session, "roles.beta")
    if beta_role:
        enqueue(session, "member.add_role", {"discord_id": discord_id, "role_id": beta_role,
                                             "reason": "Beta-Tester"}, idempotency_key=f"beta-role-{tester.id}-add")
    emit(session, "beta", tester.id)
    return tester


def add_tester(session: Session, actor: Actor, data: dict) -> BetaTester:
    discord_id = str(data.get("discord_id") or "").strip()
    if not _valid_snowflake(discord_id):
        raise Invalid("Bitte eine gültige Discord-ID angeben.")
    user = upsert_discord_user(session, discord_id, username=data.get("username") or None)
    session.flush()
    if session.scalar(select(BetaTester).where(BetaTester.user_id == user.id)):
        raise Conflict("Diese Person ist bereits als Beta-Tester erfasst.")
    tester = ensure_tester(session, actor, discord_id, data.get("username") or None)
    tester.notes = data.get("notes") or None
    set_groups(session, actor, tester.id, data.get("group_ids") or [], log=False)
    set_versions(session, actor, tester.id, data.get("version_ids") or [], log=False)
    return tester


def get_tester(session: Session, tester_id: int) -> BetaTester:
    t = session.get(BetaTester, tester_id)
    if t is None:
        raise NotFound("Beta-Tester nicht gefunden.")
    return t


def update_tester(session: Session, actor: Actor, tester_id: int, data: dict) -> BetaTester:
    t = get_tester(session, tester_id)
    if "status" in data and data["status"] != t.status:
        if data["status"] not in STATUSES:
            raise Invalid("Ungültiger Status.")
        old = t.status
        t.status = data["status"]
        t.left_at = utcnow() if data["status"] == "retired" else None
        _history(session, t, actor, "status_changed", f"{STATUS_LABELS[old]} → {STATUS_LABELS[t.status]}")
        audit(session, actor, "beta.tester_status_changed", "beta_tester", t.id, {"from": old, "to": t.status})
        beta_role = settings_service.get_value(session, "roles.beta")
        if beta_role:
            kind = "member.add_role" if t.status == "active" else "member.remove_role"
            if t.status in ("active", "blocked", "retired"):
                enqueue(session, kind, {"discord_id": t.user.discord_id, "role_id": beta_role,
                                        "reason": f"Beta-Status: {STATUS_LABELS[t.status]}"})
    if "notes" in data and data["notes"] != t.notes:
        t.notes = data["notes"] or None
        _history(session, t, actor, "notes_updated")
    if "group_ids" in data:
        set_groups(session, actor, t.id, data["group_ids"] or [])
    if "version_ids" in data:
        set_versions(session, actor, t.id, data["version_ids"] or [])
    emit(session, "beta", t.id)
    return t


def remove_tester(session: Session, actor: Actor, tester_id: int) -> None:
    t = get_tester(session, tester_id)
    beta_role = settings_service.get_value(session, "roles.beta")
    if beta_role:
        enqueue(session, "member.remove_role", {"discord_id": t.user.discord_id, "role_id": beta_role,
                                                "reason": "Als Beta-Tester entfernt"})
    for g in session.scalars(select(TestGroup).join(TesterGroup, TesterGroup.group_id == TestGroup.id)
                             .where(TesterGroup.tester_id == t.id)):
        if g.discord_role_id:
            enqueue(session, "member.remove_role", {"discord_id": t.user.discord_id, "role_id": g.discord_role_id,
                                                    "reason": "Als Beta-Tester entfernt"})
    audit(session, actor, "beta.tester_removed", "beta_tester", t.id, {"discord_id": t.user.discord_id})
    session.delete(t)
    emit(session, "beta")


def set_groups(session: Session, actor: Actor, tester_id: int, group_ids: list[int], log: bool = True) -> None:
    t = get_tester(session, tester_id)
    wanted = {int(g) for g in group_ids}
    groups = {g.id: g for g in session.scalars(select(TestGroup).where(TestGroup.id.in_(wanted)))} if wanted else {}
    if len(groups) != len(wanted):
        raise NotFound("Mindestens eine Testgruppe existiert nicht.")
    current = set(session.scalars(select(TesterGroup.group_id).where(TesterGroup.tester_id == t.id)))
    added, removed = wanted - current, current - wanted
    if removed:
        session.execute(delete(TesterGroup).where(TesterGroup.tester_id == t.id, TesterGroup.group_id.in_(removed)))
        for g in session.scalars(select(TestGroup).where(TestGroup.id.in_(removed))):
            if g.discord_role_id:
                enqueue(session, "member.remove_role", {"discord_id": t.user.discord_id, "role_id": g.discord_role_id,
                                                        "reason": f"Testgruppe {g.name} entfernt"})
    for gid in added:
        session.add(TesterGroup(tester_id=t.id, group_id=gid))
        g = groups[gid]
        if g.discord_role_id and t.status == "active":
            enqueue(session, "member.add_role", {"discord_id": t.user.discord_id, "role_id": g.discord_role_id,
                                                 "reason": f"Testgruppe {g.name}"})
    if (added or removed) and log:
        names = {g.id: g.name for g in session.scalars(select(TestGroup).where(TestGroup.id.in_(added | removed)))}
        details = "; ".join([f"+ {names.get(i, i)}" for i in added] + [f"− {names.get(i, i)}" for i in removed])
        _history(session, t, actor, "groups_changed", details)
        audit(session, actor, "beta.tester_groups_changed", "beta_tester", t.id,
              {"added": sorted(added), "removed": sorted(removed)})


def set_versions(session: Session, actor: Actor, tester_id: int, version_ids: list[int], log: bool = True) -> None:
    t = get_tester(session, tester_id)
    wanted = {int(v) for v in version_ids}
    if wanted and session.scalar(select(func.count(GameVersion.id)).where(GameVersion.id.in_(wanted))) != len(wanted):
        raise NotFound("Mindestens eine Version existiert nicht.")
    current = set(session.scalars(select(TesterVersion.version_id).where(TesterVersion.tester_id == t.id)))
    added, removed = wanted - current, current - wanted
    if removed:
        session.execute(delete(TesterVersion).where(TesterVersion.tester_id == t.id, TesterVersion.version_id.in_(removed)))
    for vid in added:
        session.add(TesterVersion(tester_id=t.id, version_id=vid))
    if (added or removed) and log:
        _history(session, t, actor, "versions_changed", f"+{len(added)} / −{len(removed)}")
        audit(session, actor, "beta.tester_versions_changed", "beta_tester", t.id,
              {"added": sorted(added), "removed": sorted(removed)})


# ---------------------------------------------------------------------------
def save_group(session: Session, actor: Actor, data: dict, group_id: Optional[int] = None) -> TestGroup:
    name = (data.get("name") or "").strip()
    if not name:
        raise Invalid("Name der Testgruppe fehlt.")
    if session.scalar(select(TestGroup).where(TestGroup.name == name, TestGroup.id != (group_id or 0))):
        raise Conflict("Eine Testgruppe mit diesem Namen existiert bereits.")
    role = data.get("discord_role_id") or None
    if role and not _valid_snowflake(str(role)):
        raise Invalid("Die Rollen-ID muss eine Discord-ID sein.")
    if group_id:
        g = session.get(TestGroup, group_id)
        if g is None:
            raise NotFound("Testgruppe nicht gefunden.")
        action = "beta.group_updated"
    else:
        g = TestGroup(name=name)
        session.add(g)
        action = "beta.group_created"
    g.name = name[:80]
    g.description = data.get("description") or None
    g.discord_role_id = role
    g.active = bool(data.get("active", True))
    session.flush()
    audit(session, actor, action, "test_group", g.id, {"name": g.name})
    emit(session, "beta")
    return g


def delete_group(session: Session, actor: Actor, group_id: int) -> None:
    g = session.get(TestGroup, group_id)
    if g is None:
        raise NotFound("Testgruppe nicht gefunden.")
    audit(session, actor, "beta.group_deleted", "test_group", g.id, {"name": g.name})
    session.delete(g)
    emit(session, "beta")


def list_groups(session: Session) -> list[dict]:
    counts = dict(session.execute(select(TesterGroup.group_id, func.count(TesterGroup.id)).group_by(TesterGroup.group_id)).all())
    return [{"id": g.id, "name": g.name, "description": g.description, "discord_role_id": g.discord_role_id,
             "active": g.active, "tester_count": counts.get(g.id, 0)}
            for g in session.scalars(select(TestGroup).order_by(TestGroup.name))]


# ---------------------------------------------------------------------------
def create_invitation(session: Session, actor: Actor, data: dict) -> BetaInvitation:
    title = (data.get("title") or "").strip()
    message = (data.get("message") or "").strip()
    if not title or not message:
        raise Invalid("Titel und Nachricht sind erforderlich.")
    group_ids = [int(g) for g in data.get("group_ids") or []]
    tester_ids = {int(t) for t in data.get("tester_ids") or []}
    if group_ids:
        tester_ids |= set(session.scalars(select(TesterGroup.tester_id).where(TesterGroup.group_id.in_(group_ids))))
    if not tester_ids:
        raise Invalid("Keine Empfänger ausgewählt.")
    testers = session.scalars(select(BetaTester).where(BetaTester.id.in_(tester_ids), BetaTester.status == "active")).all()
    if not testers:
        raise Invalid("Unter den ausgewählten Empfängern ist kein aktiver Tester.")
    version = session.get(GameVersion, data["version_id"]) if data.get("version_id") else None
    inv = BetaInvitation(title=title[:150], message=message[:1800], version_id=version.id if version else None,
                         created_by=actor.name)
    session.add(inv)
    session.flush()
    for t in testers:
        r = BetaInvitationRecipient(invitation_id=inv.id, tester_id=t.id, status="pending")
        session.add(r)
        session.flush()
        enqueue(session, "beta.send_invitation", {"recipient_id": r.id},
                idempotency_key=f"beta-invite-{inv.id}-{t.id}", max_attempts=5)
        if version:
            if not session.scalar(select(TesterVersion).where(TesterVersion.tester_id == t.id,
                                                              TesterVersion.version_id == version.id)):
                session.add(TesterVersion(tester_id=t.id, version_id=version.id))
        _history(session, t, actor, "invited", inv.title)
    audit(session, actor, "beta.invitation_created", "beta_invitation", inv.id,
          {"title": inv.title, "recipients": len(testers), "version": version.version if version else None})
    emit(session, "beta")
    return inv


def render_invitation(inv: BetaInvitation, tester: BetaTester, version: Optional[GameVersion]) -> str:
    name = tester.user.display_name or tester.user.username
    text = inv.message.replace("{name}", name).replace("{version}", version.version if version else "")
    return text


def list_invitations(session: Session) -> list[dict]:
    rows = session.scalars(select(BetaInvitation).order_by(BetaInvitation.created_at.desc()).limit(100)).all()
    out = []
    for inv in rows:
        stats = dict(session.execute(select(BetaInvitationRecipient.status, func.count(BetaInvitationRecipient.id))
                                     .where(BetaInvitationRecipient.invitation_id == inv.id)
                                     .group_by(BetaInvitationRecipient.status)).all())
        version = session.get(GameVersion, inv.version_id) if inv.version_id else None
        out.append({"id": inv.id, "title": inv.title, "message": inv.message, "created_by": inv.created_by,
                    "created_at": inv.created_at.isoformat(), "version": version.version if version else None,
                    "sent": stats.get("sent", 0), "pending": stats.get("pending", 0), "failed": stats.get("failed", 0)})
    return out


def serialize(t: BetaTester, session: Session, detail: bool = False) -> dict:
    groups = session.execute(select(TestGroup.id, TestGroup.name).join(TesterGroup, TesterGroup.group_id == TestGroup.id)
                             .where(TesterGroup.tester_id == t.id)).all()
    versions = session.execute(select(GameVersion.id, GameVersion.version)
                               .join(TesterVersion, TesterVersion.version_id == GameVersion.id)
                               .where(TesterVersion.tester_id == t.id)).all()
    data = {
        "id": t.id, "discord_id": t.user.discord_id, "username": t.user.username, "display_name": t.user.display_name,
        "avatar_url": t.user.avatar_url, "status": t.status, "notes": t.notes, "joined_at": t.joined_at.isoformat(),
        "left_at": t.left_at.isoformat() if t.left_at else None, "application_id": t.application_id,
        "groups": [{"id": g, "name": n} for g, n in groups], "versions": [{"id": v, "version": n} for v, n in versions],
    }
    if detail:
        hist = session.scalars(select(TesterHistory).where(TesterHistory.tester_id == t.id)
                               .order_by(TesterHistory.created_at.desc(), TesterHistory.id.desc())).all()
        data["history"] = [{"actor_name": h.actor_name, "action": h.action, "details": h.details,
                            "created_at": h.created_at.isoformat()} for h in hist]
    return data
