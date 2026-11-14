"""Authentifizierung, CSRF-Schutz und serverseitige Berechtigungsprüfung für alle API-Endpunkte."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.crypto import constant_time_equals, hash_token
from ..core.db import session_scope
from ..core.models import TeamMember, WebSession, utcnow
from ..services.base import Actor
from ..services.team import member_permissions

SESSION_COOKIE = "lbc_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    member_id: int
    session_id: int
    discord_id: str
    username: str
    display_name: Optional[str]
    avatar_url: Optional[str]
    role: Optional[str]
    is_owner: bool
    csrf_token: str
    permissions: set[str] = field(default_factory=set)
    ip: Optional[str] = None

    @property
    def name(self) -> str:
        return self.display_name or self.username

    def actor(self) -> Actor:
        return Actor(name=self.name, discord_id=self.discord_id, member_id=self.member_id, source="panel", ip=self.ip)

    def has(self, perm: str) -> bool:
        return perm in self.permissions


def client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else None)


def load_principal(token: str, ip: Optional[str] = None) -> Optional[Principal]:
    with session_scope() as s:
        ws = s.scalar(select(WebSession).where(WebSession.token_hash == hash_token(token)))
        now = utcnow()
        if ws is None or ws.revoked or ws.expires_at <= now:
            return None
        member = s.get(TeamMember, ws.member_id)
        if member is None or member.status != "active":
            return None
        if ws.last_seen_at < now - timedelta(minutes=1):
            ws.last_seen_at = now
            member.last_activity_at = now
        return Principal(
            member_id=member.id, session_id=ws.id, discord_id=member.user.discord_id, username=member.user.username,
            display_name=member.user.display_name, avatar_url=member.user.avatar_url,
            role=member.role.name if member.role else None, is_owner=bool(member.role and member.role.is_owner),
            csrf_token=ws.csrf_token, permissions=member_permissions(member), ip=ip,
        )


def get_principal(request: Request) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Nicht angemeldet.")
    principal = load_principal(token, client_ip(request))
    if principal is None:
        raise HTTPException(401, "Sitzung abgelaufen oder ungültig. Bitte erneut anmelden.")
    if request.method not in SAFE_METHODS:
        header = request.headers.get("x-csrf-token", "")
        if not header or not constant_time_equals(header, principal.csrf_token):
            raise HTTPException(403, "CSRF-Prüfung fehlgeschlagen. Bitte die Seite neu laden.")
    return principal


def require(*perms: str, any_of: bool = False) -> Callable[..., Principal]:
    """Dependency: prüft serverseitig die Berechtigungen des angemeldeten Teammitglieds."""

    def dep(principal: Principal = Depends(get_principal)) -> Principal:
        ok = any(principal.has(p) for p in perms) if any_of else all(principal.has(p) for p in perms)
        if not ok:
            raise HTTPException(403, "Keine Berechtigung für diese Aktion.")
        return principal

    return dep


def paginate(session: Session, query, page: int, page_size: int, serializer: Callable[[Any], dict]) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = session.scalars(query.offset((page - 1) * page_size).limit(page_size)).unique().all()
    return {"items": [serializer(i) for i in items], "total": total, "page": page, "page_size": page_size}
