"""Discord-Bot-Logik mit gemockten Discord-Objekten (ohne echte Discord-Verbindung).

Echte Discord-Aufrufe können ohne Bot-Token nicht live getestet werden; die Discord-Objekte werden
daher durch Attrappen ersetzt, die Datenbank ist jedoch echt.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlalchemy import func, select, update

from lbc.bot import cog_bugs
from lbc.bot.core import LeitstelleBot
from lbc.core.db import session_scope
from lbc.core.models import Bug, DiscordOutbox, utcnow
from lbc.services import bugs as bug_svc
from lbc.services.base import enqueue

DEV_ROLE = 610000000000000001
FORUM = 620000000000000001
DEV_CHANNEL = 630000000000000001


class FakeCfg:
    def __init__(self, values):
        self.values = values

    async def get(self, key):
        return self.values.get(key)

    async def snowflake(self, key):
        v = self.values.get(key)
        return int(v) if v else None


class FakeBot:
    def __init__(self):
        self.cfg = FakeCfg({"roles.developer": str(DEV_ROLE), "channels.bug_forum": str(FORUM),
                            "channels.developer": str(DEV_CHANNEL), "bugs.ping_developer_role": True,
                            "discord.guild_id": "300000000000000001"})
        self.channels = {}
        self.kick_outbox = AsyncMock()

    def get_channel(self, cid):
        return self.channels.get(cid)

    async def fetch_channel(self, cid):
        return self.channels[cid]

    def get_cog(self, name):
        return self.cog


def member(uid, roles=()):
    return SimpleNamespace(id=uid, name=f"user{uid}", display_name=f"User {uid}", bot=False,
                           roles=[SimpleNamespace(id=r) for r in roles], display_avatar=SimpleNamespace(url="https://a"))


def interaction(user, message=None):
    return SimpleNamespace(user=user, guild=None, message=message or SimpleNamespace(edit=AsyncMock()),
                           response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), is_done=lambda: True),
                           followup=SimpleNamespace(send=AsyncMock()), client=None)


def forum_thread(tid, author, content="Fahrzeug verschwindet\nVersion: 0.8.2"):
    msg = SimpleNamespace(author=author, content=content, attachments=[])
    return SimpleNamespace(id=tid, parent_id=FORUM, name="Fahrzeuge verschwinden", starter_message=msg,
                           applied_tags=[], send=AsyncMock(), owner=author, owner_id=author.id, archived=False)


@pytest.fixture
def bot():
    b = FakeBot()
    b.cog = cog_bugs.BugCog(b)
    return b


async def test_forum_post_creates_bug_once(bot):
    author = member(640000000000000001)
    thread = forum_thread(650000000000000001, author)
    await bot.cog.on_thread_create(thread)
    await bot.cog.on_thread_create(thread)  # doppeltes Ereignis
    with session_scope() as s:
        assert s.scalar(select(func.count(Bug.id))) == 1
    assert thread.send.await_count == 1
    assert "#0001" in thread.send.await_args.args[0]


async def test_thread_outside_bug_forum_ignored(bot):
    thread = forum_thread(650000000000000002, member(640000000000000001))
    thread.parent_id = 999
    await bot.cog.on_thread_create(thread)
    with session_scope() as s:
        assert s.scalar(select(func.count(Bug.id))) == 0


async def test_developer_notification_uses_real_role_mention(bot):
    await bot.cog.on_thread_create(forum_thread(650000000000000003, member(640000000000000001)))
    channel = SimpleNamespace(id=DEV_CHANNEL, send=AsyncMock(return_value=SimpleNamespace(id=660000000000000001)))
    bot.channels[DEV_CHANNEL] = channel
    with session_scope() as s:
        bug_id = s.scalar(select(Bug.id))
    await bot.cog.notify_developers({"bug_id": bug_id})
    kwargs = channel.send.await_args.kwargs
    assert kwargs["content"].startswith(f"<@&{DEV_ROLE}>")
    assert [r.id for r in kwargs["allowed_mentions"].roles] == [DEV_ROLE]
    assert kwargs["embed"].title.startswith("BUG #0001 – Fahrzeuge verschwinden")
    assert kwargs["embed"].author.name == "LEITSTELLE BAVARIA – NEUER BUG"
    buttons = [c for c in kwargs["view"].children]
    assert buttons[0].item.label == "Ich übernehme" and buttons[0].item.custom_id == f"lbc:bug:claim:{bug_id}"
    assert buttons[1].label == "Zum Bug" and buttons[1].url.endswith("/650000000000000003")
    with session_scope() as s:
        assert s.get(Bug, bug_id).dev_message_id == "660000000000000001"
    # Wiederholung desselben Auftrags darf keine zweite Nachricht erzeugen
    edit_msg = SimpleNamespace(edit=AsyncMock())
    channel.get_partial_message = MagicMock(return_value=edit_msg)
    await bot.cog.notify_developers({"bug_id": bug_id})
    assert channel.send.await_count == 1 and edit_msg.edit.await_count == 1


async def test_claim_button_requires_developer_role(bot):
    await bot.cog.on_thread_create(forum_thread(650000000000000004, member(640000000000000001)))
    with session_scope() as s:
        bug_id = s.scalar(select(Bug.id))
    inter = interaction(member(670000000000000001, roles=[]))
    await bot.cog.handle_claim(inter, bug_id)
    assert "Developer-Rolle" in inter.response.send_message.await_args.args[0]
    with session_scope() as s:
        assert s.get(Bug, bug_id).assignee_discord_id is None


async def test_claim_button_first_click_wins_and_disables(bot):
    await bot.cog.on_thread_create(forum_thread(650000000000000005, member(640000000000000001)))
    with session_scope() as s:
        bug_id = s.scalar(select(Bug.id))
    msg = SimpleNamespace(edit=AsyncMock())
    dev1, dev2 = member(680000000000000001, [DEV_ROLE]), member(680000000000000002, [DEV_ROLE])
    i1, i2 = interaction(dev1, msg), interaction(dev2, msg)
    await asyncio.gather(bot.cog.handle_claim(i1, bug_id), bot.cog.handle_claim(i2, bug_id))
    texts = [i1.followup.send.await_args.args[0], i2.followup.send.await_args.args[0]]
    assert sum("übernommen." in t and "Du hast" in t for t in texts) == 1
    assert bug_svc.ALREADY_CLAIMED_MSG in texts
    view = msg.edit.await_args.kwargs["view"]
    assert view.children[0].item.disabled and view.children[0].item.label.startswith("Übernommen von")
    with session_scope() as s:
        assert s.get(Bug, bug_id).assignee_discord_id in ("680000000000000001", "680000000000000002")


async def test_catch_up_after_restart(bot):
    """Während einer Ausfallzeit erstellte Forum-Beiträge werden beim Start nacherfasst."""
    t1 = forum_thread(650000000000000010, member(640000000000000001))
    t2 = forum_thread(650000000000000011, member(640000000000000002))
    await bot.cog.register_thread(t1, announce=False)

    async def archived(limit=50):
        for t in []:
            yield t

    forum = MagicMock(spec=discord.ForumChannel)
    forum.threads = [t1, t2]
    forum.archived_threads = archived
    bot.channels[FORUM] = forum
    assert await bot.cog.catch_up() == 1
    with session_scope() as s:
        assert s.scalar(select(func.count(Bug.id))) == 2


# ---------------------------------------------------------------------------- Outbox-Worker
def _http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), {"message": "Fehler", "code": 0})


def _job(kind="test.job", payload=None):
    with session_scope() as s:
        return enqueue(s, kind, payload or {}).id


@pytest.fixture
def real_bot():
    b = LeitstelleBot(300000000000000001)
    yield b


async def test_outbox_success_retry_and_permanent_failure(real_bot):
    ok_id, retry_id, perm_id, unknown_id = _job("t.ok"), _job("t.retry"), _job("t.perm"), _job("t.unknown")
    calls = []

    async def ok(p):
        calls.append("ok")

    async def flaky(p):
        raise _http_error(discord.HTTPException, 503)

    async def gone(p):
        raise _http_error(discord.NotFound, 404)

    real_bot.handlers = {"t.ok": ok, "t.retry": flaky, "t.perm": gone}
    assert await real_bot.process_outbox_once() == 4
    with session_scope() as s:
        jobs = {j.id: j for j in s.scalars(select(DiscordOutbox))}
        assert jobs[ok_id].status == "done"
        assert jobs[retry_id].status == "pending" and jobs[retry_id].attempts == 1
        assert jobs[retry_id].next_attempt_at > utcnow()
        assert "503" in jobs[retry_id].last_error
        assert jobs[perm_id].status == "failed"
        assert jobs[unknown_id].status == "failed"
    # Nicht fällige Aufträge werden nicht erneut verarbeitet
    assert await real_bot.process_outbox_once() == 0


async def test_discord_unreachable_is_retried_until_max(real_bot):
    jid = _job("t.net")

    async def down(p):
        raise OSError("Network is unreachable")

    real_bot.handlers = {"t.net": down}
    for _ in range(8):
        with session_scope() as s:
            s.execute(update(DiscordOutbox).where(DiscordOutbox.id == jid).values(next_attempt_at=utcnow()))
        await real_bot.process_outbox_once()
    with session_scope() as s:
        job = s.get(DiscordOutbox, jid)
        assert job.status == "failed" and job.attempts == 8 and "nicht erreichbar" in job.last_error


async def test_stale_lock_recovered_after_bot_crash(real_bot):
    """Bot stürzt während der Verarbeitung ab → Auftrag wird nach Timeout erneut ausgeführt."""
    jid = _job("t.ok")
    with session_scope() as s:
        s.execute(update(DiscordOutbox).where(DiscordOutbox.id == jid).values(
            status="processing", locked_by="alter-worker", locked_at=utcnow() - timedelta(minutes=10), attempts=1))
    done = []

    async def ok(p):
        done.append(1)
    real_bot.handlers = {"t.ok": ok}
    assert await real_bot.process_outbox_once() == 1
    with session_scope() as s:
        assert s.get(DiscordOutbox, jid).status == "done"


async def test_two_workers_never_process_same_job():
    from concurrent.futures import ThreadPoolExecutor

    from lbc.services.base import claim_outbox_jobs
    for _ in range(30):
        _job("t.x")

    def claim(worker):
        got = []
        for _ in range(10):
            with session_scope() as s:
                got += [j.id for j in claim_outbox_jobs(s, worker, limit=3)]
        return got

    with ThreadPoolExecutor(3) as ex:
        results = list(ex.map(claim, ["a", "b", "c"]))
    all_ids = [i for r in results for i in r]
    assert len(all_ids) == len(set(all_ids)) == 30


async def test_restart_job_requests_shutdown(real_bot):
    _job("bot.restart", {"reason": "Test"})
    real_bot._register_handlers = lambda: None
    real_bot.handlers = {"bot.restart": real_bot.request_restart}
    await real_bot.process_outbox_once()
    assert real_bot.restart_requested


async def test_idempotent_outbox_keys():
    with session_scope() as s:
        a = enqueue(s, "bug.sync_message", {"bug_id": 1}, idempotency_key="bug-sync-1")
        b = enqueue(s, "bug.sync_message", {"bug_id": 1}, idempotency_key="bug-sync-1")
        assert a is b
    with session_scope() as s:
        assert s.scalar(select(func.count(DiscordOutbox.id))) == 1


def test_embed_builder_states():
    snap = {"id": 42, "ref": "#0042", "title": "Test", "description": "d", "status": "fixed", "priority": "normal",
            "reporter": {"discord_id": "1", "name": "x"}, "version": None, "version_text": "0.8.2",
            "assignee": {"discord_id": "2", "name": "Dev"}, "reproduction_steps": None, "forum_thread_id": "5"}
    embed = cog_bugs.build_embed(snap)
    assert embed.fields[2].value == "Behoben"
    view = cog_bugs.build_view(snap, "https://discord.com/channels/1/5")
    assert view.children[0].item.disabled
