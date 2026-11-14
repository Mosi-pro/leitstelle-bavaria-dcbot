"""Bewerbungen über Discord: Panel + /bewerben, mehrseitige Formulare (Modals) mit allen Feldtypen."""
from __future__ import annotations

import logging
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import Application, ApplicationType
from ..services import applications as svc
from ..services.base import ServiceError
from .util import COLOR_DEFAULT, avatar, db, display, truncate, upsert_panel

log = logging.getLogger("lbc.bot.applications")
PER_MODAL = 5  # Discord erlaubt maximal 5 Komponenten pro Modal


def load_form(s: Session, type_id: int) -> Optional[dict]:
    t = s.get(ApplicationType, type_id)
    if t is None or not t.active:
        return None
    return svc.serialize_type(t)


def chunk_questions(questions: list[dict]) -> list[list[dict]]:
    return [questions[i:i + PER_MODAL] for i in range(0, len(questions), PER_MODAL)]


def build_component(q: dict) -> discord.ui.Item:
    ftype = q["field_type"]
    if ftype in ("text", "textarea"):
        return discord.ui.TextInput(style=discord.TextStyle.paragraph if ftype == "textarea" else discord.TextStyle.short,
                                    required=q["required"], max_length=min(q["max_length"], 4000),
                                    custom_id=f"q{q['id']}")
    if ftype == "yesno":
        opts = [discord.SelectOption(label="Ja", value="Ja"), discord.SelectOption(label="Nein", value="Nein")]
        return discord.ui.Select(options=opts, custom_id=f"q{q['id']}", required=q["required"],
                                 min_values=1 if q["required"] else 0, max_values=1)
    opts = [discord.SelectOption(label=truncate(o, 100), value=o[:100]) for o in q["options"][:25]]
    multi = ftype == "multiselect"
    return discord.ui.Select(options=opts, custom_id=f"q{q['id']}", required=q["required"],
                             min_values=1 if q["required"] else 0, max_values=len(opts) if multi else 1)


class ApplicationFlow:
    """Hält den Zustand einer laufenden Bewerbung (im Speicher, max. 30 Minuten)."""

    def __init__(self, form: dict) -> None:
        self.form = form
        self.pages = chunk_questions(form["questions"])
        self.page = 0
        self.answers: dict[int, Any] = {}

    def modal(self) -> "ApplicationModal":
        return ApplicationModal(self)


class ApplicationModal(discord.ui.Modal):
    def __init__(self, flow: ApplicationFlow) -> None:
        total = len(flow.pages)
        title = truncate(flow.form["name"] + (f" ({flow.page + 1}/{total})" if total > 1 else ""), 45)
        super().__init__(title=title, timeout=1800)
        self.flow = flow
        self.inputs: dict[int, discord.ui.Item] = {}
        for q in flow.pages[flow.page]:
            comp = build_component(q)
            self.inputs[q["id"]] = comp
            label_text = truncate(q["label"], 45)
            description = truncate(q["label"], 100) if len(q["label"]) > 45 else None
            self.add_item(discord.ui.Label(text=label_text, description=description, component=comp))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        for qid, comp in self.inputs.items():
            if isinstance(comp, discord.ui.TextInput):
                self.flow.answers[qid] = comp.value
            elif isinstance(comp, discord.ui.Select):
                q = next(x for x in self.flow.form["questions"] if x["id"] == qid)
                self.flow.answers[qid] = list(comp.values) if q["field_type"] == "multiselect" else (
                    comp.values[0] if comp.values else "")
        self.flow.page += 1
        if self.flow.page < len(self.flow.pages):
            await interaction.response.send_message(
                f"Teil {self.flow.page} von {len(self.flow.pages)} gespeichert. Weiter mit dem nächsten Teil:",
                view=ContinueView(self.flow), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = interaction.user
        try:
            app_id = await db(lambda s: svc.submit(s, self.flow.form["id"], str(user.id), display(user),
                                                   self.flow.answers, avatar(user)).id)
        except ServiceError as exc:
            await interaction.followup.send(f"Deine Bewerbung konnte nicht gespeichert werden: {exc.message}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Vielen Dank! Deine **{self.flow.form['name']}** ist eingegangen (Nr. {app_id}). "
            "Du erhältst eine Nachricht, sobald sie bearbeitet wurde.", ephemeral=True)
        await interaction.client.kick_outbox()  # type: ignore[attr-defined]

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Fehler im Bewerbungsformular", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Es ist ein Fehler aufgetreten. Bitte erneut versuchen.", ephemeral=True)


