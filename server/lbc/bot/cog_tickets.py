"""Ticket-System in Discord: Menü, private Channels, Buttons, Gesprächsprotokoll, Schließen/Wiedereröffnen."""
from __future__ import annotations

import io
import logging
import time
from typing import Optional

import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import Ticket, TicketTranscript
from ..services import tickets as svc
from ..services.base import Actor, ServiceError
from ..services.team import find_member_by_discord_id, member_permissions
from .util import (
    COLOR_DEFAULT,
    COLOR_MUTED,
    COLOR_OK,
    PermanentError,
    attachments_of,
    avatar,
    db,
    display,
    has_role,
    log_error,
    truncate,
    upsert_panel,
)

log = logging.getLogger("lbc.bot.tickets")


class TicketOpenSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"lbc:ticket:open"):
    def __init__(self, options: Optional[list[discord.SelectOption]] = None) -> None:
        super().__init__(discord.ui.Select(
            custom_id="lbc:ticket:open", placeholder="Kategorie wählen, um ein Ticket zu erstellen",
            options=options or [discord.SelectOption(label="Keine Kategorie verfügbar", value="0")]))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []
        cat_id = int(values[0]) if values and str(values[0]).isdigit() else 0
        if not cat_id:
            await interaction.response.send_message("Derzeit sind keine Ticketkategorien verfügbar.", ephemeral=True)
            return
        await interaction.response.send_modal(TicketModal(cat_id))


class TicketModal(discord.ui.Modal, title="Ticket erstellen"):
    subject = discord.ui.TextInput(label="Worum geht es?", style=discord.TextStyle.paragraph, max_length=1000,
                                   placeholder="Beschreibe dein Anliegen kurz.")

    def __init__(self, category_id: int) -> None:
        super().__init__(timeout=600)
        self.category_id = category_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog: TicketCog = interaction.client.get_cog("TicketCog")  # type: ignore[assignment]
        await cog.open_ticket(interaction, self.category_id, str(self.subject.value))


class TicketControl(discord.ui.DynamicItem[discord.ui.Button], template=r"lbc:ticket:(?P<action>claim|close|reopen)"):
    LABELS = {"claim": ("Übernehmen", discord.ButtonStyle.primary), "close": ("Schließen", discord.ButtonStyle.danger),
              "reopen": ("Wiedereröffnen", discord.ButtonStyle.success)}

    def __init__(self, action: str) -> None:
        label, style = self.LABELS[action]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"lbc:ticket:{action}"))
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: TicketCog = interaction.client.get_cog("TicketCog")  # type: ignore[assignment]
        await cog.handle_control(interaction, self.action)


