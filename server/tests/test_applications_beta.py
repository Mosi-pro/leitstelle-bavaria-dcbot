"""Bewerbungen und Beta-Tester-Verwaltung."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from lbc.core.db import session_scope
from lbc.core.models import (
    Application,
    ApplicationAnswer,
    ApplicationType,
    BetaInvitationRecipient,
    BetaTester,
    DiscordOutbox,
    utcnow,
)
from lbc.services import applications as svc
from lbc.services.base import Conflict, Invalid


def _beta_type():
    with session_scope() as s:
        t = s.scalar(select(ApplicationType).where(ApplicationType.key == "beta"))
        return svc.serialize_type(t)


def _answers(form, override=None):
    out = {}
    for q in form["questions"]:
        if q["field_type"] == "select":
            out[q["id"]] = q["options"][0]
        elif q["field_type"] == "multiselect":
            out[q["id"]] = q["options"][:2]
        elif q["field_type"] == "yesno":
            out[q["id"]] = "Ja"
        else:
            out[q["id"]] = "25" if q["max_length"] < 7 else "Antwort"
    out.update(override or {})
    return out


def _submit(discord_id="900000000000000001"):
    form = _beta_type()
    with session_scope() as s:
        return svc.submit(s, form["id"], discord_id, "Bewerber", _answers(form)).id


def test_default_types_exist():
    with session_scope() as s:
        keys = set(s.scalars(select(ApplicationType.key)))
    assert {"beta", "team"} <= keys


def test_validation_of_answers():
    form = _beta_type()
    select_q = next(q for q in form["questions"] if q["field_type"] == "select")
    with session_scope() as s, pytest.raises(Invalid):
        svc.submit(s, form["id"], "900000000000000002", "B", _answers(form, {select_q["id"]: "Amiga"}))
    text_q = next(q for q in form["questions"] if q["field_type"] == "text")
    with session_scope() as s, pytest.raises(Invalid):
        svc.submit(s, form["id"], "900000000000000002", "B", _answers(form, {text_q["id"]: ""}))
    with session_scope() as s, pytest.raises(Invalid):
        svc.submit(s, form["id"], "900000000000000002", "B", _answers(form, {text_q["id"]: "x" * 50}))


def test_duplicate_open_application_rejected():
    _submit()
    with pytest.raises(Conflict):
        _submit()


def test_accept_creates_tester_and_messages(owner_client):
    owner_client.put("/api/settings", json={"values": {"roles.beta": "910000000000000001"}})
    aid = _submit()
    r = owner_client.post(f"/api/applications/{aid}/rating", json={"score": 4, "comment": "gut"})
    assert r.status_code == 200
    owner_client.post(f"/api/applications/{aid}/rating", json={"score": 5})  # Aktualisiert statt Duplikat
    owner_client.post(f"/api/applications/{aid}/notes", json={"content": "Interne Einschätzung"})
    assert owner_client.post(f"/api/applications/{aid}/status", json={"status": "in_review"}).status_code == 200
    r = owner_client.post(f"/api/applications/{aid}/status", json={"status": "inquiry"})
    assert r.status_code == 422  # Rückfrage ohne Text
    r = owner_client.post(f"/api/applications/{aid}/status", json={"status": "inquiry", "message": "Welche GPU?"})
    assert r.status_code == 200
    r = owner_client.post(f"/api/applications/{aid}/status", json={"status": "accepted", "comment": "passt"})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert owner_client.post(f"/api/applications/{aid}/status", json={"status": "rejected"}).status_code == 409
    detail = owner_client.get(f"/api/applications/{aid}").json()
    assert detail["average_score"] == 5 and len(detail["ratings"]) == 1
    assert [h["action"] for h in detail["history"]][:2] == ["submitted", "rated"]
    assert detail["notes"][0]["content"] == "Interne Einschätzung"
    with session_scope() as s:
        tester = s.scalar(select(BetaTester))
        assert tester is not None and tester.application_id == aid and tester.status == "active"
        jobs = [(j.kind, j.payload) for j in s.scalars(select(DiscordOutbox))]
    dms = [p for k, p in jobs if k == "dm.send"]
    assert any("Welche GPU?" in p["content"] for p in dms)
    assert any("angenommen" in p["content"] for p in dms)
    assert ("member.add_role", {"discord_id": "900000000000000001", "role_id": "910000000000000001",
                                "reason": "Beta-Tester"}) in jobs
    assert all("Interne Einschätzung" not in str(p) for _, p in jobs)


def test_form_configuration(owner_client):
    r = owner_client.post("/api/applications/types", json={
        "key": "grafik", "name": "Grafik-Bewerbung", "questions": [
            {"label": "Portfolio-Link", "field_type": "text"},
            {"label": "Programme", "field_type": "multiselect", "options": ["Blender", "GIMP"]},
            {"label": "Erfahrung?", "field_type": "yesno"},
            {"label": "Bereich", "field_type": "select", "options": ["2D"]},
        ]})
    assert r.status_code == 422  # Auswahlfeld mit nur einer Option
    r = owner_client.post("/api/applications/types", json={
        "key": "grafik", "name": "Grafik-Bewerbung", "auto_assign_role": True, "accept_role_id": "910000000000000002",
        "questions": [{"label": "Portfolio-Link", "field_type": "text"},
                      {"label": "Programme", "field_type": "multiselect", "options": ["Blender", "GIMP"]}]})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    q = r.json()["questions"]
    r = owner_client.put(f"/api/applications/types/{tid}", json={**r.json(), "questions": q + [
        {"label": "Motivation", "field_type": "textarea", "required": False}]})
    assert len(r.json()["questions"]) == 3
    with session_scope() as s:
        app = svc.submit(s, tid, "900000000000000005", "Künstler", {q[0]["id"]: "https://x", q[1]["id"]: ["GIMP"]})
        assert [a.value for a in app.answers] == ["https://x", "GIMP", ""]


def test_retention_anonymizes_old_decisions():
    aid = _submit()
    with session_scope() as s:
        app = s.get(Application, aid)
        app.status = "rejected"
        app.decided_at = utcnow() - timedelta(days=200)
        app.decision_reason = "personenbezogen"
    with session_scope() as s:
        assert svc.anonymize_expired(s) == 1
    with session_scope() as s:
        app = s.get(Application, aid)
        assert app.anonymized_at is not None and app.decision_reason is None
        assert app.applicant.discord_id == "0"
        assert s.scalar(select(func.count(ApplicationAnswer.id))) == 0
        assert app.status == "rejected"  # Statistik bleibt erhalten


def test_beta_tester_management(owner_client):
    g = owner_client.post("/api/beta/groups", json={"name": "Closed Beta", "discord_role_id": "920000000000000001"})
    assert g.status_code == 200
    gid = g.json()["id"]
    owner_client.post("/api/beta/groups", json={"name": "Multiplayer-Test"})
    vid = owner_client.post("/api/versions", json={"version": "0.8.2", "status": "beta"}).json()["id"]
    r = owner_client.post("/api/beta/testers", json={"discord_id": "930000000000000001", "username": "tester1",
                                                     "group_ids": [gid], "version_ids": [vid]})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert r.json()["groups"][0]["name"] == "Closed Beta"
    assert owner_client.post("/api/beta/testers", json={"discord_id": "930000000000000001"}).status_code == 409
    r = owner_client.put(f"/api/beta/testers/{tid}", json={"status": "blocked", "notes": "Leaks"})
    assert r.json()["status"] == "blocked"
    assert any(h["action"] == "status_changed" for h in r.json()["history"])
    owner_client.put(f"/api/beta/testers/{tid}", json={"status": "active"})
    groups = owner_client.get("/api/beta/groups").json()
    assert next(x for x in groups if x["id"] == gid)["tester_count"] == 1
    r = owner_client.post("/api/beta/invitations", json={"title": "Testphase 0.8.2", "group_ids": [gid],
                                                         "version_id": vid, "message": "Hallo {name}, Version {version}!"})
    assert r.status_code == 200
    inv = owner_client.get("/api/beta/invitations").json()[0]
    assert inv["pending"] == 1 and inv["version"] == "0.8.2"
    with session_scope() as s:
        rec = s.scalar(select(BetaInvitationRecipient))
        job = s.scalar(select(DiscordOutbox).where(DiscordOutbox.kind == "beta.send_invitation"))
        assert job.payload == {"recipient_id": rec.id}
        from lbc.core.models import BetaInvitation, GameVersion
        from lbc.services.beta import render_invitation
        text = render_invitation(s.get(BetaInvitation, rec.invitation_id), s.get(BetaTester, tid), s.get(GameVersion, vid))
        assert text == "Hallo tester1, Version 0.8.2!"
    assert owner_client.delete(f"/api/beta/testers/{tid}").status_code == 200
    with session_scope() as s:
        removals = [j.payload["role_id"] for j in s.scalars(select(DiscordOutbox).where(DiscordOutbox.kind == "member.remove_role"))]
    assert "920000000000000001" in removals
