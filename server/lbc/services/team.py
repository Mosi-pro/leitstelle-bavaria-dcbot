"""Teamverwaltung, Rollen und individuelles Berechtigungssystem."""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.models import (
    Automation,
    DiscordUser,
    MemberPermissionOverride,
    RolePermission,
    TeamMember,
    TeamRole,
    TicketCategory,
    utcnow,
)
from ..core.permissions import ALL_PERMISSIONS, DEFAULT_ROLES, PERMISSIONS, effective_permissions
from .base import Actor, Conflict, Forbidden, Invalid, NotFound, audit, emit, upsert_discord_user

log = logging.getLogger("lbc.team")

MEMBER_STATUSES = ("active", "inactive", "suspended")

DEFAULT_TICKET_CATEGORIES = [
    ("Allgemeiner Support", "Allgemeine Fragen rund um Leitstelle Bavaria", 10),
    ("Technischer Support", "Technische Probleme, Installation, Abstürze", 20),
    ("Sonstige Anliegen", "Alles, was in keine andere Kategorie passt", 30),
]

DEFAULT_AUTOMATIONS = [
    ("ticket_auto_close", "Automatische Ticket-Schließung", "Warnt und schließt inaktive Tickets.", 15),
    ("weekly_report", "Wöchentlicher Report", "Erstellt den Wochenreport und veröffentlicht ihn in Discord.", 60),
    ("retention_cleanup", "Datenschutz-Bereinigung",
     "Anonymisiert abgelaufene Bewerbungen und löscht alte Ticketnachrichten, Logs und Sync-Aufträge.", 360),
    ("poll_close", "Umfragen beenden", "Schließt Umfragen nach Ablauf und aktualisiert das Ergebnis.", 5),
    ("timeout_expiry", "Moderation: abgelaufene Maßnahmen", "Markiert abgelaufene Timeouts.", 30),
    ("session_cleanup", "Sitzungen bereinigen", "Entfernt abgelaufene Panel-Sitzungen.", 60),
]


def bootstrap(session: Session, owner_discord_id: Optional[str] = None) -> None:
    """Legt Standardrollen, Ticketkategorien, Automatisierungen und den Inhaber an (idempotent)."""
    if session.scalar(select(func.count(TeamRole.id))) == 0:
        for spec in DEFAULT_ROLES:
            role = TeamRole(name=spec["name"], position=spec["position"], is_owner=spec.get("is_owner", False),
                            description=spec.get("description"))
            role.permissions = [RolePermission(permission=p) for p in spec["permissions"]]
            session.add(role)
        session.flush()
        log.info("Standardrollen angelegt")
    if session.scalar(select(func.count(TicketCategory.id))) == 0:
        for name, desc, order in DEFAULT_TICKET_CATEGORIES:
            session.add(TicketCategory(name=name, description=desc, sort_order=order))
    existing_auto = set(session.scalars(select(Automation.key)))
    for key, name, desc, interval in DEFAULT_AUTOMATIONS:
        if key not in existing_auto:
            session.add(Automation(key=key, name=name, description=desc, interval_minutes=interval))
    if owner_discord_id and owner_discord_id.isdigit():
        owner_role = session.scalar(select(TeamRole).where(TeamRole.is_owner.is_(True)).order_by(TeamRole.position))
        user = upsert_discord_user(session, owner_discord_id)
        member = session.scalar(select(TeamMember).where(TeamMember.user_id == user.id))
        if member is None:
            session.flush()
            session.add(TeamMember(user_id=user.id, role_id=owner_role.id if owner_role else None, status="active"))
            audit(session, Actor.system(), "team.owner_bootstrapped", "team_member", owner_discord_id)
            log.info("Inhaber %s als Teammitglied angelegt", owner_discord_id)
    session.flush()


# ---------------------------------------------------------------------------
def member_permissions(member: TeamMember) -> set[str]:
    if member.status != "active":
        return set()
    role_perms = {p.permission for p in member.role.permissions} if member.role else set()
    is_owner = bool(member.role and member.role.is_owner)
    overrides = {o.permission: o.allow for o in member.overrides}
    return effective_permissions(role_perms, is_owner, overrides)


def find_member_by_discord_id(session: Session, discord_id: str) -> Optional[TeamMember]:
    return session.scalar(
        select(TeamMember).join(DiscordUser, TeamMember.user_id == DiscordUser.id).where(DiscordUser.discord_id == str(discord_id))
    )


def serialize_role(role: TeamRole, member_count: int = 0) -> dict:
    return {
        "id": role.id, "name": role.name, "description": role.description, "position": role.position,
        "is_owner": role.is_owner, "discord_role_id": role.discord_role_id,
        "permissions": sorted(p.permission for p in role.permissions), "member_count": member_count,
    }


