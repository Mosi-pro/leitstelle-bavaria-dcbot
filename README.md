# Leitstelle Bavaria Control

Verwaltungs- und Controlling-Software für das Projekt und den Discord-Server **Leitstelle Bavaria**.
Sie besteht aus einem **Discord-Bot** und einem **Webpanel**, die gemeinsam auf eine zentrale
**STRATO-MySQL-Datenbank** zugreifen.

| Bereich | Funktion |
|---|---|
| Dashboard | Live-Kennzahlen zu Community, Tickets, Bewerbungen, Beta, Bugs, Team und Systemstatus |
| Tickets | Ticket-Menü in Discord, private Channels, Zuweisung, Priorität, interne Notizen, Transkripte, Auto-Schließung |
| Bewerbungen | Konfigurierbare Formulare (Text, mehrzeilig, Auswahl, Mehrfachauswahl, Ja/Nein), Bewertungen, Verlauf, DM-Benachrichtigung, Rollenvergabe |
| Beta-Tester | Tester, Testgruppen, Versionen, Historie, Einladungen zu Testphasen |
| Bug-Management | Automatische Erfassung aus dem Discord-Forum, Entwickler-Ping mit echter Rollen-Erwähnung, atomare Übernahme per Button, Übergabe, Synchronisierung |
| Entwicklung | Versionsverwaltung mit Status, Changelog, Bugs, Entwicklern und Testgruppen |
| Community | Verifizierung, Begrüßung, Moderation (Panel und Slash-Befehle), Auto-Moderation, Ankündigungen, Vorlagen, Umfragen |
| Teamverwaltung | Teammitglieder, Rollen, individuelle Berechtigungen |
| Controlling | Statistiken, Zeitreihen, Wochenreports (automatisch in Discord) |
| Audit-Logs | Unveränderliches Protokoll aller administrativen Aktionen |
| Systemeinstellungen | Discord-Zugang, Channels, Rollen, Automatisierungen, Datenbank, Synchronisierung, Backups |

---

## Inhalt

