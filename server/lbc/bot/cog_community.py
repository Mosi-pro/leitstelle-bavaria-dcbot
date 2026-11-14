"""Community: Begrüßung, Verifizierung, Moderation (Slash-Befehle), Auto-Moderation, Ankündigungen, Umfragen."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import Announcement, AutoModRule, ModerationAction, Poll, utcnow
from ..services import community as svc
from ..services.base import Actor, ServiceError
from .util import (
    COLOR_DANGER,
    COLOR_DEFAULT,
    COLOR_OK,
    COLOR_WARN,
    PermanentError,
    avatar,
    db,
    display,
    has_role,
    log_error,
    parse_color,
    truncate,
    upsert_panel,
)

log = logging.getLogger("lbc.bot.community")


class VerifyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lbc:verify"):
    def __init__(self) -> None:
        super().__init__(discord.ui.Button(label="Verifizieren", style=discord.ButtonStyle.success,
                                           custom_id="lbc:verify"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: CommunityCog = interaction.client.get_cog("CommunityCog")  # type: ignore[assignment]
        await cog.handle_verify(interaction)


class PollButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lbc:poll:(?P<poll>\d+):(?P<opt>\d+)"):
    def __init__(self, poll_id: int, option_id: int, label: str = "Abstimmen", disabled: bool = False) -> None:
        super().__init__(discord.ui.Button(label=truncate(label, 80), style=discord.ButtonStyle.secondary,
                                           custom_id=f"lbc:poll:{poll_id}:{option_id}", disabled=disabled))
        self.poll_id, self.option_id = poll_id, option_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["poll"]), int(match["opt"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: CommunityCog = interaction.client.get_cog("CommunityCog")  # type: ignore[assignment]
        await cog.handle_vote(interaction, self.poll_id, self.option_id)


def poll_embed(data: dict) -> discord.Embed:
    closed = data["status"] == "closed"
    embed = discord.Embed(title=("Umfrage beendet: " if closed else "Umfrage: ") + truncate(data["question"], 230),
                          description=truncate(data.get("description") or "", 1000) or None,
                          color=COLOR_DEFAULT if not closed else 0x6B7280)
    res = data["results"]
    for o in res["options"]:
        filled = int(round(o["percent"] / 10))
        bar = "█" * filled + "░" * (10 - filled)
        embed.add_field(name=truncate(o["label"], 256), value=f"`{bar}` {o['votes']} Stimmen ({o['percent']} %)",
                        inline=False)
    footer = f"{res['voters']} Teilnehmer"
    if data.get("multiple_choice"):
        footer += " · Mehrfachauswahl möglich"
    if data.get("ends_at") and not closed:
        footer += f" · endet {data['ends_at'][:16].replace('T', ' ')} UTC"
    embed.set_footer(text=footer)
    return embed


def poll_view(data: dict) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for o in data["results"]["options"][:20]:
        view.add_item(PollButton(data["id"], o["id"], o["label"], disabled=data["status"] != "open"))
    return view


class CommunityCog(commands.Cog, name="CommunityCog"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._rules: list[dict] = []
        self._rules_loaded = 0.0

    # ------------------------------------------------------------------ Mitglieder
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.bot.guild_id or member.bot:
            return
        await db(lambda s: svc.member_joined(s, str(member.id), member.name, avatar(member)))
        channel_id = await self.bot.cfg.snowflake("channels.welcome")
        text = await self.bot.cfg.get("community.welcome_message")
        if channel_id and text:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(text.replace("{mention}", member.mention).replace("{name}", member.display_name),
                                       allowed_mentions=discord.AllowedMentions(users=[member]))
                except discord.HTTPException as exc:
                    log.warning("Begrüßung fehlgeschlagen: %s", exc)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id == self.bot.guild_id and not member.bot:
            await db(lambda s: svc.member_left(s, str(member.id)))

    async def handle_verify(self, interaction: discord.Interaction) -> None:
        if not await self.bot.cfg.get("community.verification_enabled"):
            await interaction.response.send_message("Die Verifizierung ist derzeit deaktiviert.", ephemeral=True)
            return
        role_id = await self.bot.cfg.snowflake("roles.verified")
        member = interaction.user
        if not isinstance(member, discord.Member) or not role_id:
            await interaction.response.send_message("Die Verifizierung ist nicht eingerichtet.", ephemeral=True)
            return
        role = member.guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message("Die Verifizierungsrolle existiert nicht.", ephemeral=True)
            return
        if role in member.roles:
            await interaction.response.send_message("Du bist bereits verifiziert.", ephemeral=True)
            return
        try:
            await member.add_roles(role, reason="Verifizierung")
        except discord.HTTPException as exc:
            await log_error("Verifizierung", exc)
            await interaction.response.send_message("Die Rolle konnte nicht vergeben werden. Bitte das Team informieren.",
                                                    ephemeral=True)
            return
        await db(lambda s: svc.verify(s, str(member.id), member.name))
        await interaction.response.send_message("Du bist jetzt verifiziert. Willkommen bei Leitstelle Bavaria!",
                                                ephemeral=True)

    async def refresh_verification_panel(self, payload: dict) -> None:
        embed = discord.Embed(title="Verifizierung", color=COLOR_DEFAULT,
                              description="Bitte bestätige mit dem Button unten, dass du die Serverregeln gelesen hast "
                                          "und akzeptierst. Danach erhältst du Zugriff auf den Server.")
        view = discord.ui.View(timeout=None)
        view.add_item(VerifyButton())
        await upsert_panel(self.bot, "verification", await self.bot.cfg.snowflake("channels.verification"), embed, view,
                           bool(payload.get("force_new")))

    # ------------------------------------------------------------------ Moderation (Slash-Befehle)
    async def _mod_check(self, interaction: discord.Interaction, perm: str) -> bool:
        member = interaction.user
        mod_role = await self.bot.cfg.snowflake("roles.moderator")
        allowed = isinstance(member, discord.Member) and (
            getattr(member.guild_permissions, perm, False) or has_role(member, mod_role))
        if not allowed:
            await interaction.response.send_message("Dir fehlt die Berechtigung für diesen Befehl.", ephemeral=True)
        return allowed

    def _actor(self, interaction: discord.Interaction) -> Actor:
        return Actor(name=display(interaction.user), discord_id=str(interaction.user.id), source="discord")

    async def _record(self, interaction, target: discord.abc.User, action: str, reason: str,
                      minutes: Optional[int] = None) -> int:
        actor = self._actor(interaction)
        return await db(lambda s: svc.moderate(s, actor, target_discord_id=str(target.id), action=action, reason=reason,
                                               duration_minutes=minutes, target_name=target.name, executed=True).id)

    def _hierarchy_ok(self, interaction: discord.Interaction, target: discord.Member) -> bool:
        me = interaction.guild.me
        actor = interaction.user
        if target.id == interaction.guild.owner_id or target.top_role >= me.top_role:
            return False
        if isinstance(actor, discord.Member) and actor.id != interaction.guild.owner_id and target.top_role >= actor.top_role:
            return False
        return True

    @app_commands.command(name="verwarnen", description="Mitglied verwarnen")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def warn_cmd(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str) -> None:
        if not await self._mod_check(interaction, "moderate_members"):
            return
        await self._record(interaction, mitglied, "warn", grund)
        await interaction.response.send_message(f"{mitglied.mention} wurde verwarnt.", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
        await self.bot.kick_outbox()

    @app_commands.command(name="timeout", description="Mitglied stummschalten")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(minuten="Dauer in Minuten (max. 40320 = 28 Tage)")
    async def timeout_cmd(self, interaction: discord.Interaction, mitglied: discord.Member,
                          minuten: app_commands.Range[int, 1, 40320], grund: str) -> None:
        if not await self._mod_check(interaction, "moderate_members"):
            return
        if not self._hierarchy_ok(interaction, mitglied):
            await interaction.response.send_message("Dieses Mitglied kann nicht moderiert werden (Rollenhierarchie).",
                                                    ephemeral=True)
            return
        await mitglied.timeout(timedelta(minutes=minuten), reason=f"{display(interaction.user)}: {grund}")
        await self._record(interaction, mitglied, "timeout", grund, minuten)
        await interaction.response.send_message(f"{mitglied.mention} ist für {minuten} Minuten stummgeschaltet.",
                                                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await self.bot.kick_outbox()

    @app_commands.command(name="kick", description="Mitglied vom Server entfernen")
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    async def kick_cmd(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str) -> None:
        if not await self._mod_check(interaction, "kick_members"):
            return
        if not self._hierarchy_ok(interaction, mitglied):
            await interaction.response.send_message("Dieses Mitglied kann nicht moderiert werden (Rollenhierarchie).",
                                                    ephemeral=True)
            return
        await mitglied.kick(reason=f"{display(interaction.user)}: {grund}")
        await self._record(interaction, mitglied, "kick", grund)
        await interaction.response.send_message(f"{mitglied} wurde vom Server entfernt.", ephemeral=True)
        await self.bot.kick_outbox()

    @app_commands.command(name="bann", description="Mitglied bannen")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    async def ban_cmd(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str,
                      nachrichten_tage: app_commands.Range[int, 0, 7] = 0) -> None:
        if not await self._mod_check(interaction, "ban_members"):
            return
        if not self._hierarchy_ok(interaction, mitglied):
            await interaction.response.send_message("Dieses Mitglied kann nicht moderiert werden (Rollenhierarchie).",
                                                    ephemeral=True)
            return
        await interaction.guild.ban(mitglied, reason=f"{display(interaction.user)}: {grund}",
                                    delete_message_seconds=nachrichten_tage * 86400)
        await self._record(interaction, mitglied, "ban", grund)
        await interaction.response.send_message(f"{mitglied} wurde gebannt.", ephemeral=True)
        await self.bot.kick_outbox()

    @app_commands.command(name="entbannen", description="Bann aufheben")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(discord_id="Discord-ID des gebannten Nutzers")
    async def unban_cmd(self, interaction: discord.Interaction, discord_id: str, grund: str) -> None:
        if not await self._mod_check(interaction, "ban_members"):
            return
        if not discord_id.isdigit():
            await interaction.response.send_message("Ungültige Discord-ID.", ephemeral=True)
            return
        user = discord.Object(int(discord_id))
        try:
            await interaction.guild.unban(user, reason=f"{display(interaction.user)}: {grund}")
        except discord.NotFound:
            await interaction.response.send_message("Dieser Nutzer ist nicht gebannt.", ephemeral=True)
            return
        actor = self._actor(interaction)
        await db(lambda s: svc.moderate(s, actor, target_discord_id=discord_id, action="unban", reason=grund,
                                        executed=True))
        await interaction.response.send_message("Bann aufgehoben.", ephemeral=True)
        await self.bot.kick_outbox()

    @app_commands.command(name="verwarnungen", description="Moderationshistorie eines Mitglieds anzeigen")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def history_cmd(self, interaction: discord.Interaction, mitglied: discord.User) -> None:
        if not await self._mod_check(interaction, "moderate_members"):
            return
        try:
            data = await db(lambda s: svc.user_history(s, str(mitglied.id)))
        except ServiceError:
            await interaction.response.send_message("Keine Einträge vorhanden.", ephemeral=True)
            return
        active = [w for w in data["warnings"] if w["active"]]
        lines = [f"**Aktive Verwarnungen:** {len(active)}"]
        for a in data["actions"][:15]:
            lines.append(f"`{a['created_at'][:10]}` {a['label']} – {truncate(a['reason'], 80)} ({a['moderator_name']})")
        await interaction.response.send_message("\n".join(lines) or "Keine Einträge.", ephemeral=True)

    # ------------------------------------------------------------------ Auto-Moderation
    async def _automod_rules(self) -> list[dict]:
        if time.monotonic() - self._rules_loaded > 60:
            self._rules = await db(lambda s: [svc.serialize_rule(r) for r in s.scalars(
                select(AutoModRule).where(AutoModRule.enabled.is_(True)))])
            self._rules_loaded = time.monotonic()
        return self._rules

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or message.guild.id != self.bot.guild_id:
            return
        if not isinstance(message.author, discord.Member) or message.author.guild_permissions.manage_messages:
            return
        try:
            rules = await self._automod_rules()
        except Exception as exc:  # noqa: BLE001
            log.warning("Auto-Moderation: Regeln nicht ladbar: %s", exc)
            return
        rule = svc.evaluate_rules(rules, message.content, len(message.mentions) + len(message.role_mentions))
        if rule is None:
            return
        reason = f"Auto-Moderation: {rule['name']}"
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        actor = Actor(name="Auto-Moderation", source="automod")
        if rule["action"] == "warn":
            await db(lambda s: svc.moderate(s, actor, target_discord_id=str(message.author.id), action="warn",
                                            reason=reason, target_name=message.author.name))
        elif rule["action"] == "timeout":
            try:
                await message.author.timeout(timedelta(minutes=rule["timeout_minutes"]), reason=reason)
                await db(lambda s: svc.moderate(s, actor, target_discord_id=str(message.author.id), action="timeout",
                                                reason=reason, duration_minutes=rule["timeout_minutes"],
                                                target_name=message.author.name, executed=True))
            except discord.HTTPException as exc:
                await log_error("Auto-Moderation Timeout", exc)
        await self.bot.kick_outbox()

    # ------------------------------------------------------------------ Outbox: Moderation aus dem Panel
    async def execute_moderation(self, payload: dict) -> None:
        action_id = int(payload["action_id"])

        def load(s: Session):
            ma = s.get(ModerationAction, action_id)
            return None if ma is None else (ma.action, ma.target.discord_id, ma.reason, ma.duration_minutes,
                                            ma.moderator_name, ma.status)
        row = await db(load)
        if row is None or row[5] != "pending":
            return
        action, target_id, reason, minutes, moderator, _ = row
        guild = self.bot.get_guild(self.bot.guild_id)
        if guild is None:
            raise PermanentError("Discord-Server nicht verfügbar.")
        audit_reason = truncate(f"{moderator} (Webpanel): {reason}", 500)
        try:
            if action in ("timeout", "untimeout", "kick"):
                member = guild.get_member(int(target_id)) or await guild.fetch_member(int(target_id))
                if action == "timeout":
                    await member.timeout(timedelta(minutes=int(minutes)), reason=audit_reason)
                elif action == "untimeout":
                    await member.timeout(None, reason=audit_reason)
                else:
                    await member.kick(reason=audit_reason)
            elif action == "ban":
                await guild.ban(discord.Object(int(target_id)), reason=audit_reason,
                                delete_message_seconds=int(payload.get("delete_message_days") or 0) * 86400)
            elif action == "unban":
                await guild.unban(discord.Object(int(target_id)), reason=audit_reason)
        except (discord.NotFound, discord.Forbidden) as exc:
            err = str(exc)
            await db(lambda s: svc.finish_moderation(s, action_id, err))
            raise PermanentError(f"Moderation nicht möglich: {exc}") from exc
        await db(lambda s: svc.finish_moderation(s, action_id, None))

    async def moderation_log(self, payload: dict) -> None:
        channel_id = await self.bot.cfg.snowflake("channels.moderation_log")
        if not channel_id:
            return
        data = await db(lambda s: (lambda ma: svc.serialize_mod(ma) if ma else None)(s.get(ModerationAction, int(payload["action_id"]))))
        if data is None:
            return
        color = {"warn": COLOR_WARN, "ban": COLOR_DANGER, "kick": COLOR_DANGER, "unban": COLOR_OK}.get(data["action"], COLOR_DEFAULT)
        embed = discord.Embed(title=f"{data['label']} – {data['target']['username']}", color=color,
                              description=truncate(data["reason"], 2000))
        embed.add_field(name="Mitglied", value=f"<@{data['target']['discord_id']}> ({data['target']['discord_id']})")
        embed.add_field(name="Moderator", value=data["moderator_name"])
        embed.add_field(name="Quelle", value=data["source"])
        if data["duration_minutes"]:
            embed.add_field(name="Dauer", value=f"{data['duration_minutes']} Minuten")
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    # ------------------------------------------------------------------ Ankündigungen
    async def publish_announcement(self, payload: dict) -> None:
        ann_id = int(payload["announcement_id"])
        data = await db(lambda s: (lambda a: svc.serialize_announcement(a) if a else None)(s.get(Announcement, ann_id)))
        if data is None or data["status"] == "sent":
            return
        try:
            channel = self.bot.get_channel(int(data["channel_id"])) or await self.bot.fetch_channel(int(data["channel_id"]))
            mention = f"<@&{data['mention_role_id']}>" if data.get("mention_role_id") else None
            allowed = discord.AllowedMentions(roles=[discord.Object(int(data["mention_role_id"]))]
                                              if data.get("mention_role_id") else False, users=False, everyone=False)
            if data["as_embed"]:
                embed = discord.Embed(title=truncate(data["title"] or "", 256) or None,
                                      description=truncate(data["content"], 4000), color=parse_color(data.get("color")))
                if data.get("footer"):
                    embed.set_footer(text=truncate(data["footer"], 2000))
                if data.get("image_url"):
                    embed.set_image(url=data["image_url"])
                msg = await channel.send(content=mention, embed=embed, allowed_mentions=allowed)
            else:
                text = (f"**{data['title']}**\n" if data.get("title") else "") + data["content"]
                msg = await channel.send(content=truncate(((mention + "\n") if mention else "") + text, 2000),
                                         allowed_mentions=allowed)
        except (discord.NotFound, discord.Forbidden) as exc:
            err = str(exc)
            await db(lambda s: _mark_announcement(s, ann_id, None, err))
            raise PermanentError(str(exc)) from exc

        await db(lambda s: _mark_announcement(s, ann_id, msg.id, None))

    async def delete_message(self, payload: dict) -> None:
        channel = self.bot.get_channel(int(payload["channel_id"])) or await self.bot.fetch_channel(int(payload["channel_id"]))
        try:
            await channel.get_partial_message(int(payload["message_id"])).delete()
        except discord.NotFound:
            pass

    # ------------------------------------------------------------------ Umfragen
    async def publish_poll(self, payload: dict) -> None:
        poll_id = int(payload["poll_id"])

        def load(s: Session):
            p = s.get(Poll, poll_id)
            return svc.serialize_poll(s, p) if p else None
        data = await db(load)
        if data is None or data["status"] not in ("queued",):
            return
        data["status"] = "open"
        channel = self.bot.get_channel(int(data["channel_id"])) or await self.bot.fetch_channel(int(data["channel_id"]))
        try:
            msg = await channel.send(embed=poll_embed(data), view=poll_view(data))
        except (discord.NotFound, discord.Forbidden) as exc:
            await db(lambda s: _set_poll(s, poll_id, "failed", None))
            raise PermanentError(str(exc)) from exc
        await db(lambda s: _set_poll(s, poll_id, "open", msg.id))

    async def update_poll(self, payload: dict) -> None:
        poll_id = int(payload["poll_id"])

        def load(s: Session):
            p = s.get(Poll, poll_id)
            return svc.serialize_poll(s, p) if p else None
        data = await db(load)
        if not data or not data["message_id"]:
            return
        channel = self.bot.get_channel(int(data["channel_id"])) or await self.bot.fetch_channel(int(data["channel_id"]))
        await channel.get_partial_message(int(data["message_id"])).edit(embed=poll_embed(data), view=poll_view(data))

    async def handle_vote(self, interaction: discord.Interaction, poll_id: int, option_id: int) -> None:
        ok, msg = await db(lambda s: svc.vote(s, poll_id, option_id, str(interaction.user.id)))
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        def load(s: Session):
            return svc.serialize_poll(s, s.get(Poll, poll_id))
        data = await db(load)
        await interaction.response.edit_message(embed=poll_embed(data), view=poll_view(data))
        await interaction.followup.send(msg, ephemeral=True)


def _mark_announcement(s: Session, ann_id: int, message_id, error) -> None:
    from ..services.base import emit
    a = s.get(Announcement, ann_id)
    if a is None:
        return
    if error:
        a.status, a.error = "failed", error[:300]
    else:
        a.status, a.message_id, a.sent_at, a.error = "sent", str(message_id), utcnow(), None
    emit(s, "announcements", a.id)


def _set_poll(s: Session, poll_id: int, status: str, message_id) -> None:
    from ..services.base import emit
    p = s.get(Poll, poll_id)
    if p:
        p.status = status
        if message_id:
            p.message_id = str(message_id)
        emit(s, "polls", p.id)


async def setup(bot) -> None:
    await bot.add_cog(CommunityCog(bot))


