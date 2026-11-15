#!/usr/bin/env bash
# =====================================================================
#  Leitstelle Bavaria Control – Installation auf Debian (Dell Wyse 5070)
#
#  Aufruf:   sudo ./install.sh                 Erstinstallation (fragt alles ab)
#            sudo ./install.sh --reconfigure   Zugangsdaten neu eingeben
#            sudo ./install.sh --non-interactive  vorhandene .env verwenden
# =====================================================================
source "$(dirname "${BASH_SOURCE[0]}")/scripts/common.sh"

RECONFIGURE=0
INTERACTIVE=1
for arg in "$@"; do
  case "$arg" in
    --reconfigure) RECONFIGURE=1 ;;
    --non-interactive) INTERACTIVE=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) fail "Unbekannte Option: $arg" ;;
  esac
done

require_root "$@"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "----- Installation gestartet: $(date) -----" >> "$LOG_FILE"

printf '%s\n' "${C_BOLD}LEITSTELLE BAVARIA CONTROL – Installation${C_RESET}"
info "Projektverzeichnis: $PROJECT_DIR"

# ---------------------------------------------------------------------
step "1/15 Betriebssystem prüfen"
[ -r /etc/os-release ] || fail "/etc/os-release fehlt – Betriebssystem kann nicht erkannt werden."
# shellcheck disable=SC1091
. /etc/os-release
if [ "${ID:-}" = "debian" ]; then
  case "${VERSION_ID:-}" in
    11|12|13) ok "Debian $VERSION_ID (${VERSION_CODENAME:-}) erkannt" ;;
    *) warn "Debian ${VERSION_ID:-?} wurde nicht getestet (unterstützt: 11, 12, 13)."; confirm "Trotzdem fortfahren?" n || exit 1 ;;
  esac
elif [ "${ID_LIKE:-}" = "debian" ] || [ "${ID:-}" = "ubuntu" ]; then
  warn "$PRETTY_NAME ist Debian-basiert, aber nicht offiziell unterstützt."
  [ "$INTERACTIVE" = 1 ] && { confirm "Trotzdem fortfahren?" n || exit 1; }
else
  fail "Nicht unterstütztes Betriebssystem: ${PRETTY_NAME:-unbekannt}. Benötigt wird Debian 11, 12 oder 13."
fi

# ---------------------------------------------------------------------
step "2/15 Systemvoraussetzungen prüfen"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|aarch64) ok "Architektur $ARCH" ;;
  *) fail "Architektur $ARCH wird nicht unterstützt (benötigt x86_64 oder aarch64)." ;;
esac
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 1500 ]; then fail "Zu wenig Arbeitsspeicher: ${MEM_MB} MB (mindestens 1,5 GB)."; fi
ok "Arbeitsspeicher: ${MEM_MB} MB"
FREE_MB=$(df -Pm "$PROJECT_DIR" | awk 'NR==2 {print $4}')
if [ "$FREE_MB" -lt 3000 ]; then fail "Zu wenig freier Speicher: ${FREE_MB} MB (mindestens 3 GB)."; fi
ok "Freier Speicher: ${FREE_MB} MB"
command -v curl >/dev/null 2>&1 || { info "Installiere curl …"; apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null; }
if curl -fsS --max-time 10 https://discord.com/api/v10/gateway >/dev/null; then
  ok "Internetverbindung und Discord-API erreichbar"
else
  fail "Discord ist nicht erreichbar. Der Bot benötigt eine ausgehende Internetverbindung (HTTPS)."
fi
for pkg in openssl cron; do
  if ! command -v "$pkg" >/dev/null 2>&1; then
    info "Installiere $pkg …"; apt-get install -y -qq "$pkg" >/dev/null || fail "$pkg konnte nicht installiert werden."
  fi
done
ok "Hilfsprogramme vorhanden (curl, openssl, cron)"

# ---------------------------------------------------------------------
step "3/15 Docker prüfen"
install_docker() {
  info "Docker wird aus dem offiziellen Docker-Repository installiert …"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  local distro="debian"; [ "${ID:-}" = "ubuntu" ] && distro="ubuntu"
  curl -fsSL "https://download.docker.com/linux/$distro/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$distro ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
}
if ! command -v docker >/dev/null 2>&1; then
  if [ "$INTERACTIVE" = 0 ] || confirm "Docker ist nicht installiert. Jetzt installieren?" j; then
    install_docker || fail "Docker konnte nicht installiert werden. Siehe $LOG_FILE"
  else
    fail "Docker wird benötigt."
  fi
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker info >/dev/null 2>&1 || fail "Der Docker-Dienst läuft nicht. Prüfen mit: systemctl status docker"
ok "Docker $(docker version --format '{{.Server.Version}}') läuft und startet automatisch"

