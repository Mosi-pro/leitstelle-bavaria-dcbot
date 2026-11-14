"""Discord OAuth2-Login, Sitzungen, CSRF und serverseitige Berechtigungsprüfung."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from lbc.api import auth as auth_mod
from lbc.core.db import session_scope
from lbc.core.models import AuditLog, TeamMember, WebSession, utcnow
from lbc.core.permissions import ALL_PERMISSIONS, effective_permissions
from tests.conftest import OWNER_ID, login, make_member


def _oauth_roundtrip(client, monkeypatch, profile: dict):
    r = client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    qs = parse_qs(url.query)
    assert url.netloc == "discord.com"
    assert qs["client_id"] == ["200000000000000001"]
    assert qs["scope"] == ["identify"]
    state = qs["state"][0]
    seen = {}

    def fake_exchange(code, cfg):
        seen["code"], seen["cfg"] = code, cfg
        return profile

    monkeypatch.setattr(auth_mod, "exchange_code", fake_exchange)
    r = client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    return r, seen


def test_oauth_login_for_team_member(client, monkeypatch):
    r, seen = _oauth_roundtrip(client, monkeypatch, {"id": OWNER_ID, "username": "inhaber", "global_name": "Inhaber",
                                                     "avatar": "abc"})
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert seen["code"] == "abc" and seen["cfg"]["client_secret"] == "client-secret-for-tests"
    assert "lbc_session" in r.cookies or "lbc_session" in client.cookies
    me = client.get("/api/auth/me").json()
    assert me["discord_id"] == OWNER_ID
    assert me["is_owner"] is True
    assert set(me["permissions"]) == set(ALL_PERMISSIONS)
    assert me["avatar_url"].startswith("https://cdn.discordapp.com/avatars/")
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "auth.login")) is not None


def test_oauth_rejects_normal_discord_user(client, monkeypatch):
    r, _ = _oauth_roundtrip(client, monkeypatch, {"id": "555555555555555555", "username": "fremd"})
    assert r.status_code == 302
    assert r.headers["location"] == "/login?error=not_authorized"
    assert client.get("/api/auth/me").status_code == 401
    with session_scope() as s:
        denied = s.scalar(select(AuditLog).where(AuditLog.action == "auth.denied"))
        assert denied.actor_discord_id == "555555555555555555"


def test_oauth_state_mismatch_rejected(client, monkeypatch):
    client.get("/api/auth/login", follow_redirects=False)
    monkeypatch.setattr(auth_mod, "exchange_code", lambda c, cfg: pytest.fail("darf nicht aufgerufen werden"))
    r = client.get("/api/auth/callback?code=abc&state=manipuliert", follow_redirects=False)
    assert r.headers["location"] == "/login?error=state"


def test_oauth_discord_unreachable(client, monkeypatch):
    client.get("/api/auth/login", follow_redirects=False)
    state = client.cookies.get(auth_mod.STATE_COOKIE)

    def boom(code, cfg):
        raise auth_mod.OAuthError("Discord ist nicht erreichbar: timeout")
    monkeypatch.setattr(auth_mod, "exchange_code", boom)
    r = client.get(f"/api/auth/callback?code=x&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login?error=discord"


def test_exchange_code_with_mocked_discord(monkeypatch):
    """Der echte Token-Austausch gegen einen gemockten Discord-Endpunkt (httpx MockTransport)."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            body = parse_qs(request.content.decode())
            assert body["grant_type"] == ["authorization_code"] and body["code"] == ["code123"]
            return httpx.Response(200, json={"access_token": "tok", "token_type": "Bearer"})
        if request.url.path.endswith("/users/@me"):
            assert request.headers["authorization"] == "Bearer tok"
            return httpx.Response(200, json={"id": "42", "username": "x"})
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert auth_mod.exchange_code("code123", {"client_id": "1", "client_secret": "s", "redirect_uri": "https://x"}) == \
        {"id": "42", "username": "x"}

    def bad(request):
        return httpx.Response(400, json={"error": "invalid_grant"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(bad), **kw))
    with pytest.raises(auth_mod.OAuthError):
        auth_mod.exchange_code("x", {"client_id": "1", "client_secret": "s", "redirect_uri": "https://x"})


