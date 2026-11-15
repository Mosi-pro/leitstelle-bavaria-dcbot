#!/usr/bin/env bash
# =====================================================================
#  Leitstelle Bavaria Control – Deinstallation
#  Entfernt Container, Images, Autostart und Cron-Jobs.
#  Konfiguration und lokale Backups werden nur nach ausdrücklicher Bestätigung gelöscht.
#  Die externe STRATO-Datenbank wird NIEMALS gelöscht oder verändert.
# =====================================================================
source "$(dirname "${BASH_SOURCE[0]}")/scripts/common.sh"
require_root "$@"

printf '%s\n' "${C_BOLD}LEITSTELLE BAVARIA CONTROL – Deinstallation${C_RESET}"
info "Die STRATO-Datenbank und alle darin gespeicherten Daten bleiben unverändert erhalten."
confirm "Anwendung jetzt deinstallieren?" n || { info "Abgebrochen."; exit 0; }

step "Container und Images entfernen"
if docker_ready; then
  compose down --remove-orphans --rmi local >/dev/null 2>&1 || true
  docker image rm leitstelle-bavaria/server:latest leitstelle-bavaria/web:latest >/dev/null 2>&1 || true
  ok "Container und Images entfernt"
else
  warn "Docker läuft nicht – Container konnten nicht entfernt werden."
fi

step "Autostart und Cron-Jobs entfernen"
systemctl disable --now "$SERVICE_NAME.service" >/dev/null 2>&1 || true
rm -f "/etc/systemd/system/$SERVICE_NAME.service" "/etc/cron.d/$SERVICE_NAME"
systemctl daemon-reload || true
ok "systemd-Dienst und Cron-Jobs entfernt"

step "Konfiguration"
if [ -f "$ENV_FILE" ]; then
  warn "Die .env enthält alle Zugangsdaten (STRATO, Discord) und den Verschlüsselungsschlüssel."
  if confirm "Konfiguration (.env und Sicherungskopien) endgültig löschen?" n; then
    rm -f "$ENV_FILE" "$ENV_FILE".bak-*; ok "Konfiguration gelöscht"
  else ok "Konfiguration bleibt erhalten"; fi
fi
rm -f "$CERT_DIR"/panel.crt "$CERT_DIR"/panel.key

step "Lokale Backups"
if compgen -G "$BACKUP_DIR/*" >/dev/null; then
  info "Vorhanden: $(find "$BACKUP_DIR" -maxdepth 1 -type f | wc -l) Datei(en) in $BACKUP_DIR"
  if confirm "Lokale Backups endgültig löschen?" n && confirm "Wirklich? Dies kann nicht rückgängig gemacht werden." n; then
    rm -rf "${BACKUP_DIR:?}"/*; ok "Lokale Backups gelöscht"
  else ok "Backups bleiben erhalten"; fi
fi

echo
ok "Deinstallation abgeschlossen. Die STRATO-Datenbank wurde nicht verändert."
info "Docker und Tailscale bleiben installiert. Das Projektverzeichnis kann bei Bedarf manuell gelöscht werden."
