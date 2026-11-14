"""Versionsverwaltung des Spiels Leitstelle Bavaria."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.models import Bug, GameVersion, TeamMember, TestGroup, VersionDeveloper, VersionTestGroup
from .base import Actor, Conflict, Invalid, NotFound, audit, emit

STATUSES = ("development", "internal_test", "beta", "released", "archived")
STATUS_LABELS = {"development": "In Entwicklung", "internal_test": "Interner Test", "beta": "Beta",
                 "released": "Veröffentlicht", "archived": "Archiviert"}
VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?([-.][0-9A-Za-z.]+)?$")


def _sort_key(v: str) -> tuple:
    parts = re.split(r"[.\-]", v)
    return tuple(int(p) if p.isdigit() else -1 for p in parts)


def list_versions(session: Session) -> list[GameVersion]:
    return sorted(session.scalars(select(GameVersion)), key=lambda v: _sort_key(v.version), reverse=True)


def save(session: Session, actor: Actor, data: dict, version_id: Optional[int] = None) -> GameVersion:
    number = (data.get("version") or "").strip()
    if not VERSION_RE.match(number):
        raise Invalid("Ungültige Versionsnummer (Beispiel: 0.8.2).")
    status = data.get("status") or "development"
    if status not in STATUSES:
        raise Invalid("Ungültiger Versionsstatus.")
    if session.scalar(select(GameVersion).where(GameVersion.version == number, GameVersion.id != (version_id or 0))):
        raise Conflict("Diese Versionsnummer existiert bereits.")
    rel = data.get("release_date")
    if rel:
        try:
            rel = date.fromisoformat(str(rel)[:10])
        except ValueError:
            raise Invalid("Ungültiges Veröffentlichungsdatum.") from None
    if version_id:
        v = session.get(GameVersion, version_id)
        if v is None:
            raise NotFound("Version nicht gefunden.")
        changes = {}
        if v.status != status:
            changes["status"] = [v.status, status]
        if v.version != number:
            changes["version"] = [v.version, number]
        action = "versions.updated"
    else:
        v = GameVersion(version=number)
        session.add(v)
        changes = {"version": number, "status": status}
        action = "versions.created"
    v.version = number
    v.name = data.get("name") or None
    v.status = status
    v.release_date = rel or None
    v.description = data.get("description") or None
    if "changelog" in data:
        if (data.get("changelog") or None) != v.changelog:
            changes["changelog"] = True
        v.changelog = data.get("changelog") or None
    session.flush()
    if "developer_ids" in data:
        ids = {int(i) for i in data["developer_ids"] or []}
        if ids and session.scalar(select(func.count(TeamMember.id)).where(TeamMember.id.in_(ids))) != len(ids):
            raise NotFound("Mindestens ein Entwickler existiert nicht.")
        session.execute(delete(VersionDeveloper).where(VersionDeveloper.version_id == v.id))
        for i in ids:
            session.add(VersionDeveloper(version_id=v.id, member_id=i))
    if "group_ids" in data:
        ids = {int(i) for i in data["group_ids"] or []}
        if ids and session.scalar(select(func.count(TestGroup.id)).where(TestGroup.id.in_(ids))) != len(ids):
            raise NotFound("Mindestens eine Testgruppe existiert nicht.")
        session.execute(delete(VersionTestGroup).where(VersionTestGroup.version_id == v.id))
        for i in ids:
            session.add(VersionTestGroup(version_id=v.id, group_id=i))
    # Bugs mit passender Freitext-Version automatisch verknüpfen
    session.execute(Bug.__table__.update().where(Bug.version_id.is_(None), Bug.version_text == v.version)
                    .values(version_id=v.id))
    audit(session, actor, action, "game_version", v.id, changes)
    emit(session, "versions", v.id)
    return v


def delete_version(session: Session, actor: Actor, version_id: int) -> None:
    v = session.get(GameVersion, version_id)
    if v is None:
        raise NotFound("Version nicht gefunden.")
    bug_count = session.scalar(select(func.count(Bug.id)).where((Bug.version_id == v.id) | (Bug.fixed_in_version_id == v.id)))
    if bug_count:
        raise Conflict(f"Der Version sind noch {bug_count} Bugs zugeordnet. Archiviere sie stattdessen.")
    audit(session, actor, "versions.deleted", "game_version", v.id, {"version": v.version})
    session.delete(v)
    emit(session, "versions")


def assign_bugs(session: Session, actor: Actor, version_id: int, bug_ids: list[int], field: str = "version_id") -> int:
    if field not in ("version_id", "fixed_in_version_id"):
        raise Invalid("Ungültiges Feld.")
    v = session.get(GameVersion, version_id)
    if v is None:
        raise NotFound("Version nicht gefunden.")
    from . import bugs as bug_service
    count = 0
    for bid in bug_ids:
        bug_service.update_bug(session, actor, int(bid), {field: v.id})
        count += 1
    return count


def serialize(v: GameVersion, session: Session, detail: bool = False) -> dict:
    bug_counts = dict(session.execute(select(Bug.status, func.count(Bug.id)).where(Bug.version_id == v.id)
                                      .group_by(Bug.status)).all())
    devs = session.execute(select(TeamMember).join(VersionDeveloper, VersionDeveloper.member_id == TeamMember.id)
                           .where(VersionDeveloper.version_id == v.id)).scalars().all()
    groups = session.execute(select(TestGroup.id, TestGroup.name)
                             .join(VersionTestGroup, VersionTestGroup.group_id == TestGroup.id)
                             .where(VersionTestGroup.version_id == v.id)).all()
    data = {
        "id": v.id, "version": v.version, "name": v.name, "status": v.status,
        "release_date": v.release_date.isoformat() if v.release_date else None, "description": v.description,
        "changelog": v.changelog if detail else None, "bug_counts": bug_counts,
        "bug_total": sum(bug_counts.values()),
        "developers": [{"id": d.id, "username": d.user.username, "display_name": d.user.display_name} for d in devs],
        "groups": [{"id": g, "name": n} for g, n in groups],
        "created_at": v.created_at.isoformat(), "updated_at": v.updated_at.isoformat(),
    }
    if detail:
        from . import bugs as bug_service
        bugs = session.scalars(select(Bug).where((Bug.version_id == v.id) | (Bug.fixed_in_version_id == v.id))
                               .order_by(Bug.id.desc())).all()
        data["bugs"] = [bug_service.serialize(b) for b in bugs]
    return data