# ---------------------------------------------------------------------
step "4/15 Docker Compose prüfen"
if ! docker compose version >/dev/null 2>&1; then
  info "Installiere Docker Compose Plugin …"
  apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || install_docker
fi
docker compose version >/dev/null 2>&1 || fail "Docker Compose (Plugin) ist nicht verfügbar."
ok "$(docker compose version)"

# ---------------------------------------------------------------------
step "5/15 Tailscale (VPN) prüfen"
if ! command -v tailscale >/dev/null 2>&1; then
  if [ "$INTERACTIVE" = 1 ] && confirm "Tailscale ist nicht installiert. Jetzt installieren (offizielles Installationsskript)?" j; then
    curl -fsSL https://tailscale.com/install.sh | sh || fail "Tailscale konnte nicht installiert werden."
  else
    fail "Tailscale wird benötigt – das Webpanel ist ausschließlich über das VPN erreichbar."
  fi
fi
systemctl enable --now tailscaled >/dev/null 2>&1 || true
TS_IP="$(tailscale_ip)"
if [ -z "$TS_IP" ]; then
  warn "Dieses Gerät ist noch nicht mit Tailscale verbunden."
  if [ "$INTERACTIVE" = 1 ]; then
    info "Es wird ein Anmeldelink angezeigt – im Browser öffnen und das Gerät deinem Tailnet hinzufügen."
    tailscale up || fail "tailscale up ist fehlgeschlagen."
    TS_IP="$(tailscale_ip)"
  fi
fi
[ -n "$TS_IP" ] || fail "Keine Tailscale-IP gefunden. Bitte 'sudo tailscale up' ausführen und die Installation wiederholen."
TS_DNS="$(tailscale_dns)"
ok "Tailscale-IP: $TS_IP${TS_DNS:+ · Name: $TS_DNS}"

