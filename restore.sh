#!/usr/bin/env bash
# =====================================================================
#  Leitstelle Bavaria Control – Wiederherstellung eines Backups
#  Aufruf: sudo ./restore.sh backups/lbc-backup-JJJJMMTT-HHMMSS.tar.gz
#  ACHTUNG: Überschreibt den aktuellen Inhalt der STRATO-Datenbank!
# =====================================================================
source "$(dirname "${BASH_SOURCE[0]}")/scripts/common.sh"
require_root "$@"
FILE="${1:-}"
if [ -z "$FILE" ]; then
  echo "Verfügbare Backups:"; ls -1t "$BACKUP_DIR"/lbc-backup-*.tar.gz 2>/dev/null || echo "  (keine)"
  fail "Bitte die Backup-Datei angeben: sudo ./restore.sh backups/<datei>.tar.gz"
fi
[ -f "$FILE" ] || fail "Datei nicht gefunden: $FILE"
docker_ready || fail "Docker läuft nicht."
NAME="$(basename "$FILE")"
if [ "$(cd "$(dirname "$FILE")" && pwd)/$NAME" != "$BACKUP_DIR/$NAME" ]; then
  cp "$FILE" "$BACKUP_DIR/$NAME"; chown "$APP_UID:$APP_UID" "$BACKUP_DIR/$NAME"; chmod 600 "$BACKUP_DIR/$NAME"
fi

step "1/5 Backup prüfen"
compose run --rm --no-deps -T migrate python -m lbc.tools.backup verify "/backups/$NAME" || fail "Das Backup ist beschädigt oder ungültig – es wurde nichts verändert."

step "2/5 Sicherheitsabfrage"
warn "Die Wiederherstellung ÜBERSCHREIBT alle aktuellen Daten in der Datenbank $(get_env DB_NAME) auf $(get_env DB_HOST)."
warn "Änderungen seit dem Backup gehen verloren. Vorher wird automatisch ein Sicherheits-Backup erstellt."
read -r -p "    Zum Fortfahren WIEDERHERSTELLEN eingeben: " ANSWER
[ "$ANSWER" = "WIEDERHERSTELLEN" ] || fail "Abgebrochen – es wurde nichts verändert."

step "3/5 Sicherheits-Backup des aktuellen Stands"
compose run --rm --no-deps -T migrate python -m lbc.tools.backup create || {
  warn "Sicherheits-Backup fehlgeschlagen."; confirm "Trotzdem wiederherstellen?" n || fail "Abgebrochen."; }

step "4/5 Wiederherstellen"
compose stop backend bot >/dev/null 2>&1 || true
compose run --rm --no-deps -T migrate python -m lbc.tools.backup restore "/backups/$NAME" --yes --env-out "/backups/restored.env" \
  || { compose up -d; fail "Wiederherstellung fehlgeschlagen. Die Anwendung wurde wieder gestartet."; }
ok "Datenbank wiederhergestellt"
if [ -f "$BACKUP_DIR/restored.env" ]; then
  if cmp -s "$BACKUP_DIR/restored.env" "$ENV_FILE"; then ok "Konfiguration im Backup ist identisch mit der aktuellen"
  elif confirm "Das Backup enthält eine abweichende Konfiguration (.env). Diese ebenfalls wiederherstellen?" n; then
    cp "$ENV_FILE" "$BACKUP_DIR/env-before-restore-$(date +%Y%m%d-%H%M%S)"
    cat "$BACKUP_DIR/restored.env" > "$ENV_FILE"; secure_env_file
    ok "Konfiguration wiederhergestellt (alte Version in backups/ gesichert)"
  fi
  rm -f "$BACKUP_DIR/restored.env"
fi

step "5/5 Migrationen und Neustart"
compose run --rm --no-deps -T migrate || fail "Migration nach der Wiederherstellung fehlgeschlagen."
compose up -d --force-recreate
wait_for_healthy backend 240 && ok "Backend gesund" || warn "Backend noch nicht gesund."
show_status
