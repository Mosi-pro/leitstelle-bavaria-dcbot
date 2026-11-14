"""Katalog der im Webpanel konfigurierbaren Systemeinstellungen."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    group: str
    type: str = "string"  # string | snowflake | int | bool | text | secret
    default: Any = None
    secret: bool = False
    env: Optional[str] = None  # Fallback auf .env-Variable
    help: str = ""
    restart: bool = False


SETTINGS: list[SettingDef] = [
    # Discord-Zugang
    SettingDef("discord.bot_token", "Discord Bot Token", "discord", "secret", secret=True, env="DISCORD_BOT_TOKEN",
               help="Token aus dem Discord Developer Portal (Bot). Änderung erfordert Bot-Neustart.", restart=True),
    SettingDef("discord.client_id", "Discord Client ID", "discord", "snowflake", env="DISCORD_CLIENT_ID"),
    SettingDef("discord.client_secret", "Discord Client Secret", "discord", "secret", secret=True,
               env="DISCORD_CLIENT_SECRET", help="OAuth2 Client Secret für den Panel-Login."),
    SettingDef("discord.guild_id", "Discord Server-ID", "discord", "snowflake", env="DISCORD_GUILD_ID", restart=True),
    SettingDef("discord.redirect_uri", "OAuth2 Redirect URL", "discord", "string", env="DISCORD_REDIRECT_URI",
               help="Muss exakt im Discord Developer Portal unter OAuth2 → Redirects eingetragen sein."),
    # Channels
    SettingDef("channels.ticket_panel", "Ticket-Channel", "channels", "snowflake",
               help="Channel, in dem das Ticket-Menü veröffentlicht wird."),
    SettingDef("channels.ticket_category", "Discord-Kategorie für Ticket-Channels", "channels", "snowflake",
               help="Standard-Kategorie, unter der private Ticket-Channels angelegt werden."),
    SettingDef("channels.ticket_log", "Ticket-Log-Channel (Transkripte)", "channels", "snowflake"),
    SettingDef("channels.bug_forum", "Bug-Forum-Channel", "channels", "snowflake",
               help="Forum-Channel, in dem Mitglieder Bugs melden."),
    SettingDef("channels.developer", "Developer-Channel", "channels", "snowflake",
               help="Interner Channel für Bug-Benachrichtigungen."),
    SettingDef("channels.announcements", "Ankündigungs-Channel", "channels", "snowflake"),
    SettingDef("channels.reports", "Report-Channel", "channels", "snowflake"),
    SettingDef("channels.welcome", "Begrüßungs-Channel", "channels", "snowflake"),
    SettingDef("channels.verification", "Verifizierungs-Channel", "channels", "snowflake"),
    SettingDef("channels.applications", "Bewerbungs-Channel (Panel)", "channels", "snowflake"),
    SettingDef("channels.application_log", "Bewerbungs-Eingang (Team-Channel)", "channels", "snowflake"),
    SettingDef("channels.moderation_log", "Moderations-Log-Channel", "channels", "snowflake"),
    # Rollen
    SettingDef("roles.developer", "Developer-Rollen-ID", "roles", "snowflake",
               help="Wird bei neuen Bugs erwähnt und darf Bugs übernehmen."),
    SettingDef("roles.support", "Support-Rollen-ID", "roles", "snowflake",
               help="Erhält Zugriff auf alle Ticket-Channels."),
    SettingDef("roles.beta", "Beta-Rollen-ID", "roles", "snowflake"),
    SettingDef("roles.verified", "Verifiziert-Rollen-ID", "roles", "snowflake"),
    SettingDef("roles.moderator", "Moderator-Rollen-ID", "roles", "snowflake",
               help="Darf Moderationsbefehle in Discord verwenden (zusätzlich zu Discord-Rechten)."),
    # Tickets
    SettingDef("tickets.auto_close_enabled", "Automatische Schließung aktiv", "tickets", "bool", default=True),
    SettingDef("tickets.auto_close_hours", "Inaktivität bis zur Schließung (Stunden)", "tickets", "int", default=72),
    SettingDef("tickets.auto_close_warning_hours", "Vorwarnung vor Schließung (Stunden)", "tickets", "int", default=24),
    SettingDef("tickets.max_open_per_user", "Maximal offene Tickets pro Mitglied", "tickets", "int", default=3),
    SettingDef("tickets.welcome_message", "Begrüßungstext im Ticket", "tickets", "text",
               default="Danke für dein Ticket! Ein Teammitglied meldet sich so schnell wie möglich."),
    SettingDef("tickets.delete_channel_on_close", "Channel beim Schließen löschen", "tickets", "bool", default=False,
               help="Wenn deaktiviert, wird der Channel gesperrt und kann wiedereröffnet werden."),
    # Bugs
    SettingDef("bugs.post_resolution_in_thread", "Abschluss im Forum-Thread veröffentlichen", "bugs", "bool", default=True),
    SettingDef("bugs.ping_developer_role", "Developer-Rolle bei neuen Bugs erwähnen", "bugs", "bool", default=True),
    # Community
    SettingDef("community.welcome_message", "Begrüßungsnachricht", "community", "text",
               default="Willkommen bei Leitstelle Bavaria, {mention}! Bitte verifiziere dich im Verifizierungs-Channel."),
    SettingDef("community.verification_enabled", "Verifizierung aktiv", "community", "bool", default=True),
    SettingDef("community.warn_threshold_timeout", "Timeout ab Anzahl aktiver Verwarnungen (0 = aus)",
               "community", "int", default=3),
    # Benachrichtigungen
    SettingDef("notifications.new_application_ping", "Team bei neuen Bewerbungen im Discord benachrichtigen",
               "notifications", "bool", default=True),
    SettingDef("notifications.dm_on_application_decision", "Bewerber per DM über Entscheidung informieren",
               "notifications", "bool", default=True),
    SettingDef("notifications.dm_on_ticket_close", "Ersteller beim Schließen per DM informieren",
               "notifications", "bool", default=True),
    # Reports
    SettingDef("reports.weekly_enabled", "Wöchentlicher Report aktiv", "reports", "bool", default=True),
    SettingDef("reports.weekday", "Wochentag für Report (0=Montag)", "reports", "int", default=0),
    SettingDef("reports.hour", "Uhrzeit für Report (Stunde, UTC)", "reports", "int", default=8),
    # Datenschutz / Aufbewahrung
    SettingDef("retention.closed_ticket_days", "Ticketnachrichten nach Schließung aufbewahren (Tage)",
               "retention", "int", default=365),
    SettingDef("retention.audit_days", "Audit-Logs aufbewahren (Tage)", "retention", "int", default=730),
    SettingDef("retention.outbox_days", "Erledigte Synchronisationsaufträge aufbewahren (Tage)",
               "retention", "int", default=14),
    # Backup
    SettingDef("backup.keep_count", "Anzahl aufzubewahrender lokaler Backups", "backup", "int", default=14),
    SettingDef("backup.daily_hour", "Uhrzeit des täglichen Backups (Stunde, lokale Zeit)", "backup", "int", default=3),
]

SETTINGS_BY_KEY = {s.key: s for s in SETTINGS}

GROUP_LABELS = {
    "discord": "Discord-Anwendung",
    "channels": "Channels",
    "roles": "Rollen",
    "tickets": "Tickets",
    "bugs": "Bug-Management",
    "community": "Community",
    "notifications": "Benachrichtigungen",
    "reports": "Reports",
    "retention": "Datenschutz und Aufbewahrung",
    "backup": "Backup",
}
