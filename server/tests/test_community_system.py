"""Community, Controlling/Reports, Automatisierungen, WebSocket, Backup/Restore."""
from __future__ import annotations

import os
import shutil
import tarfile
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from lbc.core.db import session_scope
from lbc.core.models import Bug, ChangeEvent, DiscordOutbox, ModerationAction, Poll, Report, SystemStatus, Warning, utcnow
from lbc.services import community as svc
from lbc.services import reports as report_svc
from lbc.services.base import Actor, Conflict, emit
from tests.conftest import OWNER_ID, login


def test_moderation_from_panel_and_warn_threshold(owner_client):
    r = owner_client.post("/api/community/moderation", json={"discord_id": "950000000000000001", "action": "ban",
                                                             "reason": "Spam"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    for i in range(3):
        r = owner_client.post("/api/community/moderation", json={"discord_id": "950000000000000002", "action": "warn",
                                                                 "reason": f"Regelverstoß {i}"})
        assert r.status_code == 200
    with session_scope() as s:
        kinds = [j.kind for j in s.scalars(select(DiscordOutbox))]
        assert "moderation.execute" in kinds
        assert kinds.count("dm.send") == 3
        auto = s.scalar(select(ModerationAction).where(ModerationAction.action == "timeout"))
        assert auto is not None and auto.moderator_name == "Auto-Moderation" and auto.duration_minutes == 60
    hist = owner_client.get("/api/community/moderation/user/950000000000000002").json()
    assert len([w for w in hist["warnings"] if w["active"]]) == 3
    wid = hist["warnings"][0]["id"]
    assert owner_client.post(f"/api/community/warnings/{wid}/revoke").json()["active"] is False
    assert owner_client.post(f"/api/community/warnings/{wid}/revoke").status_code == 409
    assert owner_client.post("/api/community/moderation", json={"discord_id": "950000000000000002", "action": "timeout",
                                                                "reason": "x", "duration_minutes": 0}).status_code == 422
    assert owner_client.post("/api/community/moderation", json={"discord_id": "1", "action": "warn",
                                                                "reason": "x"}).status_code == 422


def test_automod_rules():
    rules = [{"name": "Wörter", "rule_type": "word_filter", "config": {"words": ["spam"]}, "enabled": True},
             {"name": "Einladungen", "rule_type": "invite_links", "config": {}, "enabled": True},
             {"name": "Erwähnungen", "rule_type": "mention_spam", "config": {"max_mentions": 3}, "enabled": True},
             {"name": "Caps", "rule_type": "caps", "config": {"min_length": 10, "max_ratio": 0.8}, "enabled": True}]
    assert svc.evaluate_rules(rules, "Das ist SPAM hier", 0)["name"] == "Wörter"
    assert svc.evaluate_rules(rules, "Spamming ist erlaubt", 0) is None
    assert svc.evaluate_rules(rules, "komm auf discord.gg/abc", 0)["name"] == "Einladungen"
    assert svc.evaluate_rules(rules, "hallo", 5)["name"] == "Erwähnungen"
    assert svc.evaluate_rules(rules, "WARUM GEHT DAS NICHT", 0)["name"] == "Caps"


def test_announcements_and_templates(owner_client):
    r = owner_client.post("/api/community/announcements", json={"content": "x", "color": "rot"})
    assert r.status_code == 422
    r = owner_client.post("/api/community/announcements", json={"content": "x"})
    assert r.status_code == 422  # kein Channel konfiguriert
    owner_client.put("/api/settings", json={"values": {"channels.announcements": "960000000000000001"}})
    r = owner_client.post("/api/community/announcements", json={"title": "Update 0.8.2", "content": "Neu!",
                                                                "color": "#3366ff", "publish": False})
    aid = r.json()["id"]
    assert r.json()["status"] == "draft"
    assert owner_client.post(f"/api/community/announcements/{aid}/publish").json()["status"] == "queued"
    assert owner_client.post(f"/api/community/announcements/{aid}/publish").status_code == 409
    r = owner_client.post("/api/community/templates", json={"name": "Wartung", "title": "Wartung", "content": "Heute"})
    assert r.status_code == 200
    assert owner_client.get("/api/community/templates").json()[0]["name"] == "Wartung"


def test_poll_voting():
    with session_scope() as s:
        poll = svc.create_poll(s, Actor(name="Team"), {"question": "Welche Karte?", "options": ["Nord", "Süd"],
                                                        "channel_id": "960000000000000002", "duration_hours": 24}, True)
        pid, opts = poll.id, [o.id for o in poll.options]
    with session_scope() as s:
        assert svc.vote(s, pid, opts[0], "970000000000000001")[0] is False  # noch nicht veröffentlicht
        s.get(Poll, pid).status = "open"
    with session_scope() as s:
        assert svc.vote(s, pid, opts[0], "970000000000000001")[0]
        assert svc.vote(s, pid, opts[1], "970000000000000001")[0]  # Einfachauswahl: ersetzt
        assert svc.vote(s, pid, opts[1], "970000000000000002")[0]
    with session_scope() as s:
        res = svc.poll_results(s, s.get(Poll, pid))
        assert [o["votes"] for o in res["options"]] == [0, 2] and res["voters"] == 2
    with session_scope() as s:
        ok, msg = svc.vote(s, pid, opts[1], "970000000000000002")  # erneut klicken = zurücknehmen
        assert ok and "zurückgenommen" in msg
    with session_scope() as s:
        s.get(Poll, pid).ends_at = utcnow() - timedelta(minutes=1)
    with session_scope() as s:
        assert svc.close_expired_polls(s) == 1
    with session_scope() as s:
        with pytest.raises(Conflict):
            svc.close_poll(s, Actor(name="x"), pid)


def test_dashboard_uses_real_data(owner_client):
    with session_scope() as s:
        s.add(SystemStatus(component="bot", status="online", details={"member_count": 321}, last_seen=utcnow()))
        s.add(Bug(title="A", description="", status="new"))
        s.add(Bug(title="B", description="", status="fixed", resolved_at=utcnow()))
    d = owner_client.get("/api/dashboard").json()
    assert d["community"]["member_count"] == 321
    assert d["bugs"]["new"] == 1 and d["bugs"]["fixed"] == 1
    assert d["system"]["bot"]["online"] is True and d["system"]["database"]["connected"] is True
    assert d["team"]["active_members"] == 1
    c = owner_client.get("/api/controlling?days=30").json()
    assert len(c["timeline"]) == 30 and c["timeline"][-1]["bugs"] == 2
    assert c["bugs"]["fixed_in_period"] == 1


def test_weekly_report_generation(owner_client):
    with session_scope() as s:
        start, end = report_svc.week_bounds(utcnow())
        s.add(Bug(title="A", description="", status="new", created_at=start + timedelta(days=1)))
    r = owner_client.post("/api/reports", json={})
    assert r.status_code == 200
    rep = r.json()
    assert rep["content"].startswith("LEITSTELLE BAVARIA – WOCHENREPORT")
    assert "1 neue Bugs eingegangen." in rep["content"]
    assert owner_client.post("/api/reports", json={}).status_code == 409
    assert owner_client.post(f"/api/reports/{rep['id']}/post").status_code == 409  # kein Channel
    owner_client.put("/api/settings", json={"values": {"channels.reports": "980000000000000001"}})
    assert owner_client.post(f"/api/reports/{rep['id']}/post").status_code == 200
    with session_scope() as s:
        assert s.scalar(select(DiscordOutbox).where(DiscordOutbox.kind == "report.post")) is not None


def test_weekly_due_logic():
    with session_scope() as s:
        monday = datetime(2026, 11, 16, 9, 0)
        assert report_svc.weekly_due(s, monday)
        assert not report_svc.weekly_due(s, monday.replace(hour=7))
        assert not report_svc.weekly_due(s, monday + timedelta(days=1))


def test_automation_run_and_toggle(owner_client):
    autos = owner_client.get("/api/system/automations").json()
    assert {a["key"] for a in autos} >= {"ticket_auto_close", "weekly_report", "retention_cleanup"}
    r = owner_client.post("/api/system/automations/retention_cleanup/run")
    assert r.status_code == 200 and "applications_anonymized" in r.json()["result"]
    r = owner_client.put("/api/system/automations/ticket_auto_close", json={"enabled": False, "interval_minutes": 30})
    assert r.json()["enabled"] is False and r.json()["interval_minutes"] == 30
    a = next(x for x in owner_client.get("/api/system/automations").json() if x["key"] == "retention_cleanup")
    assert a["last_status"] == "ok"


def test_notifications_visibility(client):
    from tests.conftest import make_member
    make_member("990000000000000001", "Supporter")
    with session_scope() as s:
        from lbc.services.base import notify
        notify(s, "bug", "Neuer Bug", permission="bugs.view")
        notify(s, "ticket", "Neues Ticket", permission="tickets.view")
    login(client, "990000000000000001")
    data = client.get("/api/notifications").json()
    assert [n["title"] for n in data["items"]] == ["Neues Ticket"] and data["unread"] == 1
    client.post("/api/notifications/read", json={})
    assert client.get("/api/notifications").json()["unread"] == 0


def test_env_file_writer(tmp_path):
    from lbc.api.routes_system import write_env_values
    p = tmp_path / ".env"
    p.write_text("# Kommentar\nDB_HOST=alt\nDB_PASSWORD=x\nOTHER=1\n")
    write_env_values(str(p), {"DB_HOST": "neu.example", "DB_PASSWORD": "pa ss'wort", "DB_SSL_MODE": "required"})
    text = p.read_text()
    assert "DB_HOST=neu.example" in text and "OTHER=1" in text and "# Kommentar" in text
    assert "DB_SSL_MODE=required" in text
    assert oct(p.stat().st_mode)[-3:] == "600"
    from dotenv import dotenv_values
    assert dotenv_values(p)["DB_PASSWORD"] == "pa ss'wort"


# ---------------------------------------------------------------------------- WebSocket
def test_websocket_live_updates_and_reconnect():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from lbc.api.main import create_app
    app = create_app(with_lifespan=True)
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/ws") as ws:
                ws.receive_text()
        login(c, OWNER_ID)
        for attempt in range(2):  # Verbindung trennen und erneut verbinden
            with c.websocket_connect("/api/ws", headers={"origin": "http://testserver"}) as ws:
                assert ws.receive_json()["type"] == "hello"
                time.sleep(2)
                with session_scope() as s:
                    emit(s, "bugs", 1)
                msg = ws.receive_json()
                while msg.get("type") != "change":
                    msg = ws.receive_json()
                assert "bugs" in msg["topics"]
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/ws", headers={"origin": "https://angreifer.example"}) as ws:
                ws.receive_text()


# ---------------------------------------------------------------------------- Backup / Restore
needs_dump = pytest.mark.skipif(not (shutil.which("mariadb-dump") or shutil.which("mysqldump")),
                                reason="MySQL-Client nicht installiert")


@needs_dump
def test_backup_and_restore(tmp_path):
    from lbc.core.config import get_settings
    from lbc.tools import backup
    env = tmp_path / "config.env"
    env.write_text("DB_HOST=x\nAPP_SECRET_KEY=y\n")
    with session_scope() as s:
        s.add(Bug(title="Vor dem Backup", description="", status="new"))
    path = backup.create_backup(str(tmp_path / "backups"), get_settings(), str(env))
    assert oct(path.stat().st_mode)[-3:] == "600"
    info = backup.verify_backup(str(path))
    assert info["has_config"] and info["manifest"]["database"] == get_settings().db_name
    # Daten nach dem Backup verändern …
    with session_scope() as s:
        s.add(Bug(title="Nach dem Backup", description="", status="new"))
        s.query(Bug).filter(Bug.title == "Vor dem Backup").delete()
    out_env = tmp_path / "restored.env"
    backup.restore_backup(str(path), get_settings(), str(out_env))
    with session_scope() as s:
        titles = set(s.scalars(select(Bug.title)))
    assert titles == {"Vor dem Backup"}
    assert out_env.read_text() == env.read_text()
    assert backup.list_backups(str(tmp_path / "backups"))[0]["name"] == path.name


@needs_dump
def test_backup_tamper_detection(tmp_path):
    from lbc.core.config import get_settings
    from lbc.tools import backup
    path = backup.create_backup(str(tmp_path), get_settings(), str(tmp_path / "nicht-vorhanden.env"))
    files = backup.read_backup(str(path))
    bad = tmp_path / "manipuliert.tar.gz"
    import io
    with tarfile.open(bad, "w:gz") as tar:
        for name, data in files.items():
            if name == "database.sql.gz":
                import gzip
                data = gzip.compress(gzip.decompress(data) + b"\nDROP TABLE bugs;")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with pytest.raises(backup.BackupError, match="Prüfsumme"):
        backup.verify_backup(str(bad))
    (tmp_path / "kaputt.tar.gz").write_bytes(b"kein archiv")
    with pytest.raises(backup.BackupError):
        backup.verify_backup(str(tmp_path / "kaputt.tar.gz"))
    assert backup.prune(str(tmp_path), keep=1) == []


def test_backup_prune(tmp_path):
    from lbc.tools import backup
    for i in range(5):
        (tmp_path / f"lbc-backup-2026010{i}-000000.tar.gz").write_bytes(b"x")
    removed = backup.prune(str(tmp_path), keep=3)
    assert sorted(removed) == ["lbc-backup-20260100-000000.tar.gz", "lbc-backup-20260101-000000.tar.gz"]


_ = (os, func, Warning, Report, ChangeEvent)
