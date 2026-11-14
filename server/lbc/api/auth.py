"""Discord OAuth2 (Authorization Code Flow) – ausschließlich zur Identifikation.

Ob jemand das Panel nutzen darf, entscheidet allein die interne Teamverwaltung.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from ..core.config import get_settings
from ..core.crypto import constant_time_equals, hash_token, new_token
from ..core.db import session_scope
from ..core.models import WebSession, utcnow
from ..services import settings_service
from ..services.base import Actor, audit, record_error, upsert_discord_user
from ..services.team import find_member_by_discord_id
from .deps import SESSION_COOKIE, Principal, client_ip, get_principal

log = logging.getLogger("lbc.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])
STATE_COOKIE = "lbc_oauth_state"


class OAuthError(Exception):
    pass


def oauth_config() -> dict:
    with session_scope() as s:
        vals = settings_service.get_values(s, ["discord.client_id", "discord.client_secret", "discord.redirect_uri"])
    return {"client_id": vals["discord.client_id"], "client_secret": vals["discord.client_secret"],
            "redirect_uri": vals["discord.redirect_uri"]}


def exchange_code(code: str, cfg: dict) -> dict:
    """Tauscht den Code gegen ein Token und lädt das Discord-Profil (/users/@me)."""
    api = get_settings().discord_api_base
    try:
        with httpx.Client(timeout=10) as client:
            tok = client.post(f"{api}/oauth2/token", data={
                "client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "grant_type": "authorization_code",
                "code": code, "redirect_uri": cfg["redirect_uri"],
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
            if tok.status_code != 200:
                raise OAuthError(f"Token-Austausch fehlgeschlagen (HTTP {tok.status_code})")
            access = tok.json().get("access_token")
            me = client.get(f"{api}/users/@me", headers={"Authorization": f"Bearer {access}"})
            if me.status_code != 200:
                raise OAuthError(f"Discord-Profil konnte nicht geladen werden (HTTP {me.status_code})")
            return me.json()
    except httpx.HTTPError as exc:
        raise OAuthError(f"Discord ist nicht erreichbar: {exc}") from exc


def avatar_url(user: dict) -> str | None:
    if user.get("avatar"):
        return f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=128"
    return None


def _cookie_kwargs() -> dict:
    return {"httponly": True, "secure": get_settings().cookie_secure, "samesite": "lax", "path": "/"}


@router.get("/login")
def login() -> RedirectResponse:
    cfg = oauth_config()
    if not cfg["client_id"] or not cfg["redirect_uri"]:
        return RedirectResponse("/login?error=not_configured", status_code=302)
    state = new_token(24)
    params = {"client_id": cfg["client_id"], "response_type": "code", "redirect_uri": cfg["redirect_uri"],
              "scope": "identify", "state": state, "prompt": "none"}
    resp = RedirectResponse("https://discord.com/oauth2/authorize?" + urlencode(params), status_code=302)
    resp.set_cookie(STATE_COOKIE, state, max_age=600, **_cookie_kwargs())
    return resp


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    cookie_state = request.cookies.get(STATE_COOKIE, "")
    if error:
        return RedirectResponse("/login?error=cancelled", status_code=302)
    if not code or not state or not cookie_state or not constant_time_equals(state, cookie_state):
        return RedirectResponse("/login?error=state", status_code=302)
    cfg = oauth_config()
    try:
        profile = exchange_code(code, cfg)
    except OAuthError as exc:
        log.warning("OAuth fehlgeschlagen: %s", exc)
        with session_scope() as s:
            record_error(s, "api", f"Discord-Login fehlgeschlagen: {exc}")
        return RedirectResponse("/login?error=discord", status_code=302)

    discord_id = str(profile.get("id"))
    username = profile.get("username") or discord_id
    ip = client_ip(request)
    with session_scope() as s:
        member = find_member_by_discord_id(s, discord_id)
        actor = Actor(name=username, discord_id=discord_id, source="panel", ip=ip)
        if member is None or member.status != "active":
            audit(s, actor, "auth.denied", "discord_user", discord_id,
                  {"reason": "kein aktives Teammitglied" if member is None else f"Status {member.status}"})
            resp = RedirectResponse("/login?error=not_authorized", status_code=302)
            resp.delete_cookie(STATE_COOKIE, path="/")
            return resp
        upsert_discord_user(s, discord_id, username=username, display_name=profile.get("global_name") or None,
                            avatar_url=avatar_url(profile) or "")
        token = new_token(32)
        now = utcnow()
        s.add(WebSession(token_hash=hash_token(token), csrf_token=new_token(24), member_id=member.id,
                         expires_at=now + timedelta(hours=get_settings().session_hours), ip_address=ip,
                         user_agent=(request.headers.get("user-agent") or "")[:255]))
        member.last_login_at = now
        member.last_activity_at = now
        audit(s, Actor(name=profile.get("global_name") or username, discord_id=discord_id, member_id=member.id,
                       source="panel", ip=ip), "auth.login", "team_member", member.id)
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(STATE_COOKIE, path="/")
    resp.set_cookie(SESSION_COOKIE, token, max_age=get_settings().session_hours * 3600, **_cookie_kwargs())
    return resp


@router.get("/me")
def me(principal: Principal = Depends(get_principal)) -> dict:
    return {
        "member_id": principal.member_id, "discord_id": principal.discord_id, "username": principal.username,
        "display_name": principal.display_name, "avatar_url": principal.avatar_url, "role": principal.role,
        "is_owner": principal.is_owner, "permissions": sorted(principal.permissions), "csrf_token": principal.csrf_token,
    }


@router.post("/logout")
def logout(principal: Principal = Depends(get_principal)) -> JSONResponse:
    with session_scope() as s:
        ws = s.get(WebSession, principal.session_id)
        if ws:
            ws.revoked = True
        audit(s, principal.actor(), "auth.logout", "team_member", principal.member_id)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/sessions")
def my_sessions(principal: Principal = Depends(get_principal)) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(WebSession).where(WebSession.member_id == principal.member_id,
                                                  WebSession.revoked.is_(False), WebSession.expires_at > utcnow())
                         .order_by(WebSession.created_at.desc())).all()
        return [{"id": r.id, "created_at": r.created_at.isoformat(), "last_seen_at": r.last_seen_at.isoformat(),
                 "ip_address": r.ip_address, "user_agent": r.user_agent, "current": r.id == principal.session_id}
                for r in rows]


@router.post("/sessions/revoke-others")
def revoke_others(principal: Principal = Depends(get_principal)) -> dict:
    with session_scope() as s:
        rows = s.scalars(select(WebSession).where(WebSession.member_id == principal.member_id,
                                                  WebSession.id != principal.session_id,
                                                  WebSession.revoked.is_(False))).all()
        for r in rows:
            r.revoked = True
        audit(s, principal.actor(), "auth.sessions_revoked", "team_member", principal.member_id, {"count": len(rows)})
    return {"revoked": len(rows)}


