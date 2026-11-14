"""Ticket-System: Erstellung, Nummern, Zuweisung, interne Notizen, Schließen, Auto-Schließung."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from lbc.core.db import session_scope
from lbc.core.models import DiscordOutbox, Ticket, TicketCategory, TicketMessage, TicketTranscript, utcnow
from lbc.services import tickets as svc
from lbc.services.base import Actor, Conflict
from tests.conftest import login, make_member


def _cat_id(name="Technischer Support") -> int:
    with session_scope() as s:
        return s.scalar(select(TicketCategory.id).where(TicketCategory.name == name))


def _create(user="800000000000000001", subject="Spiel startet nicht") -> int:
    with session_scope() as s:
        t = svc.create_ticket(s, user, "Mitglied", _cat_id(), subject)
        svc.set_channel(s, t.id, str(int(user) + t.number))
        return t.id


def test_default_categories():
    with session_scope() as s:
        names = [c.name for c in svc.list_categories(s)]
    assert names == ["Allgemeiner Support", "Technischer Support", "Sonstige Anliegen"]


def test_ticket_numbers_are_unique_under_concurrency():
    barrier = threading.Barrier(8)

    def create(i):
        barrier.wait()
        with session_scope() as s:
            return svc.create_ticket(s, f"80000000000000{i:04d}", f"U{i}", _cat_id(), "x").number

    with ThreadPoolExecutor(8) as ex:
        numbers = list(ex.map(create, range(8)))
    assert sorted(numbers) == list(range(1, 9))


def test_max_open_tickets_per_user():
    for _ in range(3):
        _create()
    with session_scope() as s, pytest.raises(Conflict):
        svc.create_ticket(s, "800000000000000001", "Mitglied", _cat_id(), "zu viel")


def test_message_logging_is_idempotent_and_tracks_first_response():
    tid = _create()
    with session_scope() as s:
        ch = s.get(Ticket, tid).channel_id
        svc.log_message(s, ch, "810000000000000001", "800000000000000001", "Mitglied", "Hallo")
        svc.log_message(s, ch, "810000000000000001", "800000000000000001", "Mitglied", "Hallo")  # Duplikat
        svc.log_message(s, ch, "810000000000000002", "820000000000000001", "Supporter", "Wie kann ich helfen?",
                        is_staff=True)
    with session_scope() as s:
        assert s.scalar(select(func.count(TicketMessage.id))) == 2
        assert s.get(Ticket, tid).first_response_at is not None


def test_claim_is_atomic():
    tid = _create()
    m1 = make_member("820000000000000001", "Supporter")
    m2 = make_member("820000000000000002", "Supporter")
    barrier = threading.Barrier(2)

    def claim(mid):
        barrier.wait()
        try:
            with session_scope() as s:
                svc.claim(s, Actor(name=str(mid), member_id=mid), tid, mid)
                return "ok"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(2) as ex:
        results = list(ex.map(claim, [m1, m2]))
    assert sorted(results) == ["conflict", "ok"]
    with session_scope() as s:
        t = s.get(Ticket, tid)
        assert t.assignee_id in (m1, m2) and t.status == "in_progress"


def test_internal_notes_never_reach_discord(owner_client):
    tid = _create()
    secret = "INTERN: Nutzer wirkt verdächtig"
    r = owner_client.post(f"/api/tickets/{tid}/notes", json={"content": secret})
    assert r.status_code == 200
    owner_client.post(f"/api/tickets/{tid}/status", json={"status": "closed", "reason": "erledigt"})
    with session_scope() as s:
        payloads = [str(j.payload) for j in s.scalars(select(DiscordOutbox))]
        transcript = s.scalar(select(TicketTranscript).where(TicketTranscript.ticket_id == tid))
    assert all(secret not in p for p in payloads)
    assert secret not in transcript.content
    detail = owner_client.get(f"/api/tickets/{tid}").json()
    assert detail["notes"][0]["content"] == secret


def test_notes_require_permission(client):
    tid = _create()
    make_member("820000000000000005", None, overrides=[("dashboard.view", True)])
    login(client, "820000000000000005")
    assert client.get(f"/api/tickets/{tid}").status_code == 403
    assert client.post(f"/api/tickets/{tid}/notes", json={"content": "x"}).status_code == 403


def test_panel_workflow(owner_client):
    tid = _create()
    members = owner_client.get("/api/team/directory").json()
    owner = members[0]
    r = owner_client.post(f"/api/tickets/{tid}/assign", json={"member_id": owner["id"]})
    assert r.json()["assignee"]["id"] == owner["id"]
    assert owner_client.post(f"/api/tickets/{tid}/priority", json={"priority": "urgent"}).json()["priority"] == "urgent"
    assert owner_client.post(f"/api/tickets/{tid}/status", json={"status": "waiting"}).json()["status"] == "waiting"
    assert owner_client.post(f"/api/tickets/{tid}/reply", json={"content": "Bitte Logdatei senden"}).status_code == 200
    r = owner_client.post(f"/api/tickets/{tid}/status", json={"status": "closed", "reason": "gelöst"})
    assert r.json()["status"] == "closed"
    assert owner_client.post(f"/api/tickets/{tid}/status", json={"status": "closed"}).status_code == 409
    detail = owner_client.get(f"/api/tickets/{tid}").json()
    assert len(detail["transcripts"]) == 1
    tr = owner_client.get(f"/api/tickets/{tid}/transcripts/{detail['transcripts'][0]['id']}")
    assert tr.status_code == 200 and "TICKET #0001" in tr.text
    r = owner_client.post(f"/api/tickets/{tid}/reopen")
    assert r.json()["status"] == "in_progress" and r.json()["reopen_count"] == 1
    with session_scope() as s:
        kinds = [j.kind for j in s.scalars(select(DiscordOutbox).order_by(DiscordOutbox.id))]
    assert "ticket.send_message" in kinds and "ticket.close_channel" in kinds and "ticket.reopen_channel" in kinds
    lst = owner_client.get("/api/tickets?status=active&q=0001").json()
    assert lst["total"] == 1


def test_auto_close_warns_then_closes():
    tid = _create()
    with session_scope() as s:
        t = s.get(Ticket, tid)
        t.last_activity_at = utcnow() - timedelta(hours=60)
    with session_scope() as s:
        assert svc.auto_close_run(s) == {"warned": 1, "closed": 0}
    with session_scope() as s:
        assert svc.auto_close_run(s) == {"warned": 0, "closed": 0}  # nicht doppelt warnen
        t = s.get(Ticket, tid)
        t.last_activity_at = utcnow() - timedelta(hours=80)
        t.auto_close_warned_at = utcnow() - timedelta(hours=25)
    with session_scope() as s:
        assert svc.auto_close_run(s) == {"warned": 0, "closed": 1}
        assert s.get(Ticket, tid).status == "closed"


def test_activity_resets_auto_close_warning():
    tid = _create()
    with session_scope() as s:
        t = s.get(Ticket, tid)
        t.last_activity_at = utcnow() - timedelta(hours=60)
        svc.auto_close_run(s)
    with session_scope() as s:
        svc.log_message(s, s.get(Ticket, tid).channel_id, "810000000000000077", "800000000000000001", "M", "noch da")
    with session_scope() as s:
        assert s.get(Ticket, tid).auto_close_warned_at is None


def test_category_management(owner_client):
    r = owner_client.post("/api/tickets/categories", json={"name": "Bewerbungsfragen", "discord_category_id": "123"})
    assert r.status_code == 422
    r = owner_client.post("/api/tickets/categories", json={"name": "Bewerbungsfragen", "sort_order": 40})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert owner_client.put(f"/api/tickets/categories/{cid}", json={"name": "Fragen", "active": False}).json()["active"] is False
    assert owner_client.delete(f"/api/tickets/categories/{cid}").status_code == 200
    with session_scope() as s:
        assert s.scalar(select(DiscordOutbox).where(DiscordOutbox.kind == "ticket.refresh_panel")) is not None