def serialize_member(m: TeamMember, with_stats: Optional[dict] = None) -> dict:
    return {
        "id": m.id,
        "discord_id": m.user.discord_id,
        "username": m.user.username,
        "display_name": m.user.display_name,
        "avatar_url": m.user.avatar_url,
        "role": {"id": m.role.id, "name": m.role.name, "is_owner": m.role.is_owner} if m.role else None,
        "status": m.status,
        "joined_team_at": m.joined_team_at.isoformat() if m.joined_team_at else None,
        "notes": m.notes,
        "last_login_at": m.last_login_at.isoformat() if m.last_login_at else None,
        "last_activity_at": m.last_activity_at.isoformat() if m.last_activity_at else None,
        "overrides": [{"permission": o.permission, "allow": o.allow} for o in m.overrides],
        "permissions": sorted(member_permissions(m)),
        "stats": with_stats,
    }


def list_roles(session: Session) -> list[dict]:
    counts = dict(session.execute(select(TeamMember.role_id, func.count(TeamMember.id)).group_by(TeamMember.role_id)).all())
    return [serialize_role(r, counts.get(r.id, 0)) for r in session.scalars(select(TeamRole).order_by(TeamRole.position))]


def _check_perms(perms: list[str]) -> list[str]:
    bad = [p for p in perms if p not in ALL_PERMISSIONS]
    if bad:
        raise Invalid("Unbekannte Berechtigung(en): " + ", ".join(bad))
    return sorted(set(perms))


def _actor_is_owner(session: Session, actor: Actor) -> bool:
    if actor.member_id is None:
        return actor.source == "system"
    m = session.get(TeamMember, actor.member_id)
    return bool(m and m.role and m.role.is_owner)


def create_role(session: Session, actor: Actor, data: dict) -> TeamRole:
    name = (data.get("name") or "").strip()
    if not name:
        raise Invalid("Rollenname fehlt.")
    if session.scalar(select(TeamRole).where(TeamRole.name == name)):
        raise Conflict("Eine Rolle mit diesem Namen existiert bereits.")
    perms = _check_perms(data.get("permissions") or [])
    role = TeamRole(name=name[:60], description=data.get("description"), position=int(data.get("position") or 100),
                    discord_role_id=data.get("discord_role_id") or None)
    role.permissions = [RolePermission(permission=p) for p in perms]
    session.add(role)
    session.flush()
    audit(session, actor, "team.role_created", "team_role", role.id, {"name": name, "permissions": perms})
    emit(session, "team")
    return role


def update_role(session: Session, actor: Actor, role_id: int, data: dict) -> TeamRole:
    role = session.get(TeamRole, role_id)
    if role is None:
        raise NotFound("Rolle nicht gefunden.")
    if role.is_owner and not _actor_is_owner(session, actor):
        raise Forbidden("Nur der Inhaber kann die Inhaber-Rolle verändern.")
    changes: dict = {}
    if "name" in data and data["name"] and data["name"] != role.name:
        if session.scalar(select(TeamRole).where(TeamRole.name == data["name"], TeamRole.id != role.id)):
            raise Conflict("Eine Rolle mit diesem Namen existiert bereits.")
        changes["name"] = [role.name, data["name"]]
        role.name = data["name"][:60]
    for field in ("description", "discord_role_id"):
        if field in data and data[field] != getattr(role, field):
            changes[field] = [getattr(role, field), data[field]]
            setattr(role, field, data[field] or None)
    if "position" in data and data["position"] is not None:
        role.position = int(data["position"])
    if "permissions" in data and not role.is_owner:
        new = set(_check_perms(data["permissions"]))
        old = {p.permission for p in role.permissions}
        if new != old:
            changes["permissions"] = {"added": sorted(new - old), "removed": sorted(old - new)}
            role.permissions = [p for p in role.permissions if p.permission in new] + [
                RolePermission(permission=p) for p in sorted(new - old)
            ]
    if changes:
        audit(session, actor, "team.role_updated", "team_role", role.id, changes)
        emit(session, "team")
    return role


def delete_role(session: Session, actor: Actor, role_id: int) -> None:
    role = session.get(TeamRole, role_id)
    if role is None:
        raise NotFound("Rolle nicht gefunden.")
    if role.is_owner:
        raise Forbidden("Die Inhaber-Rolle kann nicht gelöscht werden.")
    count = session.scalar(select(func.count(TeamMember.id)).where(TeamMember.role_id == role.id))
    if count:
        raise Conflict(f"Die Rolle ist noch {count} Teammitglied(ern) zugewiesen.")
    audit(session, actor, "team.role_deleted", "team_role", role.id, {"name": role.name})
    session.delete(role)
    emit(session, "team")


