"""Bug-Erfassung, atomare Übernahme (auch gleichzeitig), Bearbeitung und Synchronisierung."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from lbc.core.db import session_scope
from lbc.core.models import AuditLog, Bug, BugAssignment, BugAttachment, DiscordOutbox, GameVersion
from lbc.services import bugs as svc
from lbc.services.base import Actor
from tests.conftest import OWNER_ID, login, make_member


def _forum_bug(thread_id="700000000000000001", content="Fahrzeug verschwindet.\nVersion: 0.8.2", **kw):
    with session_scope() as s:
        bug, created = svc.create_from_forum(
            s, thread_id=thread_id, forum_channel_id="710000000000000001", title=kw.get("title", "Fahrzeuge verschwinden"),
            content=content, author_discord_id="720000000000000001", author_name="Beispielnutzer",
            attachments=kw.get("attachments"), tag_names=kw.get("tags"))
        return bug.id, created


def test_parse_post():
    p = svc.parse_post("Text\nSpielversion: 0.8.2\nReproduktionsschritte:\n1. Laden\n2. Warten")
    assert p["version"] == "0.8.2"
    assert p["reproduction_steps"].startswith("1. Laden")
    assert svc.parse_post("ohne angabe", ["Grafik", "v0.9.0"])["version"] == "0.9.0"
    assert svc.parse_post("nichts")["version"] is None


def test_create_from_forum_creates_record_and_notification_job():
    with session_scope() as s:
        s.add(GameVersion(version="0.8.2", status="beta"))
    bug_id, created = _forum_bug(attachments=[{"filename": "bild.png", "url": "https://cdn.example/bild.png",
                                               "content_type": "image/png", "size": 12}])
    assert created
    with session_scope() as s:
        bug = s.get(Bug, bug_id)
        assert bug.status == "new" and bug.reporter_discord_id == "720000000000000001"
        assert bug.forum_thread_id == "700000000000000001"
        assert bug.version is not None and bug.version.version == "0.8.2"
        assert s.scalar(select(func.count(BugAttachment.id))) == 1
        job = s.scalar(select(DiscordOutbox).where(DiscordOutbox.kind == "bug.notify_developers"))
        assert job.payload == {"bug_id": bug_id} and job.idempotency_key == f"bug-notify-{bug_id}"
    assert svc.fmt_id(42) == "#0042"


def test_duplicate_forum_event_is_ignored():
    first, c1 = _forum_bug()
    second, c2 = _forum_bug()
    assert c1 and not c2 and first == second
    with session_scope() as s:
        assert s.scalar(select(func.count(Bug.id))) == 1
        assert s.scalar(select(func.count(DiscordOutbox.id)).where(DiscordOutbox.kind == "bug.notify_developers")) == 1


def test_concurrent_duplicate_forum_events():
    """Discord liefert dasselbe Ereignis parallel mehrfach → genau ein Datensatz."""
    barrier = threading.Barrier(6)

    def worker(_):
        barrier.wait()
        return _forum_bug(thread_id="700000000000000099")

    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(worker, range(6)))
    assert len({r[0] for r in results}) == 1
    assert sum(1 for r in results if r[1]) == 1
    with session_scope() as s:
        assert s.scalar(select(func.count(Bug.id))) == 1


def test_claim_first_wins_and_second_gets_message():
    bug_id, _ = _forum_bug()
    with session_scope() as s:
        result, bug = svc.claim(s, bug_id, "730000000000000001", "Dev Eins")
        assert result == svc.ClaimResult.CLAIMED and bug.status == "claimed"
    with session_scope() as s:
        result, bug = svc.claim(s, bug_id, "730000000000000002", "Dev Zwei")
        assert result == svc.ClaimResult.TAKEN
        assert bug.assignee_discord_id == "730000000000000001"
    with session_scope() as s:
        result, _ = svc.claim(s, bug_id, "730000000000000001", "Dev Eins")
        assert result == svc.ClaimResult.ALREADY_YOURS
    assert svc.ALREADY_CLAIMED_MSG == "Dieser Bug wurde bereits von einem anderen Entwickler übernommen."


def test_concurrent_claims_exactly_one_winner():
    """Zwei (bzw. zehn) Entwickler klicken gleichzeitig auf „Ich übernehme“."""
    for round_ in range(5):
        bug_id, _ = _forum_bug(thread_id=f"70000000000000{round_:04d}")
        barrier = threading.Barrier(10)

        def attempt(i):
            barrier.wait()
            with session_scope() as s:
                result, _ = svc.claim(s, bug_id, f"74000000000000{i:04d}", f"Dev {i}")
                return result

        with ThreadPoolExecutor(10) as ex:
            results = list(ex.map(attempt, range(10)))
        assert results.count(svc.ClaimResult.CLAIMED) == 1, results
        assert results.count(svc.ClaimResult.TAKEN) == 9
        with session_scope() as s:
            bug = s.get(Bug, bug_id)
            assert bug.status == "claimed" and bug.assignee_discord_id is not None
            assigns = s.scalars(select(BugAssignment).where(BugAssignment.bug_id == bug_id)).all()
            assert len(assigns) == 1 and assigns[0].developer_discord_id == bug.assignee_discord_id
            assert s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "bugs.claimed",
                                                                  AuditLog.object_id == str(bug_id))) == 1


def test_status_handover_and_sync_jobs():
    bug_id, _ = _forum_bug()
    actor = Actor(name="Dev Eins", discord_id="730000000000000001")
    with session_scope() as s:
        svc.claim(s, bug_id, "730000000000000001", "Dev Eins")
        svc.set_dev_message(s, bug_id, "750000000000000001", "760000000000000001")
    with session_scope() as s:
        svc.set_status(s, actor, bug_id, "in_progress")
    with session_scope() as s:
        svc.handover(s, actor, bug_id, "730000000000000002", "Dev Zwei", note="Bitte übernehmen")
    with session_scope() as s:
        bug = s.get(Bug, bug_id)
        assert bug.assignee_discord_id == "730000000000000002"
        hist = s.scalars(select(BugAssignment).where(BugAssignment.bug_id == bug_id).order_by(BugAssignment.id)).all()
        assert [h.kind for h in hist] == ["claim", "handover"]
        assert hist[0].ended_at is not None and hist[1].ended_at is None
        # Eine Sync-Nachricht pro Bug (idempotent) – nicht mehrere parallele
        assert s.scalar(select(func.count(DiscordOutbox.id)).where(DiscordOutbox.kind == "bug.sync_message")) == 1
        assert s.scalar(select(func.count(DiscordOutbox.id)).where(DiscordOutbox.kind == "bug.dm_handover")) == 1
    with session_scope() as s:
        svc.set_status(s, actor, bug_id, "fixed", "Behoben in 0.8.3")
    with session_scope() as s:
        bug = s.get(Bug, bug_id)
        assert bug.status == "fixed" and bug.resolved_at is not None
        assert s.scalar(select(DiscordOutbox).where(DiscordOutbox.kind == "bug.thread_message")) is not None
        actions = set(s.scalars(select(AuditLog.action).where(AuditLog.object_id == str(bug_id))))
        assert {"bugs.reported", "bugs.claimed", "bugs.status_changed", "bugs.developer_changed"} <= actions


def test_status_requires_assignee():
    bug_id, _ = _forum_bug()
    import pytest
    with session_scope() as s, pytest.raises(svc.Conflict):
        svc.set_status(s, Actor(name="x"), bug_id, "in_progress")


def test_panel_permissions_for_bugs(client):
    bug_id, _ = _forum_bug()
    make_member("730000000000000001", "Developer", username="dev1")
    make_member("730000000000000002", "Developer", username="dev2")
    make_member("730000000000000003", "Supporter", username="support")
    login(client, "730000000000000003")
    assert client.get("/api/bugs").status_code == 403
    login(client, "730000000000000001")
    r = client.post(f"/api/bugs/{bug_id}/claim")
    assert r.status_code == 200 and r.json()["assignee"]["discord_id"] == "730000000000000001"
    login(client, "730000000000000002")
    r = client.post(f"/api/bugs/{bug_id}/claim")
    assert r.status_code == 409 and r.json()["detail"] == svc.ALREADY_CLAIMED_MSG
    # Developer-Rolle hat bugs.manage → darf bearbeiten; ohne bugs.manage nur eigener Bug
    make_member("730000000000000004", None, overrides=[("bugs.view", True), ("bugs.claim", True)], username="junior")
    login(client, "730000000000000004")
    assert client.post(f"/api/bugs/{bug_id}/status", json={"status": "fixed"}).status_code == 403
    login(client, "730000000000000001")
    detail = client.get(f"/api/bugs/{bug_id}").json()
    assert detail["can_edit"] and detail["thread_url"] == "https://discord.com/channels/300000000000000001/700000000000000001"
    r = client.post(f"/api/bugs/{bug_id}/handover", json={"discord_id": "730000000000000003"})
    assert r.status_code == 422  # Empfänger ohne bugs.claim
    r = client.post(f"/api/bugs/{bug_id}/handover", json={"discord_id": "730000000000000002", "note": "Urlaub"})
    assert r.status_code == 200 and r.json()["assignee"]["discord_id"] == "730000000000000002"
    r = client.post(f"/api/bugs/{bug_id}/comments", json={"content": "interne Notiz", "internal": True})
    assert r.status_code == 200


def test_bug_versions_link(owner_client):
    r = owner_client.post("/api/versions", json={"version": "0.8.2", "status": "beta", "name": "Herbst-Update"})
    assert r.status_code == 200
    vid = r.json()["id"]
    bug_id, _ = _forum_bug()  # Version im Text "0.8.2" → automatisch verknüpft
    detail = owner_client.get(f"/api/versions/{vid}").json()
    assert detail["bug_total"] == 1 and detail["bugs"][0]["id"] == bug_id
    r = owner_client.put(f"/api/bugs/{bug_id}", json={"fixed_in_version_id": vid, "priority": "high"})
    assert r.json()["fixed_in_version"]["version"] == "0.8.2"
    assert owner_client.delete(f"/api/versions/{vid}").status_code == 409


def test_bug_created_before_version_is_linked_later(owner_client):
    bug_id, _ = _forum_bug(content="Version: 0.9.0")
    with session_scope() as s:
        assert s.get(Bug, bug_id).version_id is None
    vid = owner_client.post("/api/versions", json={"version": "0.9.0"}).json()["id"]
    with session_scope() as s:
        assert s.get(Bug, bug_id).version_id == vid


_ = OWNER_ID
