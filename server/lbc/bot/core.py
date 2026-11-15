"""Bot-Kern: Start, Heartbeat, Server-Cache, Mitglieder-Statistik und Outbox-Worker (Webpanel → Discord)."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import time
from typing import Awaitable, Callable, Optional

import aiohttp
import discord
from discord.ext import commands
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.db import session_scope
from ..core.models import (
    BetaInvitation,
    BetaInvitationRecipient,
    BetaTester,
    GameVersion,
    Report,
    SystemStatus,
    utcnow,
)
from ..services import beta as beta_svc
from ..services import community as community_svc
from ..services import settings_service
from ..services.base import claim_outbox_jobs, emit, finish_outbox_job
from . import cog_applications, cog_bugs, cog_community, cog_tickets
from .util import COLOR_DEFAULT, PermanentError, SettingsCache, db, log_error, truncate

log = logging.getLogger("lbc.bot")
HEARTBEAT_FILE = os.environ.get("BOT_HEARTBEAT_FILE", "/tmp/lbc-bot-heartbeat")
VERSION = "1.0.0"


class LeitstelleBot(commands.Bot):
    def __init__(self, guild_id: int) -> None:
        intents = discord.Intents.default()
        intents.members = True          # privilegierter Intent: Mitglieder (Begrüßung, Statistik)
        intents.message_content = True  # privilegierter Intent: Ticket-Protokolle, Bug-Beiträge, Auto-Moderation
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None,
                         allowed_mentions=discord.AllowedMentions.none())
        self.guild_id = guild_id
        self.cfg = SettingsCache()
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}"
        self._outbox_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.handlers: dict[str, Callable[[dict], Awaitable[None]]] = {}
        self.restart_requested = False

    async def setup_hook(self) -> None:
        await self.cfg.refresh()
        for mod in (cog_bugs, cog_tickets, cog_applications, cog_community):
            await mod.setup(self)
        # Persistente Buttons/Menüs (funktionieren auch nach einem Neustart)
        self.add_dynamic_items(cog_bugs.ClaimButton, cog_tickets.TicketOpenSelect, cog_tickets.TicketControl,
                               cog_applications.ApplySelect, cog_community.VerifyButton, cog_community.PollButton)
        guild = discord.Object(self.guild_id)
        for cmd in list(self.tree.get_commands(guild=None)):
            self.tree.remove_command(cmd.name)
            self.tree.add_command(cmd, guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
            log.info("%s Slash-Befehle synchronisiert", len(synced))
        except discord.HTTPException as exc:
            log.warning("Slash-Befehle konnten nicht synchronisiert werden: %s", exc)
        self._register_handlers()

    def _register_handlers(self) -> None:
        bugs: cog_bugs.BugCog = self.get_cog("BugCog")  # type: ignore[assignment]
        tickets: cog_tickets.TicketCog = self.get_cog("TicketCog")  # type: ignore[assignment]
        apps: cog_applications.ApplicationCog = self.get_cog("ApplicationCog")  # type: ignore[assignment]
        com: cog_community.CommunityCog = self.get_cog("CommunityCog")  # type: ignore[assignment]
        self.handlers = {
            "bug.notify_developers": bugs.notify_developers,
            "bug.sync_message": bugs.sync_message,
            "bug.thread_message": bugs.thread_message,
            "bug.thread_comment": bugs.thread_comment,
            "bug.dm_handover": bugs.dm_handover,
            "ticket.refresh_panel": tickets.refresh_panel,
            "ticket.send_message": tickets.send_message,
            "ticket.system_message": tickets.system_message,
            "ticket.close_channel": tickets.close_channel,
            "ticket.reopen_channel": tickets.reopen_channel,
            "applications.refresh_panel": apps.refresh_panel,
            "applications.notify_team": apps.notify_team,
            "verification.refresh_panel": com.refresh_verification_panel,
            "moderation.execute": com.execute_moderation,
            "moderation.log": com.moderation_log,
            "announcement.publish": com.publish_announcement,
            "message.delete": com.delete_message,
            "poll.publish": com.publish_poll,
            "poll.update": com.update_poll,
            "dm.send": self.send_dm,
            "member.add_role": self.add_role,
            "member.remove_role": self.remove_role,
            "beta.send_invitation": self.send_beta_invitation,
            "report.post": self.post_report,
            "bot.restart": self.request_restart,
        }

    # ------------------------------------------------------------------ Lebenszyklus
    async def on_ready(self) -> None:
        log.info("Angemeldet als %s (%s)", self.user, self.user.id if self.user else "-")
        guild = self.get_guild(self.guild_id)
        if guild is None:
            log.error("Der Bot ist nicht Mitglied des Servers %s – bitte einladen.", self.guild_id)
            await log_error("Start", f"Bot ist nicht Mitglied des Discord-Servers {self.guild_id}")
        else:
            await self.update_guild_cache(guild)
        tickets: cog_tickets.TicketCog = self.get_cog("TicketCog")  # type: ignore[assignment]
        await tickets.load_channels()
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._heartbeat_loop()), asyncio.create_task(self._outbox_loop())]
        bugs: cog_bugs.BugCog = self.get_cog("BugCog")  # type: ignore[assignment]
        try:
            await bugs.catch_up()
        except Exception as exc:  # noqa: BLE001
            await log_error("Nacherfassung Bugs", exc)

    async def update_guild_cache(self, guild: discord.Guild) -> None:
        details = {
            "id": str(guild.id), "name": guild.name, "member_count": guild.member_count,
            "channels": [{"id": str(c.id), "name": c.name, "type": str(c.type),
                          "category": c.category.name if getattr(c, "category", None) else None}
                         for c in sorted(guild.channels, key=lambda c: (c.position, c.id))],
            "roles": [{"id": str(r.id), "name": r.name} for r in sorted(guild.roles, key=lambda r: -r.position)
                      if not r.is_default() and not r.managed],
        }

        def save(s: Session) -> None:
            row = s.get(SystemStatus, "guild") or SystemStatus(component="guild", status="ok")
            row.details = details
            row.last_seen = utcnow()
            s.merge(row)
            community_svc.record_snapshot(s, guild.member_count or 0)
        await db(save)

    async def on_guild_channel_create(self, channel):  # noqa: D401
        await self.update_guild_cache(channel.guild)

    async def on_guild_channel_delete(self, channel):
        await self.update_guild_cache(channel.guild)

    async def on_guild_role_create(self, role):
        await self.update_guild_cache(role.guild)

    async def on_guild_role_delete(self, role):
        await self.update_guild_cache(role.guild)

    async def _heartbeat_loop(self) -> None:
        counter = 0
        while not self.is_closed():
            guild = self.get_guild(self.guild_id)
            details = {"latency_ms": round(self.latency * 1000) if self.latency == self.latency else None,
                       "guild": guild.name if guild else None, "member_count": guild.member_count if guild else None,
                       "version": VERSION, "worker": self.worker_id}

            def beat(s: Session) -> None:
                row = s.get(SystemStatus, "bot") or SystemStatus(component="bot", status="online")
                row.status = "online"
                row.details = details
                row.last_seen = utcnow()
                s.merge(row)
                emit(s, "system")
            try:
                await db(beat)
                with open(HEARTBEAT_FILE, "w") as fh:
                    fh.write(str(int(time.time())))
                counter += 1
                if guild and counter % 60 == 0:  # ca. alle 30 Minuten
                    await self.update_guild_cache(guild)
            except Exception as exc:  # noqa: BLE001
                log.warning("Heartbeat fehlgeschlagen (Datenbank?): %s", exc)
            await asyncio.sleep(30)

    def kick_outbox(self) -> Awaitable[None]:
        """Weckt den Outbox-Worker sofort (statt auf das nächste Intervall zu warten)."""
        self._outbox_event.set()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        fut.set_result(None)
        return fut

    async def _outbox_loop(self) -> None:
        while not self.is_closed():
            try:
                await self.process_outbox_once()
            except Exception as exc:  # noqa: BLE001
                log.warning("Outbox-Worker: %s", exc)
            try:
                await asyncio.wait_for(self._outbox_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            self._outbox_event.clear()
            if self.restart_requested:
                log.info("Neustart angefordert – Bot wird beendet (Docker startet ihn neu)")
                await self.close()
                return

    async def process_outbox_once(self) -> int:
        jobs = await asyncio.to_thread(self._claim_jobs)
        for job_id, kind, payload in jobs:
            handler = self.handlers.get(kind)
            error, permanent = None, False
            if handler is None:
                error, permanent = f"Unbekannter Auftragstyp: {kind}", True
            else:
                try:
                    await handler(payload)
                except PermanentError as exc:
                    error, permanent = str(exc), True
                except discord.Forbidden as exc:
                    error, permanent = f"Keine Berechtigung in Discord: {exc.text or exc}", True
                except discord.NotFound as exc:
                    error, permanent = f"Nicht gefunden in Discord: {exc.text or exc}", True
                except discord.HTTPException as exc:
                    error = f"Discord-Fehler {exc.status}: {exc.text or exc}"
                except (discord.GatewayNotFound, OSError, asyncio.TimeoutError) as exc:
                    error = f"Discord nicht erreichbar: {exc}"
                except Exception as exc:  # noqa: BLE001
                    log.exception("Auftrag %s (%s) fehlgeschlagen", job_id, kind)
                    error = f"{type(exc).__name__}: {exc}"
            await db(lambda s, j=job_id, e=error, p=permanent: finish_outbox_job(s, j, e, p))
        return len(jobs)

    def _claim_jobs(self) -> list[tuple[int, str, dict]]:
        with session_scope() as s:
            return [(j.id, j.kind, dict(j.payload or {})) for j in claim_outbox_jobs(s, self.worker_id)]

    # ------------------------------------------------------------------ Allgemeine Outbox-Handler
    async def send_dm(self, payload: dict) -> None:
        user = await self.fetch_user(int(payload["discord_id"]))
        embed = discord.Embed(title=truncate(payload.get("title") or "Leitstelle Bavaria", 256),
                              description=truncate(payload["content"], 4000), color=COLOR_DEFAULT)
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            raise PermanentError("Der Nutzer erlaubt keine Direktnachrichten.") from None

    async def _member(self, discord_id) -> discord.Member:
        guild = self.get_guild(self.guild_id)
        if guild is None:
            raise PermanentError("Discord-Server nicht verfügbar.")
        return guild.get_member(int(discord_id)) or await guild.fetch_member(int(discord_id))

    async def add_role(self, payload: dict) -> None:
        member = await self._member(payload["discord_id"])
        role = member.guild.get_role(int(payload["role_id"]))
        if role is None:
            raise PermanentError(f"Rolle {payload['role_id']} existiert nicht.")
        if role not in member.roles:
            await member.add_roles(role, reason=payload.get("reason"))

    async def remove_role(self, payload: dict) -> None:
        try:
            member = await self._member(payload["discord_id"])
        except discord.NotFound:
            return  # nicht mehr auf dem Server
        role = member.guild.get_role(int(payload["role_id"]))
        if role is not None and role in member.roles:
            await member.remove_roles(role, reason=payload.get("reason"))

    async def send_beta_invitation(self, payload: dict) -> None:
        rid = int(payload["recipient_id"])

        def load(s: Session):
            r = s.get(BetaInvitationRecipient, rid)
            if r is None or r.status == "sent":
                return None
            inv = s.get(BetaInvitation, r.invitation_id)
            tester = s.get(BetaTester, r.tester_id)
            version = s.get(GameVersion, inv.version_id) if inv.version_id else None
            return inv.title, beta_svc.render_invitation(inv, tester, version), tester.user.discord_id

        data = await db(load)
        if data is None:
            return
        title, text, discord_id = data

        def mark(s: Session, status: str, error: Optional[str]) -> None:
            r = s.get(BetaInvitationRecipient, rid)
            r.status, r.error = status, error
            r.sent_at = utcnow() if status == "sent" else None
            emit(s, "beta")
        try:
            user = await self.fetch_user(int(discord_id))
            await user.send(embed=discord.Embed(title=truncate(title, 256), description=truncate(text, 4000),
                                                color=COLOR_DEFAULT))
        except (discord.Forbidden, discord.NotFound) as exc:
            await db(mark, "failed", f"DM nicht möglich: {exc}"[:300])
            raise PermanentError(f"Einladung nicht zustellbar: {exc}") from exc
        await db(mark, "sent", None)

    async def post_report(self, payload: dict) -> None:
        rid = int(payload["report_id"])
        content = await db(lambda s: (lambda r: r.content if r else None)(s.get(Report, rid)))
        if content is None:
            return
        channel_id = int(payload["channel_id"])
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        lines = content.split("\n")
        embed = discord.Embed(title=lines[0], description="\n".join(lines[1:]), color=COLOR_DEFAULT)
        await channel.send(embed=embed)

        def mark(s: Session) -> None:
            r = s.get(Report, rid)
            r.posted = True
            emit(s, "reports")
        await db(mark)

    async def request_restart(self, payload: dict) -> None:
        log.info("Neustart angefordert: %s", payload.get("reason"))
        self.restart_requested = True


def resolve_startup() -> tuple[str, int]:
    with session_scope() as s:
        token = settings_service.get_value(s, "discord.bot_token")
        guild = settings_service.get_value(s, "discord.guild_id")
    if not token:
        raise SystemExit("FEHLER: Kein Discord Bot Token konfiguriert (DISCORD_BOT_TOKEN in .env oder Webpanel).")
    if not guild or not str(guild).isdigit():
        raise SystemExit("FEHLER: Keine Discord Server-ID konfiguriert (DISCORD_GUILD_ID in .env oder Webpanel).")
    return token, int(guild)


def main() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from ..tools.dbcheck import run_checks
    for attempt in range(1, 31):
        report = run_checks()
        if report.ok:
            break
        failed = next((c for c in report.checks if not c.ok and c.required), None)
        log.error("Datenbank nicht bereit (Versuch %s/30): %s", attempt,
                  f"{failed.title}: {failed.message}" if failed else "unbekannt")
        time.sleep(10)
    else:
        sys.exit("FEHLER: Die STRATO-Datenbank ist nicht erreichbar. Der Bot wird beendet.")
    token, guild_id = resolve_startup()
    bot = LeitstelleBot(guild_id)
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        with session_scope() as s:
            from ..services.base import record_error
            record_error(s, "bot", "Anmeldung bei Discord fehlgeschlagen – Bot Token ungültig.")
        sys.exit("FEHLER: Der Discord Bot Token ist ungültig.")
    except discord.PrivilegedIntentsRequired:
        sys.exit("FEHLER: Bitte im Discord Developer Portal die Intents 'Server Members' und 'Message Content' aktivieren.")
    except (OSError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        msg = f"Discord ist nicht erreichbar ({type(exc).__name__}: {exc}). Internetverbindung/DNS prüfen – neuer Versuch durch Docker."
        log.error(msg)
        try:
            with session_scope() as s:
                from ..services.base import record_error
                record_error(s, "bot", msg)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(15)  # Docker startet den Bot anschließend neu (restart: unless-stopped)
        sys.exit(f"FEHLER: {msg}")
    finally:
        try:
            with session_scope() as s:
                row = s.get(SystemStatus, "bot")
                if row:
                    row.status = "offline"
        except Exception:  # noqa: BLE001
            pass


