"""Bug-Erfassung aus dem Forum-Kanal, Entwickler-Benachrichtigung und atomare Übernahme per Button."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.orm import Session

from ..core.models import Bug, BugComment
from ..services import bugs as bug_svc
from ..services.base import Actor, ServiceError
from ..services.team import find_member_by_discord_id, member_permissions
from .util import (
    COLOR_DANGER,
    COLOR_DEFAULT,
    COLOR_MUTED,
    COLOR_OK,
    COLOR_WARN,
    PermanentError,
    attachments_of,
    avatar,
    db,
    display,
    has_role,
    log_error,
    truncate,
)

log = logging.getLogger("lbc.bot.bugs")

STATUS_COLORS = {"new": COLOR_DANGER, "claimed": COLOR_WARN, "in_progress": COLOR_DEFAULT, "fixed": COLOR_OK,
                 "not_reproducible": COLOR_MUTED}


def bug_snapshot(bug: Bug) -> dict:
    return bug_svc.serialize(bug)


def build_embed(b: dict) -> discord.Embed:
    """Entwickler-Nachricht – aus einem serialisierten Bug (reine Funktion, testbar)."""
    embed = discord.Embed(
        title=truncate(f"BUG {b['ref']} – {b['title']}", 256),
        description=truncate(b["description"] or "–", 1500),
        color=STATUS_COLORS.get(b["status"], COLOR_DEFAULT),
    )
    embed.set_author(name="LEITSTELLE BAVARIA – NEUER BUG")
    reporter = b["reporter"]
    embed.add_field(name="Eingereicht von",
                    value=f"<@{reporter['discord_id']}>" if reporter.get("discord_id") else (reporter.get("name") or "–"),
                    inline=True)
    version = b["version"]["version"] if b.get("version") else (b.get("version_text") or "nicht angegeben")
    embed.add_field(name="Version", value=version, inline=True)
    embed.add_field(name="Status", value=bug_svc.STATUS_LABELS.get(b["status"], b["status"]), inline=True)
    if b.get("assignee"):
        embed.add_field(name="Zuständiger Entwickler", value=f"<@{b['assignee']['discord_id']}>", inline=True)
    if b.get("reproduction_steps"):
        embed.add_field(name="Reproduktion", value=truncate(b["reproduction_steps"], 1000), inline=False)
    embed.set_footer(text=f"Bug-ID {b['ref']} · Priorität {b['priority']}")
    return embed


class ClaimButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lbc:bug:claim:(?P<id>\d+)"):
    """Persistenter Button „Ich übernehme“ (überlebt Bot-Neustarts)."""

    def __init__(self, bug_id: int, disabled: bool = False, label: str = "Ich übernehme") -> None:
        super().__init__(discord.ui.Button(label=label, style=discord.ButtonStyle.success if not disabled
                                           else discord.ButtonStyle.secondary,
                                           custom_id=f"lbc:bug:claim:{bug_id}", disabled=disabled))
        self.bug_id = bug_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: Optional[BugCog] = interaction.client.get_cog("BugCog")  # type: ignore[union-attr]
        if cog is None:
            await interaction.response.send_message("Der Bot startet gerade neu. Bitte erneut versuchen.", ephemeral=True)
            return
        await cog.handle_claim(interaction, self.bug_id)


def build_view(b: dict, thread_url: Optional[str]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    claimed = b.get("assignee") is not None
    closed = b["status"] in bug_svc.CLOSED_STATUSES
    if claimed:
        label = f"Übernommen von {truncate(b['assignee']['name'] or '', 40)}"
    elif closed:
        label = bug_svc.STATUS_LABELS[b["status"]]
    else:
        label = "Ich übernehme"
    view.add_item(ClaimButton(b["id"], disabled=claimed or closed, label=label))
    if thread_url:
        view.add_item(discord.ui.Button(label="Zum Bug", style=discord.ButtonStyle.link, url=thread_url))
    return view


class BugCog(commands.Cog, name="BugCog"):
    def __init__(self, bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ Erfassung
    async def _forum_id(self) -> Optional[int]:
        return await self.bot.cfg.snowflake("channels.bug_forum")

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        forum_id = await self._forum_id()
        if not forum_id or thread.parent_id != forum_id:
            return
        await self.register_thread(thread, announce=True)

    async def _starter_message(self, thread: discord.Thread) -> Optional[discord.Message]:
        if thread.starter_message:
            return thread.starter_message
        for delay in (0.5, 1.5, 3, 6):
            try:
                return await thread.fetch_message(thread.id)
            except discord.NotFound:
                await asyncio.sleep(delay)
            except discord.HTTPException as exc:
                log.warning("Startnachricht von Thread %s nicht abrufbar: %s", thread.id, exc)
                await asyncio.sleep(delay)
        return None

    async def register_thread(self, thread: discord.Thread, announce: bool) -> Optional[int]:
        """Legt den Bug an. Doppelte Ereignisse werden über die eindeutige Thread-ID abgefangen."""
        msg = await self._starter_message(thread)
        author = msg.author if msg else (thread.owner or (await self._fetch_user(thread.owner_id)))
        if author is None or getattr(author, "bot", False):
            return None
        tag_names = [t.name for t in getattr(thread, "applied_tags", [])]
        try:
            bug_id, created = await db(lambda s: self._create(s, thread, msg, author, tag_names))
        except Exception as exc:  # noqa: BLE001
            await log_error(f"Bug-Erfassung Thread {thread.id}", exc)
            return None
        if created and announce:
            try:
                await thread.send(
                    f"Danke für deine Meldung! Der Bug wurde als **{bug_svc.fmt_id(bug_id)}** erfasst und an das "
                    "Entwicklerteam weitergeleitet.", allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                log.warning("Bestätigung im Thread fehlgeschlagen: %s", exc)
        if created:
            await self.bot.kick_outbox()
        return bug_id

    @staticmethod
    def _create(s: Session, thread: discord.Thread, msg: Optional[discord.Message], author, tag_names) -> tuple[int, bool]:
        bug, created = bug_svc.create_from_forum(
            s, thread_id=str(thread.id), forum_channel_id=str(thread.parent_id), title=thread.name,
            content=msg.content if msg else "", author_discord_id=str(author.id), author_name=display(author),
            author_avatar=avatar(author), attachments=attachments_of(msg) if msg else [], tag_names=tag_names)
        return bug.id, created

    async def _fetch_user(self, user_id: Optional[int]):
        if not user_id:
            return None
        try:
            return await self.bot.fetch_user(user_id)
        except discord.HTTPException:
            return None

    async def catch_up(self) -> int:
        """Nach (Neu-)Start: Forum-Beiträge nacherfassen, die während einer Ausfallzeit erstellt wurden."""
        forum_id = await self._forum_id()
        if not forum_id:
            return 0
        forum = self.bot.get_channel(forum_id)
        if not isinstance(forum, discord.ForumChannel):
            return 0
        known = set(await db(lambda s: [r for (r,) in s.query(Bug.forum_thread_id)
                                        .filter(Bug.forum_thread_id.isnot(None)).all()]))
        threads = list(forum.threads)
        try:
            async for t in forum.archived_threads(limit=50):
                threads.append(t)
        except discord.HTTPException:
            pass
        count = 0
        for t in threads:
            if str(t.id) not in known:
                if await self.register_thread(t, announce=True):
                    count += 1
        if count:
            log.info("%s Bug-Beiträge nacherfasst", count)
        return count

    # ------------------------------------------------------------------ Übernahme
    async def handle_claim(self, interaction: discord.Interaction, bug_id: int) -> None:
        dev_role = await self.bot.cfg.snowflake("roles.developer")
        member = interaction.user
        if isinstance(member, discord.User) and interaction.guild:
            member = interaction.guild.get_member(member.id) or member
        # Serverseitige Prüfung der Developer-Rolle
        if not dev_role or not has_role(member, dev_role):
            await interaction.response.send_message(
                "Nur Mitglieder mit der Developer-Rolle dürfen Bugs übernehmen.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result, snap = await db(lambda s: self._claim(s, bug_id, member))
        guild_id = await self.bot.cfg.get("discord.guild_id")
        if snap is None:
            await interaction.followup.send("Dieser Bug existiert nicht mehr.", ephemeral=True)
            return
        try:
            await interaction.message.edit(embed=build_embed(snap),
                                           view=build_view(snap, self._thread_url(guild_id, snap)))
        except discord.HTTPException as exc:
            log.warning("Entwickler-Nachricht konnte nicht aktualisiert werden: %s", exc)
        if result == bug_svc.ClaimResult.CLAIMED:
            await interaction.followup.send(f"Du hast Bug **{snap['ref']}** übernommen.", ephemeral=True)
        elif result == bug_svc.ClaimResult.ALREADY_YOURS:
            await interaction.followup.send("Du bist bereits für diesen Bug zuständig.", ephemeral=True)
        elif result == bug_svc.ClaimResult.CLOSED:
            await interaction.followup.send("Dieser Bug ist bereits abgeschlossen.", ephemeral=True)
        else:
            await interaction.followup.send(bug_svc.ALREADY_CLAIMED_MSG, ephemeral=True)

    @staticmethod
    def _claim(s: Session, bug_id: int, member) -> tuple[bug_svc.ClaimResult, Optional[dict]]:
        result, bug = bug_svc.claim(s, bug_id, str(member.id), display(member), source="discord")
        return result, (bug_snapshot(bug) if bug else None)

    @staticmethod
    def _thread_url(guild_id, snap: dict) -> Optional[str]:
        if guild_id and snap.get("forum_thread_id"):
            return f"https://discord.com/channels/{guild_id}/{snap['forum_thread_id']}"
        return None

    # ------------------------------------------------------------------ Outbox-Aufträge
    async def notify_developers(self, payload: dict) -> None:
        bug_id = int(payload["bug_id"])
        snap = await db(lambda s: bug_snapshot(bug_svc.get_bug(s, bug_id)))
        if snap["dev_message_id"]:
            await self.sync_message(payload)
            return
        channel_id = await self.bot.cfg.snowflake("channels.developer")
        if not channel_id:
            raise PermanentError("Kein Developer-Channel konfiguriert.")
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        dev_role = await self.bot.cfg.snowflake("roles.developer")
        ping = bool(await self.bot.cfg.get("bugs.ping_developer_role"))
        content = f"<@&{dev_role}> Neuer Bug eingegangen." if (dev_role and ping) else "Neuer Bug eingegangen."
        guild_id = await self.bot.cfg.get("discord.guild_id")
        msg = await channel.send(
            content=content, embed=build_embed(snap), view=build_view(snap, self._thread_url(guild_id, snap)),
            allowed_mentions=discord.AllowedMentions(roles=[discord.Object(dev_role)] if dev_role and ping else False,
                                                     users=False, everyone=False))
        await db(lambda s: bug_svc.set_dev_message(s, bug_id, str(channel.id), str(msg.id)))

    async def sync_message(self, payload: dict) -> None:
        bug_id = int(payload["bug_id"])
        snap = await db(lambda s: bug_snapshot(bug_svc.get_bug(s, bug_id)))
        if not snap["dev_message_id"]:
            return
        bug_channel = await db(lambda s: bug_svc.get_bug(s, bug_id).dev_channel_id)
        channel = self.bot.get_channel(int(bug_channel)) or await self.bot.fetch_channel(int(bug_channel))
        guild_id = await self.bot.cfg.get("discord.guild_id")
        msg = channel.get_partial_message(int(snap["dev_message_id"]))
        await msg.edit(embed=build_embed(snap), view=build_view(snap, self._thread_url(guild_id, snap)))

    async def thread_message(self, payload: dict) -> None:
        bug_id = int(payload["bug_id"])
        snap = await db(lambda s: bug_snapshot(bug_svc.get_bug(s, bug_id)))
        if not snap["forum_thread_id"]:
            return
        thread = self.bot.get_channel(int(snap["forum_thread_id"])) or await self.bot.fetch_channel(int(snap["forum_thread_id"]))
        if snap["status"] == "fixed":
            fixed = snap["fixed_in_version"]["version"] if snap.get("fixed_in_version") else None
            text = (f"Bug **{snap['ref']}** wurde behoben" + (f" (Version {fixed})" if fixed else "") +
                    ". Vielen Dank für deine Meldung!")
        else:
            text = (f"Bug **{snap['ref']}** konnte vom Entwicklerteam leider nicht reproduziert werden. "
                    "Falls der Fehler erneut auftritt, ergänze bitte weitere Details.")
        if isinstance(thread, discord.Thread) and thread.archived:
            await thread.edit(archived=False)
        await thread.send(text, allowed_mentions=discord.AllowedMentions.none())

    async def thread_comment(self, payload: dict) -> None:
        bug_id, comment_id = int(payload["bug_id"]), int(payload["comment_id"])

        def load(s: Session):
            b = bug_svc.get_bug(s, bug_id)
            c = s.get(BugComment, comment_id)
            return b.forum_thread_id, (c.author_name, c.content) if c else None

        thread_id, comment = await db(load)
        if not thread_id or not comment:
            return
        thread = self.bot.get_channel(int(thread_id)) or await self.bot.fetch_channel(int(thread_id))
        if isinstance(thread, discord.Thread) and thread.archived:
            await thread.edit(archived=False)
        await thread.send(f"**Entwicklerteam ({comment[0]}):** {truncate(comment[1], 1800)}",
                          allowed_mentions=discord.AllowedMentions.none())

    async def dm_handover(self, payload: dict) -> None:
        bug_id = int(payload["bug_id"])
        snap = await db(lambda s: bug_snapshot(bug_svc.get_bug(s, bug_id)))
        user = await self.bot.fetch_user(int(payload["to_discord_id"]))
        guild_id = await self.bot.cfg.get("discord.guild_id")
        text = f"Dir wurde Bug **{snap['ref']} – {snap['title']}** von {payload.get('by')} übergeben."
        if payload.get("note"):
            text += f"\nNotiz: {payload['note']}"
        url = self._thread_url(guild_id, snap)
        if url:
            text += f"\n{url}"
        await user.send(text)

    # ------------------------------------------------------------------ Slash-Befehle für Entwickler
    group = app_commands.Group(name="bug", description="Bug-Management", guild_only=True)

    async def _dev_actor(self, interaction: discord.Interaction, bug_id: int) -> tuple[Optional[Actor], Optional[str]]:
        def check(s: Session):
            member = find_member_by_discord_id(s, str(interaction.user.id))
            perms = member_permissions(member) if member else set()
            bug = s.get(Bug, bug_id)
            if bug is None:
                return None, "Bug nicht gefunden."
            if "bugs.manage" in perms or ("bugs.claim" in perms and bug.assignee_discord_id == str(interaction.user.id)):
                return Actor(name=display(interaction.user), discord_id=str(interaction.user.id),
                             member_id=member.id, source="discord"), None
            return None, "Nur der zuständige Entwickler (oder Berechtigte mit bugs.manage) darf diesen Bug bearbeiten."
        return await db(check)

    @group.command(name="status", description="Status eines Bugs ändern")
    @app_commands.describe(bug_id="Bug-ID (z. B. 42)", status="Neuer Status", kommentar="Optionaler Kommentar")
    @app_commands.choices(status=[app_commands.Choice(name=v, value=k) for k, v in bug_svc.STATUS_LABELS.items()
                                  if k != "new"])
    async def status_cmd(self, interaction: discord.Interaction, bug_id: int, status: app_commands.Choice[str],
                         kommentar: Optional[str] = None) -> None:
        actor, err = await self._dev_actor(interaction, bug_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        try:
            await db(lambda s: bug_svc.set_status(s, actor, bug_id, status.value, kommentar))
        except ServiceError as exc:
            await interaction.response.send_message(exc.message, ephemeral=True)
            return
        await interaction.response.send_message(f"Status von {bug_svc.fmt_id(bug_id)}: **{status.name}**", ephemeral=True)
        await self.bot.kick_outbox()

    @group.command(name="uebergeben", description="Bug an einen anderen Entwickler übergeben")
    async def handover_cmd(self, interaction: discord.Interaction, bug_id: int, entwickler: discord.Member,
                           notiz: Optional[str] = None) -> None:
        actor, err = await self._dev_actor(interaction, bug_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        dev_role = await self.bot.cfg.snowflake("roles.developer")
        if not has_role(entwickler, dev_role):
            await interaction.response.send_message("Der Empfänger besitzt nicht die Developer-Rolle.", ephemeral=True)
            return
        try:
            await db(lambda s: bug_svc.handover(s, actor, bug_id, str(entwickler.id), display(entwickler), notiz))
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(getattr(exc, "message", str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(f"{bug_svc.fmt_id(bug_id)} wurde an {entwickler.mention} übergeben.",
                                                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await self.bot.kick_outbox()

    @group.command(name="notiz", description="Interne Notiz zu einem Bug hinzufügen")
    async def note_cmd(self, interaction: discord.Interaction, bug_id: int, text: str) -> None:
        actor, err = await self._dev_actor(interaction, bug_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await db(lambda s: bug_svc.add_comment(s, actor, bug_id, text, internal=True))
        await interaction.response.send_message("Notiz gespeichert.", ephemeral=True)

    @group.command(name="meine", description="Deine offenen Bugs anzeigen")
    async def mine_cmd(self, interaction: discord.Interaction) -> None:
        def load(s: Session):
            rows = s.query(Bug).filter(Bug.assignee_discord_id == str(interaction.user.id),
                                       Bug.status.in_(("claimed", "in_progress"))).order_by(Bug.id).limit(20).all()
            return [(b.id, b.title, b.status) for b in rows]
        rows = await db(load)
        if not rows:
            await interaction.response.send_message("Du hast keine offenen Bugs.", ephemeral=True)
            return
        lines = [f"**{bug_svc.fmt_id(i)}** {truncate(t, 70)} – {bug_svc.STATUS_LABELS[st]}" for i, t, st in rows]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(BugCog(bot))