# ---------------------------------------------------------------------
step "6/15 Projektkonfiguration anlegen"
mkdir -p "$BACKUP_DIR" "$CERT_DIR"
chown "$APP_UID:$APP_UID" "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
chmod +x "$PROJECT_DIR"/*.sh "$PROJECT_DIR"/scripts/*.sh
ok "Verzeichnisse backups/ und nginx/certs/ bereit"

# ---------------------------------------------------------------------
step "7/15 .env-Datei erstellen"
NEW_ENV=0
if [ ! -f "$ENV_FILE" ]; then
  cp "$PROJECT_DIR/.env.example" "$ENV_FILE"; NEW_ENV=1
  ok ".env aus .env.example erstellt"
else
  cp "$ENV_FILE" "$ENV_FILE.bak-$(date +%Y%m%d-%H%M%S)"
  ok "Vorhandene .env gefunden (Sicherungskopie angelegt)"
fi
secure_env_file
if [ -z "$(get_env APP_SECRET_KEY)" ]; then
  set_env APP_SECRET_KEY "$(openssl rand -hex 32)"
  ok "APP_SECRET_KEY erzeugt"
fi
set_env BIND_ADDRESS "$TS_IP"
PANEL_HOST="${TS_DNS:-$TS_IP}"
[ -n "$(get_env APP_BASE_URL)" ] || set_env APP_BASE_URL "https://$PANEL_HOST"
[ -n "$(get_env DISCORD_REDIRECT_URI)" ] || set_env DISCORD_REDIRECT_URI "https://$PANEL_HOST/api/auth/callback"

ASK=0
if [ "$INTERACTIVE" = 1 ] && { [ "$NEW_ENV" = 1 ] || [ "$RECONFIGURE" = 1 ] || [ -z "$(get_env DB_HOST)" ] || [ -z "$(get_env DISCORD_BOT_TOKEN)" ]; }; then
  ASK=1
fi

# ---------------------------------------------------------------------
step "8/15 STRATO-Datenbank"
ask_db() {
  info "Die Zugangsdaten findest du im STRATO-Kundenbereich unter Datenbanken (MySQL)."
  info "Hinweis: Der Dell Wyse muss die STRATO-Datenbank von außen erreichen dürfen (siehe README)."
  local v
  ask v "Datenbank-Host (DB_HOST)" "$(get_env DB_HOST)"; set_env DB_HOST "$v"
  ask v "Port (DB_PORT)" "$(get_env DB_PORT || true)" '^[0-9]{2,5}$' "Bitte eine Portnummer eingeben."; set_env DB_PORT "$v"
  ask v "Datenbankname (DB_NAME)" "$(get_env DB_NAME)"; set_env DB_NAME "$v"
  ask v "Benutzername (DB_USER)" "$(get_env DB_USER)"; set_env DB_USER "$v"
  ask_secret v "Passwort (DB_PASSWORD)" "$(get_env DB_PASSWORD)"; set_env DB_PASSWORD "$v"
  ask v "TLS-Modus (disabled/preferred/required/verify_ca/verify_identity)" "$(get_env DB_SSL_MODE)" \
    '^(disabled|preferred|required|verify_ca|verify_identity)$' "Erlaubt: disabled, preferred, required, verify_ca, verify_identity"
  set_env DB_SSL_MODE "$v"
}
if [ "$ASK" = 1 ]; then ask_db; else ok "Datenbankzugang aus .env übernommen ($(get_env DB_USER)@$(get_env DB_HOST))"; fi

# ---------------------------------------------------------------------
step "9/15 Discord-Zugangsdaten"
ask_discord() {
  info "Discord Developer Portal: https://discord.com/developers/applications"
  local v snow='^[0-9]{15,21}$' snowmsg="Eine Discord-ID besteht aus 15–21 Ziffern (Entwicklermodus → ID kopieren)."
  ask_secret v "Bot Token (Bot → Reset Token)" "$(get_env DISCORD_BOT_TOKEN)"; set_env DISCORD_BOT_TOKEN "$v"
  ask v "Client ID (OAuth2)" "$(get_env DISCORD_CLIENT_ID)" "$snow" "$snowmsg"; set_env DISCORD_CLIENT_ID "$v"
  ask_secret v "Client Secret (OAuth2)" "$(get_env DISCORD_CLIENT_SECRET)"; set_env DISCORD_CLIENT_SECRET "$v"
  ask v "Server-ID von Leitstelle Bavaria" "$(get_env DISCORD_GUILD_ID)" "$snow" "$snowmsg"; set_env DISCORD_GUILD_ID "$v"
  ask v "Deine eigene Discord-ID (Inhaber, erhält Vollzugriff)" "$(get_env OWNER_DISCORD_ID)" "$snow" "$snowmsg"; set_env OWNER_DISCORD_ID "$v"
  ask v "OAuth2 Redirect URL" "$(get_env DISCORD_REDIRECT_URI)" '^https?://' "Muss mit https:// beginnen."; set_env DISCORD_REDIRECT_URI "$v"
  warn "Trage diese Redirect URL im Developer Portal unter OAuth2 → Redirects ein: $(get_env DISCORD_REDIRECT_URI)"
}
if [ "$ASK" = 1 ]; then ask_discord; else ok "Discord-Zugangsdaten aus .env übernommen"; fi
for key in DB_HOST DB_NAME DB_USER DISCORD_BOT_TOKEN DISCORD_GUILD_ID OWNER_DISCORD_ID; do
  [ -n "$(get_env "$key")" ] || fail "$key fehlt in der .env. Bitte 'sudo ./install.sh --reconfigure' ausführen."
done
secure_env_file

# ---------------------------------------------------------------------
step "10/15 Container-Images bauen und Datenbankverbindung testen"
info "Images werden gebaut – das dauert beim ersten Mal einige Minuten …"
compose build --pull >/dev/null || fail "Der Build der Docker-Images ist fehlgeschlagen. Details: docker compose build"
ok "Images gebaut"
while true; do
  if compose run --rm --no-deps -T migrate python -m lbc.tools.dbcheck; then
    ok "STRATO-Datenbank ist einsatzbereit"
    break
  fi
  warn "Die Datenbankprüfung ist fehlgeschlagen (siehe Meldungen oben)."
  warn "Es wird NICHT auf eine lokale Datenbank ausgewichen."
  if [ "$INTERACTIVE" = 1 ] && confirm "Zugangsdaten neu eingeben und erneut prüfen?" j; then
    ask_db; secure_env_file
  else
    fail "Die STRATO-Datenbank ist nicht erreichbar oder nicht nutzbar. Siehe README → Fehlerbehebung → Datenbank."
  fi
done

# ---------------------------------------------------------------------
step "11/15 Datenbankmigrationen durchführen"
compose run --rm --no-deps -T migrate || fail "Die Datenbankmigration ist fehlgeschlagen."
ok "Datenbankschema ist aktuell"

# ---------------------------------------------------------------------
step "12/15 HTTPS-Zertifikat (innerhalb des VPN)"
"$PROJECT_DIR/scripts/renew-cert.sh" || fail "Es konnte kein Zertifikat erstellt werden."

# ---------------------------------------------------------------------
step "13/15 Anwendung starten und Autostart einrichten"
cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=Leitstelle Bavaria Control (Docker Compose)
Requires=docker.service
After=docker.service network-online.target tailscaled.service
Wants=network-online.target tailscaled.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$PROJECT_DIR
# Warten, bis Tailscale eine IP vergeben hat (das Panel lauscht nur auf dieser Adresse)
ExecStartPre=/bin/sh -c 'for i in \$(seq 1 60); do ip -4 addr show tailscale0 2>/dev/null | grep -q inet && exit 0; sleep 2; done; exit 1'
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose stop
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE_NAME.service" >/dev/null 2>&1
compose up -d --remove-orphans || fail "Die Container konnten nicht gestartet werden. Details: docker compose logs"
systemctl start "$SERVICE_NAME.service" || true
ok "Container laufen; Autostart nach Neustart aktiv (systemd-Dienst $SERVICE_NAME)"
ok "Automatischer Neustart bei Abstürzen aktiv (restart: unless-stopped)"

cat > "/etc/cron.d/$SERVICE_NAME" <<EOF
# Leitstelle Bavaria Control – automatische Aufgaben
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Stündliche Prüfung: Backup wird zur im Webpanel eingestellten Uhrzeit erstellt
7 * * * * root $PROJECT_DIR/backup.sh --scheduled >> $PROJECT_DIR/backups/backup.log 2>&1
# Monatliche Erneuerung des HTTPS-Zertifikats
23 4 1 * * root $PROJECT_DIR/scripts/renew-cert.sh --reload >> $PROJECT_DIR/install.log 2>&1
EOF
chmod 644 "/etc/cron.d/$SERVICE_NAME"
ok "Automatische Backups und Zertifikatserneuerung eingerichtet (/etc/cron.d/$SERVICE_NAME)"

# ---------------------------------------------------------------------
step "14/15 Healthchecks"
if wait_for_healthy backend 240; then ok "Backend/API ist gesund"; else
  compose logs --tail=40 backend || true
  fail "Das Backend ist nicht gesund geworden (Logs oben)."
fi
if curl -fsk --max-time 10 "https://$TS_IP/api/health" >/dev/null; then ok "Webpanel antwortet über HTTPS auf $TS_IP"; else
  compose logs --tail=30 web || true
  fail "Das Webpanel antwortet nicht auf https://$TS_IP"
fi
info "Warte auf den Discord-Bot …"
if wait_for_healthy bot 180; then ok "Discord-Bot ist verbunden"; else
  warn "Der Discord-Bot ist noch nicht gesund. Häufige Ursachen: ungültiger Token, Intents nicht aktiviert, Bot nicht auf dem Server."
  compose logs --tail=25 bot || true
fi

# ---------------------------------------------------------------------
step "15/15 Installationsstatus"
show_status
cat <<EOF

${C_BOLD}Installation abgeschlossen.${C_RESET}

  Webpanel (nur im Tailscale-VPN):  https://${TS_DNS:-$TS_IP}
  Anmeldung:                        mit deinem Discord-Konto ($(get_env OWNER_DISCORD_ID))
  Redirect URL im Discord-Portal:   $(get_env DISCORD_REDIRECT_URI)

  Nächste Schritte:
   1. Im Webpanel unter Systemeinstellungen die Channels und Rollen auswählen
      (Bug-Forum, Developer-Channel, Developer-Rolle, Ticket-Channel, …).
   2. Unter „Discord-Panels & Bot“ das Ticket-Menü, das Bewerbungs-Panel und die
      Verifizierung veröffentlichen.
   3. Weitere Teammitglieder unter Teamverwaltung freischalten.

  Update:  sudo ./update.sh     Backup:  sudo ./backup.sh     Status:  sudo docker compose ps
EOF
