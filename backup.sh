#!/usr/bin/env bash
# =====================================================================
#  Leitstelle Bavaria Control – Backup (Datenbank + Konfiguration)
#  Aufruf: sudo ./backup.sh            sofortiges Backup
#          sudo ./backup.sh --scheduled  nur zur eingestellten Uhrzeit (Cron)
#          sudo ./backup.sh --list       vorhandene Backups anzeigen
#  Backups liegen lokal in ./backups (Dateirechte 600).
# =====================================================================
source "$(dirname "${BASH_SOURCE[0]}")/scripts/common.sh"
require_root "$@"
[ -f "$ENV_FILE" ] || fail "Keine .env gefunden – Anwendung ist nicht installiert."
docker_ready || fail "Docker läuft nicht."
mkdir -p "$BACKUP_DIR"; chown "$APP_UID:$APP_UID" "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"

case "${1:-}" in
  --list) compose run --rm --no-deps -T migrate python -m lbc.tools.backup list; exit 0 ;;
  --scheduled) compose run --rm --no-deps -T migrate python -m lbc.tools.backup create --scheduled ;;
  "") step "Backup erstellen"
      compose run --rm --no-deps -T migrate python -m lbc.tools.backup create || fail "Backup fehlgeschlagen (Datenbank erreichbar?)."
      ok "Backup liegt in $BACKUP_DIR" ;;
  *) fail "Unbekannte Option: $1" ;;
esac
