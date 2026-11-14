"""Katalog aller Berechtigungen und Standardrollen."""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    "dashboard.view": "Dashboard anzeigen",
    "tickets.view": "Tickets anzeigen",
    "tickets.manage": "Tickets bearbeiten (Status, Priorität, Schließen, Notizen)",
    "tickets.assign": "Tickets zuweisen und übernehmen",
    "tickets.configure": "Ticketkategorien konfigurieren",
    "applications.view": "Bewerbungen anzeigen",
    "applications.review": "Bewerbungen bearbeiten und entscheiden",
    "applications.configure": "Bewerbungsformulare konfigurieren",
    "beta.view": "Beta-Tester anzeigen",
    "beta.manage": "Beta-Tester und Testgruppen verwalten",
    "bugs.view": "Bugs anzeigen",
    "bugs.manage": "Bugs bearbeiten, zuweisen und übergeben",
    "bugs.claim": "Bugs übernehmen",
    "versions.view": "Versionen anzeigen",
    "versions.manage": "Versionen und Changelogs verwalten",
    "community.view": "Community-Bereich anzeigen",
    "community.moderate": "Moderationsaktionen ausführen",
    "community.announce": "Ankündigungen und Umfragen veröffentlichen",
    "team.view": "Teamverwaltung anzeigen",
    "team.manage": "Teammitglieder, Rollen und Rechte verwalten",
    "controlling.view": "Controlling und Statistiken anzeigen",
    "reports.manage": "Reports erzeugen und veröffentlichen",
    "logs.view": "Audit-Logs anzeigen",
    "settings.manage": "Systemeinstellungen verwalten",
}

ALL_PERMISSIONS = frozenset(PERMISSIONS)

DEFAULT_ROLES: list[dict] = [
    {"name": "Inhaber", "position": 0, "is_owner": True, "permissions": sorted(ALL_PERMISSIONS),
     "description": "Vollzugriff auf alle Bereiche"},
    {"name": "Administration", "position": 10, "permissions": sorted(ALL_PERMISSIONS - {"settings.manage"}),
     "description": "Verwaltung aller Module außer Systemeinstellungen"},
    {"name": "Projektleitung", "position": 20, "permissions": [
        "dashboard.view", "tickets.view", "applications.view", "applications.review", "beta.view", "beta.manage",
        "bugs.view", "bugs.manage", "versions.view", "versions.manage", "community.view", "community.announce",
        "team.view", "controlling.view", "reports.manage", "logs.view"],
     "description": "Projekt- und Entwicklungssteuerung"},
    {"name": "Developer", "position": 30, "permissions": [
        "dashboard.view", "bugs.view", "bugs.manage", "bugs.claim", "versions.view", "versions.manage", "beta.view"],
     "description": "Entwickler – Bug-Bearbeitung und Versionen"},
    {"name": "Supportleitung", "position": 40, "permissions": [
        "dashboard.view", "tickets.view", "tickets.manage", "tickets.assign", "tickets.configure",
        "applications.view", "applications.review", "community.view", "community.moderate", "controlling.view"],
     "description": "Leitung des Supports"},
    {"name": "Supporter", "position": 50, "permissions": [
        "dashboard.view", "tickets.view", "tickets.manage", "tickets.assign", "community.view"],
     "description": "Bearbeitung von Support-Tickets"},
    {"name": "Beta-Team", "position": 60, "permissions": [
        "dashboard.view", "beta.view", "beta.manage", "bugs.view", "versions.view", "applications.view"],
     "description": "Betreuung der Beta-Tester"},
]


def effective_permissions(role_permissions: set[str], is_owner: bool, overrides: dict[str, bool]) -> set[str]:
    """Berechnet die wirksamen Rechte: Rolle ∪ erteilte Einzelrechte − entzogene Einzelrechte."""
    if is_owner:
        return set(ALL_PERMISSIONS)
    perms = set(role_permissions) & ALL_PERMISSIONS
    for perm, allow in overrides.items():
        if perm not in ALL_PERMISSIONS:
            continue
        if allow:
            perms.add(perm)
        else:
            perms.discard(perm)
    return perms