def test_csrf_required_for_mutations(client):
    login(client, OWNER_ID)
    csrf = client.headers.pop("X-CSRF-Token")
    r = client.post("/api/tickets/categories", json={"name": "Test"})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    r = client.post("/api/tickets/categories", json={"name": "Test"}, headers={"X-CSRF-Token": "falsch"})
    assert r.status_code == 403
    r = client.post("/api/tickets/categories", json={"name": "Test"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200


def test_logout_revokes_session(client):
    login(client, OWNER_ID)
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_rejected(client):
    login(client, OWNER_ID)
    with session_scope() as s:
        for ws in s.scalars(select(WebSession)):
            ws.expires_at = utcnow() - timedelta(minutes=1)
    assert client.get("/api/auth/me").status_code == 401


def test_suspended_member_loses_access_immediately(client):
    make_member("400000000000000001", "Supporter")
    login(client, "400000000000000001")
    assert client.get("/api/tickets").status_code == 200
    with session_scope() as s:
        m = s.scalar(select(TeamMember).where(TeamMember.user.has(discord_id="400000000000000001")))
        m.status = "suspended"
    assert client.get("/api/tickets").status_code == 401


def test_role_permissions_enforced_server_side(client):
    make_member("400000000000000002", "Supporter")
    login(client, "400000000000000002")
    assert client.get("/api/tickets").status_code == 200
    assert client.get("/api/team/members").status_code == 403
    assert client.get("/api/audit").status_code == 403
    assert client.get("/api/settings").status_code == 403
    assert client.post("/api/team/members", json={"discord_id": "400000000000000099"}).status_code == 403


def test_individual_overrides(client):
    make_member("400000000000000003", "Supporter", overrides=[("team.view", True), ("tickets.view", False)])
    login(client, "400000000000000003")
    assert client.get("/api/team/members").status_code == 200
    assert client.get("/api/tickets").status_code == 403


def test_effective_permissions_function():
    assert effective_permissions({"a.b"}, True, {}) == set(ALL_PERMISSIONS)
    perms = effective_permissions({"tickets.view", "unbekannt"}, False, {"logs.view": True, "tickets.view": False})
    assert perms == {"logs.view"}


def test_member_without_role_has_no_rights(client):
    make_member("400000000000000004", None)
    login(client, "400000000000000004")
    assert client.get("/api/auth/me").json()["permissions"] == []
    assert client.get("/api/dashboard").status_code == 403


def test_non_owner_cannot_modify_owner(client):
    make_member("400000000000000005", "Administration")
    login(client, "400000000000000005")
    members = client.get("/api/team/members").json()
    owner = next(m for m in members if m["discord_id"] == OWNER_ID)
    r = client.put(f"/api/team/members/{owner['id']}", json={"status": "suspended"})
    assert r.status_code == 403
    roles = client.get("/api/team/roles").json()
    owner_role = next(r for r in roles if r["is_owner"])
    r = client.post("/api/team/members", json={"discord_id": "400000000000000077", "role_id": owner_role["id"]})
    assert r.status_code == 403


def test_team_management_and_audit(owner_client):
    roles = owner_client.get("/api/team/roles").json()
    assert {r["name"] for r in roles} >= {"Inhaber", "Administration", "Projektleitung", "Developer", "Supportleitung",
                                         "Supporter", "Beta-Team"}
    r = owner_client.post("/api/team/roles", json={"name": "Grafik", "permissions": ["dashboard.view", "bugs.view"]})
    assert r.status_code == 200, r.text
    role_id = r.json()["id"]
    r = owner_client.post("/api/team/members", json={"discord_id": "400000000000000010", "role_id": role_id})
    assert r.status_code == 200
    mid = r.json()["id"]
    assert set(r.json()["permissions"]) == {"dashboard.view", "bugs.view"}
    r = owner_client.put(f"/api/team/members/{mid}", json={"overrides": [{"permission": "logs.view", "allow": True}]})
    assert "logs.view" in r.json()["permissions"]
    assert owner_client.post("/api/team/members", json={"discord_id": "400000000000000010"}).status_code == 409
    assert owner_client.post("/api/team/members", json={"discord_id": "abc"}).status_code == 422
    assert owner_client.delete(f"/api/team/roles/{role_id}").status_code == 409
    logs = owner_client.get("/api/audit?action=team.").json()["items"]
    actions = {entry["action"] for entry in logs}
    assert {"team.role_created", "team.member_added", "team.permissions_changed"} <= actions
    entry = next(e for e in logs if e["action"] == "team.member_added")
    assert entry["actor_discord_id"] == OWNER_ID and entry["object_type"] == "team_member"


def test_audit_logs_are_read_only(owner_client):
    assert owner_client.delete("/api/audit/1").status_code in (404, 405)
    assert owner_client.put("/api/audit/1", json={}).status_code in (404, 405)


def test_secrets_never_returned(owner_client):
    r = owner_client.put("/api/settings", json={"values": {"discord.bot_token": "SUPER-GEHEIMES-TOKEN",
                                                            "roles.developer": "123456789012345678"}})
    assert r.status_code == 200, r.text
    assert r.json()["bot_restart"] is True
    body = owner_client.get("/api/settings").text
    assert "SUPER-GEHEIMES-TOKEN" not in body
    assert "client-secret-for-tests" not in body
    with session_scope() as s:
        from lbc.core.models import SystemSetting
        row = s.get(SystemSetting, "discord.bot_token")
        assert row.is_secret and "SUPER-GEHEIMES-TOKEN" not in row.value
        from lbc.services import settings_service
        assert settings_service.get_value(s, "discord.bot_token") == "SUPER-GEHEIMES-TOKEN"
    r = owner_client.put("/api/settings", json={"values": {"roles.developer": "abc"}})
    assert r.status_code == 422