def controls_view(closed: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    if closed:
        view.add_item(TicketControl("reopen"))
    else:
        view.add_item(TicketControl("claim"))
        view.add_item(TicketControl("close"))
    return view


class TicketCog(commands.Cog, name="TicketCog"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.ticket_channels: set[int] = set()
        self.staff_ids: set[int] = set()
        self._staff_loaded = 0.0

    async def load_channels(self) -> None:
        ids = await db(lambda s: [int(c) for c in s.scalars(select(Ticket.channel_id).where(
            Ticket.channel_id.is_not(None), Ticket.status != "closed"))])
        self.ticket_channels = set(ids)

    async def _staff(self) -> set[int]:
        if time.monotonic() - self._staff_loaded > 60:
            def load(s: Session):
                from ..core.models import TeamMember
                out = set()
                for m in s.scalars(select(TeamMember).where(TeamMember.status == "active")):
                    if member_permissions(m) & {"tickets.manage", "tickets.assign"}:
                        out.add(int(m.user.discord_id))
                return out
            try:
                self.staff_ids = await db(load)
                self._staff_loaded = time.monotonic()
            except Exception as exc:  # noqa: BLE001
                log.warning("Teamliste nicht ladbar: %s", exc)
        return self.staff_ids

    async def is_staff(self, user: discord.abc.User) -> bool:
        support = await self.bot.cfg.snowflake("roles.support")
        return has_role(user, support) or user.id in await self._staff()

    # ------------------------------------------------------------------ Panel
    async def refresh_panel(self, payload: dict) -> None:
        cats = await db(lambda s: [(c.id, c.name, c.description, c.emoji) for c in svc.list_categories(s, True)])
        embed = discord.Embed(
            title="Support – Leitstelle Bavaria",
            description="Wähle unten eine Kategorie, um ein privates Ticket zu erstellen. "
                        "Nur du und das Support-Team können das Ticket sehen.", color=COLOR_DEFAULT)
        options = []
        for cid, name, desc, emoji in cats[:25]:
            options.append(discord.SelectOption(label=truncate(name, 100), value=str(cid),
                                                description=truncate(desc or "", 100) or None,
                                                emoji=emoji or None))
        view = discord.ui.View(timeout=None)
        view.add_item(TicketOpenSelect(options))
        await upsert_panel(self.bot, "ticket", await self.bot.cfg.snowflake("channels.ticket_panel"), embed, view,
                           bool(payload.get("force_new")))

    # ------------------------------------------------------------------ Erstellung
    async def open_ticket(self, interaction: discord.Interaction, category_id: int, subject: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        user = interaction.user

        def create(s: Session):
            t = svc.create_ticket(s, str(user.id), display(user), category_id, subject, avatar(user))
            return {"id": t.id, "number": t.number,
                    "discord_category_id": t.category.discord_category_id if t.category else None,
                    "support_role_id": t.category.support_role_id if t.category else None,
                    "category": t.category.name if t.category else "Ticket"}

        try:
            info = await db(create)
        except ServiceError as exc:
            await interaction.followup.send(exc.message, ephemeral=True)
            return
        try:
            channel = await self._create_channel(guild, user, info, subject)
        except discord.HTTPException as exc:
            err = str(exc)
            await db(lambda s: svc.mark_creation_failed(s, info["id"], err))
            await log_error(f"Ticket #{info['number']} Channel", exc)
            await interaction.followup.send("Der Ticket-Channel konnte nicht erstellt werden. Bitte informiere das Team.",
                                            ephemeral=True)
            return
        await db(lambda s: svc.set_channel(s, info["id"], str(channel.id)))
        self.ticket_channels.add(channel.id)
        await interaction.followup.send(f"Dein Ticket wurde erstellt: {channel.mention}", ephemeral=True)

    async def _create_channel(self, guild: discord.Guild, user, info: dict, subject: str) -> discord.TextChannel:
        support_id = int(info["support_role_id"]) if info["support_role_id"] else await self.bot.cfg.snowflake("roles.support")
        cat_id = int(info["discord_category_id"]) if info["discord_category_id"] else \
            await self.bot.cfg.snowflake("channels.ticket_category")
        category = guild.get_channel(cat_id) if cat_id else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True,
                                              read_message_history=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True,
                                                  read_message_history=True, embed_links=True, attach_files=True,
                                                  manage_messages=True),
        }
        support_role = guild.get_role(support_id) if support_id else None
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                                   attach_files=True, read_message_history=True)
        channel = await guild.create_text_channel(
            name=f"ticket-{info['number']:04d}", category=category if isinstance(category, discord.CategoryChannel) else None,
            overwrites=overwrites, topic=truncate(f"Ticket #{info['number']:04d} · {info['category']} · {display(user)}", 1000),
            reason=f"Ticket #{info['number']:04d}")
        welcome = await self.bot.cfg.get("tickets.welcome_message") or ""
        embed = discord.Embed(title=f"Ticket #{info['number']:04d} – {info['category']}",
                              description=truncate(subject, 3000), color=COLOR_DEFAULT)
        embed.add_field(name="Erstellt von", value=user.mention)
        embed.set_footer(text=truncate(welcome, 2000))
        mention = user.mention + (f" {support_role.mention}" if support_role else "")
        await channel.send(content=mention, embed=embed, view=controls_view(),
                           allowed_mentions=discord.AllowedMentions(users=[user],
                                                                    roles=[support_role] if support_role else False))
        return channel

    # ------------------------------------------------------------------ Buttons
    async def _actor(self, user) -> Actor:
        def load(s: Session):
            m = find_member_by_discord_id(s, str(user.id))
            return m.id if m else None
        mid = await db(load)
        return Actor(name=display(user), discord_id=str(user.id), member_id=mid, source="discord")

    async def handle_control(self, interaction: discord.Interaction, action: str) -> None:
        ticket = await db(lambda s: (lambda t: svc.serialize(t) if t else None)(svc.get_by_channel(s, str(interaction.channel_id))))
        if ticket is None:
            await interaction.response.send_message("Dieser Channel gehört zu keinem Ticket.", ephemeral=True)
            return
        staff = await self.is_staff(interaction.user)
        is_creator = ticket["creator"]["discord_id"] == str(interaction.user.id)
        actor = await self._actor(interaction.user)
        try:
            if action == "claim":
                if not staff or actor.member_id is None:
                    await interaction.response.send_message("Nur Teammitglieder können Tickets übernehmen.", ephemeral=True)
                    return
                await db(lambda s: svc.claim(s, actor, ticket["id"], actor.member_id))
                await interaction.response.send_message(f"{interaction.user.mention} hat das Ticket übernommen.",
                                                        allowed_mentions=discord.AllowedMentions.none())
            elif action == "close":
                if not staff and not is_creator:
                    await interaction.response.send_message("Nur der Ersteller oder das Team kann schließen.", ephemeral=True)
                    return
                await interaction.response.send_message("Das Ticket wird geschlossen …")
                await db(lambda s: svc.close(s, actor, ticket["id"], None))
            elif action == "reopen":
                if not staff and not is_creator:
                    await interaction.response.send_message("Nur der Ersteller oder das Team kann wiedereröffnen.",
                                                            ephemeral=True)
                    return
                await interaction.response.send_message("Das Ticket wird wiedereröffnet …")
                await db(lambda s: svc.reopen(s, actor, ticket["id"]))
        except ServiceError as exc:
            if interaction.response.is_done():
                await interaction.followup.send(exc.message, ephemeral=True)
            else:
                await interaction.response.send_message(exc.message, ephemeral=True)
            return
        await self.bot.kick_outbox()

    # ------------------------------------------------------------------ Gesprächsprotokoll
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.channel.id not in self.ticket_channels:
            return
        staff = await self.is_staff(message.author)
        try:
            await db(lambda s: svc.log_message(s, str(message.channel.id), str(message.id), str(message.author.id),
                                               display(message.author), message.content, attachments_of(message), staff))
        except Exception as exc:  # noqa: BLE001
            await log_error(f"Ticketnachricht {message.id}", exc)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.channel_id in self.ticket_channels and "content" in payload.data:
            await db(lambda s: svc.mark_message_edited(s, str(payload.message_id), payload.data["content"]))

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.channel_id in self.ticket_channels:
            await db(lambda s: svc.mark_message_deleted(s, str(payload.message_id)))

    # ------------------------------------------------------------------ Outbox-Aufträge
    async def _channel(self, channel_id) -> discord.abc.Messageable:
        cid = int(channel_id)
        return self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)

    async def send_message(self, payload: dict) -> None:
        channel = await self._channel(payload["channel_id"])
        msg = await channel.send(f"**{payload.get('author', 'Team')} (Team):** {payload['content']}",
                                 allowed_mentions=discord.AllowedMentions.none())

        def log_it(s: Session):
            m = svc.log_message(s, str(payload["channel_id"]), str(msg.id), str(payload.get("author_discord_id") or "0"),
                                payload.get("author", "Team"), payload["content"], None, True)
            if m:
                m.source = "panel"
        await db(log_it)

    async def system_message(self, payload: dict) -> None:
        channel = await self._channel(payload["channel_id"])
        mention = f"<@{payload['mention_creator']}> " if payload.get("mention_creator") else ""
        embed = discord.Embed(description=truncate(payload["content"], 2000), color=COLOR_MUTED)
        await channel.send(content=mention or None, embed=embed,
                           allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))

    async def close_channel(self, payload: dict) -> None:
        cid = int(payload["channel_id"])
        self.ticket_channels.discard(cid)
        transcript = await db(lambda s: (lambda t: t.content if t else "")(s.get(TicketTranscript, int(payload["transcript_id"]))))
        number = int(payload["number"])
        fname = f"ticket-{number:04d}-transkript.txt"
        log_channel_id = await self.bot.cfg.snowflake("channels.ticket_log")
        if log_channel_id:
            try:
                log_channel = await self._channel(log_channel_id)
                await log_channel.send(
                    f"Ticket **#{number:04d}** geschlossen von {payload.get('closed_by')}."
                    + (f" Grund: {payload['reason']}" if payload.get("reason") else ""),
                    file=discord.File(io.BytesIO(transcript.encode()), filename=fname),
                    allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                log.warning("Transkript konnte nicht gepostet werden: %s", exc)
        if await self.bot.cfg.get("notifications.dm_on_ticket_close"):
            try:
                user = await self.bot.fetch_user(int(payload["creator_discord_id"]))
                await user.send(f"Dein Ticket **#{number:04d}** bei Leitstelle Bavaria wurde geschlossen."
                                + (f"\nGrund: {payload['reason']}" if payload.get("reason") else ""),
                                file=discord.File(io.BytesIO(transcript.encode()), filename=fname))
            except discord.HTTPException:
                pass  # DMs deaktiviert – kein Fehler
        try:
            channel = await self._channel(cid)
        except discord.NotFound:
            return
        if await self.bot.cfg.get("tickets.delete_channel_on_close"):
            await channel.delete(reason=f"Ticket #{number:04d} geschlossen")
            return
        creator = channel.guild.get_member(int(payload["creator_discord_id"])) or discord.Object(int(payload["creator_discord_id"]))
        await channel.set_permissions(creator, view_channel=True, send_messages=False, read_message_history=True,
                                      reason="Ticket geschlossen")
        await channel.edit(name=f"closed-{number:04d}")
        embed = discord.Embed(title="Ticket geschlossen",
                              description=f"Geschlossen von {payload.get('closed_by')}."
                              + (f"\nGrund: {payload['reason']}" if payload.get("reason") else ""), color=COLOR_MUTED)
        await channel.send(embed=embed, view=controls_view(closed=True))

    async def reopen_channel(self, payload: dict) -> None:
        if not payload.get("channel_id"):
            raise PermanentError("Ticket besitzt keinen Discord-Channel.")
        number = int(payload["number"])
        try:
            channel = await self._channel(payload["channel_id"])
        except discord.NotFound:
            raise PermanentError("Der Ticket-Channel wurde gelöscht und kann nicht wiedereröffnet werden.") from None
        creator = channel.guild.get_member(int(payload["creator_discord_id"])) or discord.Object(int(payload["creator_discord_id"]))
        await channel.set_permissions(creator, view_channel=True, send_messages=True, attach_files=True,
                                      read_message_history=True, embed_links=True, reason="Ticket wiedereröffnet")
        await channel.edit(name=f"ticket-{number:04d}")
        self.ticket_channels.add(channel.id)
        embed = discord.Embed(title="Ticket wiedereröffnet", description=f"Wiedereröffnet von {payload.get('reopened_by')}.",
                              color=COLOR_OK)
        await channel.send(content=f"<@{payload['creator_discord_id']}>", embed=embed, view=controls_view())


async def setup(bot) -> None:
    await bot.add_cog(TicketCog(bot))


