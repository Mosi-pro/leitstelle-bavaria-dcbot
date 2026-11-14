"""Hilfsfunktionen des Discord-Bots: Datenbankzugriff im Thread-Pool, Einstellungen, Embeds."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional, TypeVar

import discord
from sqlalchemy.orm import Session

from ..core.db import session_scope, with_retry
from ..services import settings_service
from ..services.base import record_error

log = logging.getLogger("lbc.bot")
T = TypeVar("T")

COLOR_DEFAULT = 0x3B82F6
COLOR_OK = 0x22A06B
COLOR_WARN = 0xD29922
COLOR_DANGER = 0xCF4B4B
COLOR_MUTED = 0x6B7280


class PermanentError(Exception):
    """Fehler, bei dem eine Wiederholung nicht sinnvoll ist (z. B. fehlende Konfiguration)."""


def _run_tx(fn: Callable[..., T], *args, **kwargs) -> T:
    @with_retry(attempts=3)
    def inner() -> T:
        with session_scope() as s:
            return fn(s, *args, **kwargs)

    return inner()


async def db(fn: Callable[..., T], *args, **kwargs) -> T:
    """Führt fn(session, ...) in einer eigenen Transaktion im Thread-Pool aus (blockiert die Event-Loop nicht)."""
    return await asyncio.to_thread(_run_tx, fn, *args, **kwargs)


async def log_error(context: str, exc: BaseException | str) -> None:
    msg = str(exc)
    log.error("%s: %s", context, msg)
    try:
        await db(lambda s: record_error(s, "bot", msg, context))
    except Exception:  # noqa: BLE001
        pass


class SettingsCache:
    """Zwischenspeicher der Systemeinstellungen (Aktualisierung alle 20 Sekunden)."""

    def __init__(self, ttl: float = 20.0) -> None:
        self.ttl = ttl
        self._values: dict[str, Any] = {}
        self._loaded = 0.0

    async def refresh(self) -> None:
        def load(s: Session) -> dict:
            values = settings_service.get_values(s)
            values.pop("discord.bot_token", None)
            values.pop("discord.client_secret", None)
            return values

        self._values = await db(load)
        self._loaded = time.monotonic()

    async def get(self, key: str) -> Any:
        if time.monotonic() - self._loaded > self.ttl:
            try:
                await self.refresh()
            except Exception as exc:  # noqa: BLE001
                log.warning("Einstellungen konnten nicht geladen werden: %s", exc)
        return self._values.get(key)

    def peek(self, key: str) -> Any:
        return self._values.get(key)

    async def snowflake(self, key: str) -> Optional[int]:
        v = await self.get(key)
        return int(v) if v and str(v).isdigit() else None


def attachments_of(message: discord.Message) -> list[dict]:
    return [{"filename": a.filename, "url": a.url, "content_type": a.content_type, "size": a.size}
            for a in message.attachments]


def display(user: discord.abc.User) -> str:
    return getattr(user, "display_name", None) or user.name


def avatar(user: discord.abc.User) -> Optional[str]:
    try:
        return user.display_avatar.url
    except Exception:  # noqa: BLE001
        return None


def has_role(member: discord.abc.User, role_id: Optional[int]) -> bool:
    """Prüft anhand der aktuellen Discord-Rollen des Mitglieds (serverseitig im Bot)."""
    roles = getattr(member, "roles", None)
    if not role_id or not roles:
        return False
    return any(r.id == role_id for r in roles)


def truncate(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def parse_color(value: Optional[str], default: int = COLOR_DEFAULT) -> int:
    if value and value.startswith("#") and len(value) == 7:
        try:
            return int(value[1:], 16)
        except ValueError:
            pass
    return default


# ---------------------------------------------------------------------------
# Gespeicherte Panel-Nachrichten (Ticket-Menü, Bewerbungen, Verifizierung)
# ---------------------------------------------------------------------------
def get_panel_ref(s: Session, key: str) -> Optional[dict]:
    from ..core.models import SystemStatus
    row = s.get(SystemStatus, f"panel:{key}")
    return row.details if row and row.details else None


def set_panel_ref(s: Session, key: str, channel_id: int, message_id: int) -> None:
    from ..core.models import SystemStatus, utcnow
    row = s.get(SystemStatus, f"panel:{key}")
    if row is None:
        row = SystemStatus(component=f"panel:{key}", status="ok")
        s.add(row)
    row.details = {"channel_id": str(channel_id), "message_id": str(message_id)}
    row.last_seen = utcnow()


async def upsert_panel(bot, key: str, channel_id: Optional[int], embed: discord.Embed, view: discord.ui.View,
                       force_new: bool = False) -> None:
    """Aktualisiert die Panel-Nachricht oder veröffentlicht sie neu."""
    if not channel_id:
        raise PermanentError("Für dieses Panel ist kein Channel konfiguriert (Systemeinstellungen → Channels).")
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    ref = await db(get_panel_ref, key)
    if ref and int(ref["channel_id"]) == channel_id:
        msg = channel.get_partial_message(int(ref["message_id"]))
        if force_new:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
        else:
            try:
                await msg.edit(embed=embed, view=view)
                return
            except discord.NotFound:
                pass
    new = await channel.send(embed=embed, view=view)
    await db(set_panel_ref, key, channel_id, new.id)
