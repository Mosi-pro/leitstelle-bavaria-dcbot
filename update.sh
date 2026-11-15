#!/usr/bin/env bash
# =====================================================================
#  Leitstelle Bavaria Control – Update
#  Aufruf: sudo ./update.sh [--skip-backup]
# =====================================================================
source "$(dirname "${BASH_SOURCE[0]}")/scripts/common.sh"
require_root "$@"
SKIP_BACKUP=0; [ "${1:-}" = "--skip-backup" ] && SKIP_BACKUP=1
exec > >(tee -a "$LOG_FILE") 2>&1
echo "----- Update gestartet: $(date) -----"
[ -f "$ENV_FILE" ] || fail "Keine .env gefunden – bitte zuerst ./install.sh ausführen."
docker_ready || fail "Docker läuft nicht (systemctl status docker)."

step "1/6 Konfiguration sichern"
STAMP="$(date +%Y%m%d-%H%M%S)"
cp "$ENV_FILE" "$BACKUP_DIR/env-before-update-$STAMP"; chmod 600 "$BACKUP_DIR/env-before-update-$STAMP"
ok "Konfiguration gesichert: backups/env-before-update-$STAMP"

step "2/6 Datenbank sichern"
if [ "$SKIP_BACKUP" = 1 ]; then warn "Datenbank-Backup übersprungen (--skip-backup)"
elif compose run --rm --no-deps -T migrate python -m lbc.tools.backup create; then ok "Datenbank-Backup erstellt"
else
  warn "Das Datenbank-Backup ist fehlgeschlagen."
  confirm "Update trotzdem fortsetzen?" n || fail "Update abgebrochen."
fi

step "3/6 Neue Version laden"
if [ -d "$PROJECT_DIR/.git" ] && command -v git >/dev/null 2>&1; then
  OLD="$(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
  git -C "$PROJECT_DIR" pull --ff-only || fail "git pull ist fehlgeschlagen (lokale Änderungen?). Prüfen mit: git status"
  NEW="$(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
  if [ "$OLD" = "$NEW" ]; then ok "Bereits aktuell ($NEW) – Images werden trotzdem neu gebaut"; else ok "Aktualisiert: $OLD → $NEW"; fi
else
  warn "Kein Git-Repository – es wird die vorhandene Programmversion neu gebaut."
fi
chmod +x "$PROJECT_DIR"/*.sh "$PROJECT_DIR"/scripts/*.sh

step "4/6 Images bauen"
compose build --pull >/dev/null || fail "Build fehlgeschlagen. Details: docker compose build"
ok "Images gebaut"

step "5/6 Datenbankmigrationen"
compose run --rm --no-deps -T migrate python -m lbc.tools.dbcheck >/dev/null || fail "Die STRATO-Datenbank ist nicht erreichbar – Update abgebrochen, die alte Version läuft weiter."
compose run --rm --no-deps -T migrate || fail "Migration fehlgeschlagen. Wiederherstellung: sudo ./restore.sh backups/<letztes Backup>"
ok "Datenbankschema aktuell"

step "6/6 Neustart"
compose up -d --remove-orphans
wait_for_healthy backend 240 && ok "Backend gesund" || warn "Backend noch nicht gesund – Logs: docker compose logs backend"
docker image prune -f >/dev/null 2>&1 || true
show_status
echo; ok "Update abgeschlossen."