1. [Architektur](#1-architektur)
2. [Voraussetzungen](#2-voraussetzungen)
3. [Vorbereitung: STRATO-Datenbank](#3-vorbereitung-strato-datenbank)
4. [Vorbereitung: Discord](#4-vorbereitung-discord)
5. [Vorbereitung: Tailscale](#5-vorbereitung-tailscale)
6. [Installation](#6-installation)
7. [Erste Einrichtung im Webpanel](#7-erste-einrichtung-im-webpanel)
8. [Alle Zugangsdaten erklärt](#8-alle-zugangsdaten-erklärt)
9. [Betrieb: Start, Stopp, Logs](#9-betrieb-start-stopp-logs)
10. [Updates](#10-updates)
11. [Backups und Wiederherstellung](#11-backups-und-wiederherstellung)
12. [Deinstallation](#12-deinstallation)
13. [Sicherheit und Datenschutz](#13-sicherheit-und-datenschutz)
14. [Fehlerbehebung](#14-fehlerbehebung)
15. [Entwicklung und Tests](#15-entwicklung-und-tests)

---

## 1. Architektur

```
                 Internet (ausgehend)                       Tailscale-VPN (nur Teamgeräte)
                        │                                              │
   Discord API ◄────────┤                                              ▼
                        │       Dell Wyse 5070 (Debian, Docker)   ┌──────────┐
                  ┌─────┴─────┐   ┌───────────┐   ┌───────────┐   │  web     │ HTTPS :443
                  │   bot     │   │  backend  │◄──┤  (Nginx + │◄──┤ (nur an  │
                  │discord.py │   │  FastAPI  │   │   React)  │   │Tailscale-│
                  └─────┬─────┘   └─────┬─────┘   └───────────┘   │   IP)    │
                        │               │                         └──────────┘
                        └──────┬────────┘
                               ▼ TLS
                     STRATO MySQL (extern)  ← einzige Datenhaltung
```

* **backend** – REST-API + WebSocket (Live-Aktualisierung), Discord-OAuth2-Login, Berechtigungen,
  zeitgesteuerte Automatisierungen (Auto-Schließung, Wochenreport, Datenschutz-Bereinigung …).
* **bot** – Discord-Bot. Er schreibt Ereignisse direkt in die Datenbank und führt Aufträge aus dem
  Webpanel aus.
* **web** – Nginx mit dem gebauten React-Panel, Reverse Proxy zur API, HTTPS. Lauscht **nur** auf der
  Tailscale-IP.
* **migrate** – führt beim Start die Datenbankmigrationen (Alembic) aus und beendet sich.

**Synchronisierung Panel ↔ Discord:** Änderungen im Panel, die Discord betreffen (z. B. Bugstatus →
Entwickler-Nachricht aktualisieren), werden als Auftrag in der Tabelle `discord_outbox` gespeichert. Der
Bot arbeitet diese Aufträge ab, wiederholt fehlgeschlagene mit wachsendem Abstand (bis 8 Versuche) und
verhindert Duplikate über eindeutige Schlüssel. Ereignisse aus Discord (z. B. Bug-Übernahme) schreibt der
Bot direkt in die Datenbank; das Panel erfährt davon über den WebSocket sofort. Der Zustand ist damit auch
nach Verbindungsabbrüchen, Neustarts oder doppelten Discord-Ereignissen konsistent – siehe
*Systemeinstellungen → Synchronisierung*.

Es gibt **keine** lokale Datenbank und keinen SQLite-Ersatz. Ist STRATO nicht erreichbar, melden alle
Komponenten das verständlich und arbeiten nach Wiederherstellung der Verbindung weiter.

## 2. Voraussetzungen

* Dell Wyse 5070 (oder vergleichbar, x86_64) mit **Debian 12** (11 und 13 werden ebenfalls unterstützt)
* mindestens 1,5 GB RAM (empfohlen 4 GB+), 3 GB freier Speicher
* ausgehende Internetverbindung (Discord, STRATO, Paketquellen)
* ein Benutzer mit `sudo`-Rechten
* eine STRATO-MySQL-Datenbank, ein Discord-Server und ein Tailscale-Konto

Docker, Docker Compose und Tailscale installiert `install.sh` bei Bedarf automatisch.

## 3. Vorbereitung: STRATO-Datenbank

1. Im STRATO-Kundenbereich unter **Datenbanken** eine **MySQL-Datenbank** anlegen.
2. Notiere: **Hostname**, **Port** (meist 3306), **Datenbankname**, **Benutzername**, **Passwort**.
   Die Werte stehen in der Datenbankverwaltung – es gibt keine Standardwerte, bitte nichts raten.
3. **Wichtig – externer Zugriff:** Die Datenbank liegt bei STRATO, die Anwendung läuft auf deinem
   Dell Wyse. Die Datenbank muss also **von außerhalb des STRATO-Hostings erreichbar** sein.
   * Je nach STRATO-Tarif sind MySQL-Datenbanken nur aus dem STRATO-Webhosting selbst erreichbar.
     Prüfe im Kundenbereich bzw. beim STRATO-Support, ob dein Tarif externen Zugriff erlaubt.
   * Falls eine **IP-Freigabe** nötig ist: die **öffentliche** IP-Adresse deines Internetanschlusses
     freigeben (nicht die Tailscale-IP). Bei wechselnder IP (übliche DSL-Anschlüsse) muss die Freigabe ggf.
     nach einem IP-Wechsel erneuert werden – oder ein Tarif mit freiem Zugriff/fester IP gewählt werden.
   * Ohne externen Zugriff kann die Software **nicht** betrieben werden; sie weicht bewusst nicht auf eine
     lokale Datenbank aus.
4. Der Datenbankbenutzer benötigt volle Rechte auf **seine** Datenbank (CREATE, ALTER, INDEX, REFERENCES,
   INSERT, UPDATE, DELETE, DROP). Das prüft die Installation automatisch.
5. **TLS:** Unterstützt der Server verschlüsselte Verbindungen, `DB_SSL_MODE=required` verwenden
   (bzw. `verify_identity` mit CA-Zertifikat). `preferred` verschlüsselt, wenn möglich.

Die Installation prüft vor dem Start: Erreichbarkeit, Zugangsdaten, Vorhandensein der Datenbank,
Berechtigungen, MySQL-Version (MySQL ≥ 5.7.8, empfohlen 8; MariaDB ≥ 10.5) und Verschlüsselung.
Dieselbe Prüfung jederzeit: `sudo docker compose run --rm migrate python -m lbc.tools.dbcheck`.

## 4. Vorbereitung: Discord

1. <https://discord.com/developers/applications> → **New Application** (z. B. „Leitstelle Bavaria Control“).
2. **Bot** → *Reset Token* → Token kopieren (= `DISCORD_BOT_TOKEN`).
   Unter *Privileged Gateway Intents* **Server Members Intent** und **Message Content Intent** aktivieren.
3. **OAuth2** → *Client ID* (= `DISCORD_CLIENT_ID`) und *Client Secret* (= `DISCORD_CLIENT_SECRET`).
   Unter **Redirects** die Adresse eintragen, die die Installation anzeigt, z. B.
   `https://wyse.dein-tailnet.ts.net/api/auth/callback` (= `DISCORD_REDIRECT_URI`).
4. Bot auf den Server einladen: **OAuth2 → URL Generator** → Scopes `bot` und `applications.commands`,
   Berechtigungen: *Manage Channels, Manage Roles, Kick Members, Ban Members, Moderate Members,
   View Channels, Send Messages, Send Messages in Threads, Manage Messages, Embed Links, Attach Files,
   Read Message History, Mention Everyone* (für Rollen-Erwähnungen) → Link öffnen und Server wählen.
   Die Bot-Rolle muss in der Rollenliste **über** den Rollen stehen, die der Bot vergeben soll.
5. In Discord **Einstellungen → Erweitert → Entwicklermodus** aktivieren. Dann per Rechtsklick
   „ID kopieren“ für: den Server (`DISCORD_GUILD_ID`) und dein eigenes Profil (`OWNER_DISCORD_ID`).
6. Benötigte Channels anlegen (Namen frei wählbar), z. B.: `#support` (Ticket-Menü), Kategorie
   „Tickets“, `#ticket-log`, **Forum-Channel** `#bug-meldungen`, `#dev-bugs` (intern), `#ankündigungen`,
   `#reports`, `#willkommen`, `#verifizierung`, `#bewerbungen`, `#bewerbungs-eingang` (intern),
   `#mod-log` (intern) sowie Rollen *Developer*, *Support*, *Beta*, *Verifiziert*, *Moderator*.
   Die Zuordnung erfolgt später bequem im Webpanel (Auswahllisten).

Der Discord-Login dient nur der **Identifikation**. Wer das Panel nutzen darf, legt ausschließlich die
Teamverwaltung fest; normale Discord-Nutzer werden abgewiesen.

## 5. Vorbereitung: Tailscale

Das Webpanel ist **nicht öffentlich** erreichbar, sondern nur über dein Tailscale-Netz (Tailnet).

1. Tailscale-Konto anlegen: <https://login.tailscale.com>.
2. Auf deinem PC/Tablet die Tailscale-App installieren und anmelden.
3. Den Dell Wyse fügt `install.sh` hinzu (`tailscale up` zeigt einen Anmeldelink).
4. **Empfohlen:** In der Tailscale-Adminkonsole unter **DNS** *MagicDNS* und *HTTPS Certificates*
   aktivieren. Dann erhält das Panel ein vom Browser vertrautes Zertifikat
   (`https://<gerätename>.<tailnet>.ts.net`). Ohne diese Option wird ein selbstsigniertes Zertifikat
   verwendet (einmalige Browserwarnung).
5. Optional per **ACL** einschränken, welche Geräte/Nutzer den Wyse auf Port 443 erreichen dürfen.

Es sind **keine Portweiterleitungen** am Router nötig. Der Bot baut nur ausgehende Verbindungen auf.

## 6. Installation

Auf dem Dell Wyse (Debian), als normaler Benutzer mit sudo:

```bash
sudo apt-get update && sudo apt-get install -y git
```

```bash
git clone https://github.com/mosi-pro/leitstelle-bavaria-dcbot.git
```

```bash
cd leitstelle-bavaria-dcbot && sudo ./install.sh
```

Das Skript führt durch alle Schritte und bricht bei Problemen mit einer verständlichen Meldung ab:

1. Debian-Version prüfen · 2. Systemvoraussetzungen (RAM, Speicher, Internet, Discord erreichbar)
3. Docker prüfen/installieren · 4. Docker Compose prüfen · 5. Tailscale prüfen/verbinden
6. Verzeichnisse anlegen · 7. `.env` erzeugen (inkl. zufälligem `APP_SECRET_KEY`)
8. STRATO-Zugangsdaten abfragen · 9. Discord-Zugangsdaten abfragen
10. Images bauen und **Datenbankverbindung testen** (bei Fehler: neu eingeben)
11. **Migrationen** ausführen · 12. HTTPS-Zertifikat · 13. Start + Autostart (systemd) + Cron-Jobs
14. **Healthchecks** (API, HTTPS, Bot) · 15. Status und Zugangsadresse anzeigen

Weitere Aufrufe:

* `sudo ./install.sh --reconfigure` – Zugangsdaten neu eingeben (z. B. neues DB-Passwort)
* `sudo ./install.sh --non-interactive` – vorhandene `.env` verwenden, keine Fragen

Ein Protokoll liegt in `install.log`.

**Autostart & Absturzschutz:** Der systemd-Dienst `leitstelle-bavaria` startet die Anwendung nach
einem Neustart (wartet auf die Tailscale-IP). Abgestürzte Container startet Docker automatisch neu
(`restart: unless-stopped`).

## 7. Erste Einrichtung im Webpanel

1. `https://<gerätename>.<tailnet>.ts.net` (oder `https://<Tailscale-IP>`) öffnen → **Mit Discord anmelden**.
   Der Inhaber (`OWNER_DISCORD_ID`) wird beim ersten Start automatisch mit Vollzugriff angelegt.
2. **Systemeinstellungen → Konfiguration:** Channels und Rollen auswählen (der Bot liefert die Listen,
   sobald er online ist). Wichtig für das Bug-Management: *Bug-Forum-Channel*, *Developer-Channel*,
   *Developer-Rollen-ID*.
3. **Systemeinstellungen → Discord-Panels & Bot:** Ticket-Menü, Bewerbungs-Panel und Verifizierung
   veröffentlichen.
4. **Teamverwaltung:** Teammitglieder per Discord-ID hinzufügen und Rollen zuweisen. Rechte können
   je Person zusätzlich erteilt oder entzogen werden.
5. **Bewerbungen → Formulare:** Fragen der Beta- und Team-Bewerbung anpassen.
6. **Entwicklung:** Spielversionen anlegen (z. B. 0.8.2 Beta). Bugs mit „Version: 0.8.2“ im Text
   werden automatisch zugeordnet.

**Discord-Befehle:** `/bewerben`, `/bug status|uebergeben|notiz|meine` (Entwickler),
`/verwarnen`, `/timeout`, `/kick`, `/bann`, `/entbannen`, `/verwarnungen` (Moderation).

## 8. Alle Zugangsdaten erklärt

Alle Werte stehen in der Datei `.env` im Projektverzeichnis (Rechte 600, nur root/Anwendung lesbar).

| Variable | Bedeutung | Herkunft |
|---|---|---|
| `DB_HOST` | Hostname des STRATO-MySQL-Servers | STRATO-Kundenbereich |
| `DB_PORT` | Port, meist `3306` | STRATO |
| `DB_NAME` | Name der Datenbank | STRATO |
| `DB_USER` / `DB_PASSWORD` | Datenbankbenutzer und Passwort | STRATO |
| `DB_SSL_MODE` | `disabled`, `preferred`, `required`, `verify_ca`, `verify_identity` | eigene Wahl |
| `DB_SSL_CA` | optional: CA-Datei für `verify_*` (im Container erreichbar ablegen) | STRATO/CA |
| `APP_SECRET_KEY` | verschlüsselt Geheimnisse in der DB. **Nie ändern** | automatisch erzeugt |
| `APP_BASE_URL` | Adresse des Panels | automatisch (Tailscale) |
| `DISCORD_BOT_TOKEN` | Token des Bots | Developer Portal → Bot |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | OAuth2-Daten für den Login | Developer Portal → OAuth2 |
| `DISCORD_GUILD_ID` | ID des Discord-Servers | Rechtsklick → ID kopieren |
| `DISCORD_REDIRECT_URI` | Rücksprungadresse nach dem Login | muss im Portal eingetragen sein |
| `OWNER_DISCORD_ID` | deine Discord-ID (Inhaber) | Rechtsklick → ID kopieren |
| `BIND_ADDRESS` | Tailscale-IP, an die das Panel gebunden wird | automatisch |

Discord-Token, Client-ID/-Secret, Server-ID und Redirect-URL können später auch im Webpanel geändert
werden; sie werden dort **verschlüsselt** gespeichert und **nie** im Klartext angezeigt. Die
Datenbankverbindung kann der Inhaber im Panel (*Systemeinstellungen → Datenbank*) testen und ändern;
gespeichert wird nur nach erfolgreichem Test.

## 9. Betrieb: Start, Stopp, Logs

```bash
sudo systemctl status leitstelle-bavaria
```

```bash
sudo docker compose ps
```

```bash
sudo docker compose logs -f --tail=100 bot
```

```bash
sudo systemctl restart leitstelle-bavaria
```

```bash
sudo systemctl stop leitstelle-bavaria
```

(Befehle mit `docker compose` im Projektverzeichnis ausführen.)

## 10. Updates

```bash
sudo ./update.sh
```

Ablauf: Konfiguration sichern → Datenbank-Backup → neue Version (`git pull`) → Images bauen →
Datenbank prüfen und migrieren → Neustart → Statusprüfung. Ist die Datenbank nicht erreichbar, wird
abgebrochen und die bisherige Version läuft weiter.

## 11. Backups und Wiederherstellung

**Automatisch:** Ein Cron-Job prüft stündlich und erstellt das Backup zur im Panel eingestellten Uhrzeit
(*Systemeinstellungen → Konfiguration → Backup*; Standard 3 Uhr, 14 Backups werden aufbewahrt).

**Manuell:**

```bash
sudo ./backup.sh
```

```bash
sudo ./backup.sh --list
```

Ein Backup (`backups/lbc-backup-JJJJMMTT-HHMMSS.tar.gz`, Rechte 600) enthält einen vollständigen Dump der
STRATO-Datenbank, die `.env` und ein Manifest mit SHA-256-Prüfsummen. **Die Backups enthalten
Zugangsdaten – sicher aufbewahren.** Empfehlung: regelmäßig zusätzlich auf ein externes Medium kopieren.

**Wiederherstellen:**

```bash
sudo ./restore.sh backups/lbc-backup-20261115-030701.tar.gz
```

Das Skript prüft die Datei (Format und Prüfsummen), warnt vor dem Überschreiben, verlangt die Eingabe
`WIEDERHERSTELLEN`, erstellt vorher ein Sicherheits-Backup des aktuellen Stands, stellt die Datenbank
wieder her, fragt separat nach der Konfiguration und startet die Anwendung neu.

## 12. Deinstallation

```bash
sudo ./uninstall.sh
```

Entfernt Container, Images, Autostart und Cron-Jobs. Konfiguration und lokale Backups werden nur nach
ausdrücklicher Bestätigung gelöscht. **Die STRATO-Datenbank wird niemals gelöscht oder verändert.**

## 13. Sicherheit und Datenschutz

* Panel nur im Tailscale-VPN, HTTPS, HSTS, strikte Content-Security-Policy, Rate-Limits beim Login.
* Login ausschließlich per Discord OAuth2 (Authorization Code Flow mit `state`-Prüfung); Zugang nur für
  aktive Teammitglieder. Sitzungen serverseitig (Token gehasht), HttpOnly-/Secure-/SameSite-Cookies,
  Ablauf nach `SESSION_HOURS`, Abmeldung anderer Sitzungen möglich.
* CSRF-Schutz für alle verändernden Anfragen; WebSocket mit Origin-Prüfung.
* Jede API-Aktion prüft die Berechtigung **serverseitig**; Sperrung eines Mitglieds wirkt sofort.
* SQL ausschließlich über SQLAlchemy (parametrisiert); Eingaben werden validiert.
* Geheimnisse in der DB mit Fernet (AES) verschlüsselt; Backups und `.env` mit Rechten 600.
* Audit-Logs haben keine Änderungs- oder Löschfunktion; nur die automatische Aufbewahrungsfrist
  (Standard 730 Tage) entfernt sehr alte Einträge.
* **Aufbewahrung/Datenminimierung** (*Systemeinstellungen → Datenschutz und Aufbewahrung*):
  entschiedene Bewerbungen werden nach der je Formular festgelegten Frist (Standard 180 Tage)
  **anonymisiert** (Antworten, Notizen, Bewertungen, Verlauf gelöscht; nur Status für Statistiken
  bleibt). Ticketnachrichten geschlossener Tickets werden nach 365 Tagen gelöscht, erledigte
  Synchronisationsaufträge nach 14 Tagen, Fehlerprotokolle nach 90 Tagen.
* Interne Notizen (Tickets, Bewerbungen, Bugs) werden nie an Ersteller/Bewerber übermittelt.

## 14. Fehlerbehebung

**Datenbank nicht erreichbar / Zugriff verweigert**
`sudo docker compose run --rm migrate python -m lbc.tools.dbcheck` zeigt die genaue Ursache.
* *Hostname nicht auflösbar* → `DB_HOST` prüfen.
* *Keine Verbindung* → externer Zugriff im STRATO-Tarif? IP-Freigabe der **öffentlichen** IP? Firewall?
* *Zugriff verweigert (1045)* → Benutzer/Passwort prüfen (`sudo ./install.sh --reconfigure`).
* *Host not allowed (1130)* → IP-Freigabe bei STRATO fehlt.
* *TLS verlangt, Server bietet keins* → `DB_SSL_MODE=preferred` oder STRATO-Support fragen.

**Bot offline**
`sudo docker compose logs --tail=50 bot`
* „Bot Token ungültig“ → Token im Portal neu erzeugen und im Panel/`.env` eintragen.
* „Intents aktivieren“ → Server Members und Message Content Intent einschalten.
* „nicht Mitglied des Servers“ → Bot einladen, `DISCORD_GUILD_ID` prüfen.
* „Discord ist nicht erreichbar“ → Internet/DNS des Wyse prüfen; der Bot versucht es automatisch erneut.

**Bugs werden nicht erfasst / keine Entwickler-Nachricht**
Bug-Forum-Channel muss ein **Forum** sein; Developer-Channel und -Rolle gesetzt? Bot braucht Lese-
und Schreibrechte. Unter *Systemeinstellungen → Synchronisierung* stehen Fehlermeldungen einzelner
Aufträge; „Wiederholen“ führt sie erneut aus. Beiträge, die während einer Bot-Pause entstanden,
werden beim nächsten Start automatisch nacherfasst.

**Login schlägt fehl**
* „nicht freigeschaltet“ → Person in der Teamverwaltung anlegen (Status *Aktiv*).
* „Discord-Login nicht eingerichtet“/„Redirect“ → Client ID/Secret und **exakt** gleiche Redirect URL
  im Portal und in den Einstellungen.

**Panel nicht erreichbar**
Tailscale auf dem Endgerät verbunden? `tailscale ip -4` auf dem Wyse = `BIND_ADDRESS` in `.env`?
Nach Änderung der Tailscale-IP: `sudo ./install.sh --non-interactive`.

**Zertifikatswarnung im Browser** → MagicDNS + HTTPS in Tailscale aktivieren, dann
`sudo scripts/renew-cert.sh --reload`.

## 15. Entwicklung und Tests

```
server/   Python: lbc/core (Konfiguration, DB, Modelle), lbc/services (Fachlogik), lbc/api (FastAPI),
          lbc/bot (Discord-Bot), lbc/tools (dbcheck, backup), migrations (Alembic), tests
web/      React + TypeScript (Vite), nginx.conf
scripts/  gemeinsame Shell-Funktionen, Zertifikat
```

Tests laufen gegen eine **echte** MySQL/MariaDB (es wird je Lauf eine eigene Datenbank angelegt und
wieder gelöscht). Zugang per `TEST_DB_HOST`, `TEST_DB_PORT`, `TEST_DB_USER`, `TEST_DB_PASSWORD`
(der Benutzer braucht das Recht, Datenbanken `lbc_test_*` anzulegen):

```bash
cd server && pip install -r requirements-dev.txt && python -m pytest -q
```

```bash
cd web && npm ci && npm test && npm run build
```

Neue Migration nach Modelländerung: `cd server && alembic revision --autogenerate -m "beschreibung"`.