class ContinueView(discord.ui.View):
    def __init__(self, flow: ApplicationFlow) -> None:
        super().__init__(timeout=1800)
        self.flow = flow

    @discord.ui.button(label="Weiter", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(self.flow.modal())


class ApplySelect(discord.ui.DynamicItem[discord.ui.Select], template=r"lbc:apply:select"):
    def __init__(self, options: Optional[list[discord.SelectOption]] = None) -> None:
        super().__init__(discord.ui.Select(custom_id="lbc:apply:select", placeholder="Bewerbungsart wählen",
                                           options=options or [discord.SelectOption(label="Keine verfügbar", value="0")]))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []
        type_id = int(values[0]) if values and str(values[0]).isdigit() else 0
        cog: ApplicationCog = interaction.client.get_cog("ApplicationCog")  # type: ignore[assignment]
        await cog.start(interaction, type_id)


class ApplicationCog(commands.Cog, name="ApplicationCog"):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def start(self, interaction: discord.Interaction, type_id: int) -> None:
        form = await db(load_form, type_id) if type_id else None
        if form is None or not form["questions"]:
            await interaction.response.send_message("Diese Bewerbungsart ist derzeit nicht verfügbar.", ephemeral=True)
            return

        def has_open(s: Session) -> bool:
            from ..core.models import DiscordUser
            return s.scalar(select(Application.id).join(DiscordUser, DiscordUser.id == Application.applicant_id).where(
                DiscordUser.discord_id == str(interaction.user.id), Application.type_id == type_id,
                Application.status.in_(svc.OPEN_STATUSES))) is not None

        if await db(has_open):
            await interaction.response.send_message("Du hast bereits eine offene Bewerbung dieser Art.", ephemeral=True)
            return
        await interaction.response.send_modal(ApplicationFlow(form).modal())

    async def type_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        rows = await db(lambda s: [(t.id, t.name) for t in s.scalars(select(ApplicationType).where(
            ApplicationType.active.is_(True)).order_by(ApplicationType.name))])
        return [app_commands.Choice(name=n, value=str(i)) for i, n in rows if current.lower() in n.lower()][:25]

    @app_commands.command(name="bewerben", description="Bewerbung einreichen (Beta-Tester, Team, …)")
    @app_commands.describe(art="Bewerbungsart")
    @app_commands.guild_only()
    async def apply_cmd(self, interaction: discord.Interaction, art: str) -> None:
        await self.start(interaction, int(art) if art.isdigit() else 0)

    @apply_cmd.autocomplete("art")
    async def _ac(self, interaction: discord.Interaction, current: str):
        return await self.type_autocomplete(interaction, current)

    # ------------------------------------------------------------------ Outbox
    async def refresh_panel(self, payload: dict) -> None:
        types = await db(lambda s: [(t.id, t.name, t.description) for t in s.scalars(
            select(ApplicationType).where(ApplicationType.active.is_(True)).order_by(ApplicationType.name))])
        embed = discord.Embed(title="Bewerbungen – Leitstelle Bavaria",
                              description="Wähle unten aus, wofür du dich bewerben möchtest. Das Formular öffnet sich "
                                          "direkt in Discord.", color=COLOR_DEFAULT)
        for _, name, desc in types[:10]:
            embed.add_field(name=name, value=truncate(desc or "–", 300), inline=False)
        options = [discord.SelectOption(label=truncate(n, 100), value=str(i), description=truncate(d or "", 100) or None)
                   for i, n, d in types[:25]]
        view = discord.ui.View(timeout=None)
        view.add_item(ApplySelect(options))
        await upsert_panel(self.bot, "applications", await self.bot.cfg.snowflake("channels.applications"), embed, view,
                           bool(payload.get("force_new")))

    async def notify_team(self, payload: dict) -> None:
        channel_id = await self.bot.cfg.snowflake("channels.application_log")
        if not channel_id:
            return  # Optional – Benachrichtigung erfolgt dann nur im Webpanel
        app_id = int(payload["application_id"])
        data = await db(lambda s: svc.serialize(svc.get_application(s, app_id), detail=True, session=s))
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        embed = discord.Embed(title=f"Neue {data['type']['name']} (Nr. {app_id})", color=COLOR_DEFAULT,
                              description=f"Bewerber: <@{data['applicant']['discord_id']}>")
        for a in data["answers"][:20]:
            embed.add_field(name=truncate(a["question"], 256), value=truncate(a["value"] or "–", 1024), inline=False)
        embed.set_footer(text="Bearbeitung im Webpanel → Bewerbungen")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot) -> None:
    await bot.add_cog(ApplicationCog(bot))