def add_member(session: Session, actor: Actor, data: dict) -> TeamMember:
    discord_id = str(data.get("discord_id") or "").strip()
    if not discord_id.isdigit() or not (15 <= len(discord_id) <= 21):
        raise Invalid("Bitte eine gültige Discord-ID (15–21 Ziffern) angeben.")
    role_id = data.get("role_id")
    role = session.get(TeamRole, role_id) if role_id else None
    if role_id and role is None:
        raise NotFound("Rolle nicht gefunden.")
    if role and role.is_owner and not _actor_is_owner(session, actor):
        raise Forbidden("Nur der Inhaber kann weitere Inhaber ernennen.")
    user = upsert_discord_user(session, discord_id, username=data.get("username") or None)
    if session.scalar(select(TeamMember).where(TeamMember.user_id == user.id)):
        raise Conflict("Diese Person ist bereits Teammitglied.")
    member = TeamMember(user_id=user.id, role_id=role.id if role else None, status="active",
                        notes=data.get("notes"), joined_team_at=date.today())
    session.add(member)
    session.flush()
    session.refresh(member)
    audit(session, actor, "team.member_added", "team_member", member.id,
          {"discord_id": discord_id, "role": role.name if role else None})
    emit(session, "team")
    return member


def update_member(session: Session, actor: Actor, member_id: int, data: dict) -> TeamMember:
    member = session.get(TeamMember, member_id)
    if member is None:
        raise NotFound("Teammitglied nicht gefunden.")
    is_owner_member = bool(member.role and member.role.is_owner)
    actor_owner = _actor_is_owner(session, actor)
    if is_owner_member and not actor_owner:
        raise Forbidden("Nur der Inhaber kann Inhaber-Konten verändern.")
    changes: dict = {}
    if "role_id" in data and data["role_id"] != member.role_id:
        role = session.get(TeamRole, data["role_id"]) if data["role_id"] else None
        if data["role_id"] and role is None:
            raise NotFound("Rolle nicht gefunden.")
        if role and role.is_owner and not actor_owner:
            raise Forbidden("Nur der Inhaber kann weitere Inhaber ernennen.")
        if is_owner_member and actor.member_id == member.id:
            raise Forbidden("Du kannst dir die Inhaber-Rolle nicht selbst entziehen.")
        changes["role"] = [member.role.name if member.role else None, role.name if role else None]
        member.role_id = role.id if role else None
    if "status" in data and data["status"] != member.status:
        if data["status"] not in MEMBER_STATUSES:
            raise Invalid("Ungültiger Status.")
        if actor.member_id == member.id:
            raise Forbidden("Du kannst deinen eigenen Status nicht ändern.")
        changes["status"] = [member.status, data["status"]]
        member.status = data["status"]
    if "notes" in data:
        member.notes = data["notes"]
    if "overrides" in data:
        new = {}
        for o in data["overrides"] or []:
            perm = o.get("permission")
            if perm not in PERMISSIONS:
                raise Invalid(f"Unbekannte Berechtigung: {perm}")
            new[perm] = bool(o.get("allow"))
        old = {o.permission: o.allow for o in member.overrides}
        if new != old:
            changes["overrides"] = {"old": old, "new": new}
            member.overrides.clear()
            session.flush()
            for perm, allow in new.items():
                member.overrides.append(MemberPermissionOverride(permission=perm, allow=allow))
    if changes:
        action = "team.permissions_changed" if set(changes) == {"overrides"} else "team.member_updated"
        audit(session, actor, action, "team_member", member.id, changes)
        emit(session, "team")
    session.flush()
    session.refresh(member)
    return member


def remove_member(session: Session, actor: Actor, member_id: int) -> None:
    member = session.get(TeamMember, member_id)
    if member is None:
        raise NotFound("Teammitglied nicht gefunden.")
    if member.role and member.role.is_owner:
        raise Forbidden("Inhaber-Konten können nicht entfernt werden.")
    if actor.member_id == member.id:
        raise Forbidden("Du kannst dich nicht selbst entfernen.")
    audit(session, actor, "team.member_removed", "team_member", member.id, {"discord_id": member.user.discord_id})
    session.delete(member)
    emit(session, "team")


def touch_activity(session: Session, member_id: Optional[int]) -> None:
    if member_id:
        m = session.get(TeamMember, member_id)
        if m:
            m.last_activity_at = utcnow()
