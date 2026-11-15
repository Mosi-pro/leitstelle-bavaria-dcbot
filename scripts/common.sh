#!/usr/bin/env bash
# Gemeinsame Hilfsfunktionen für install.sh, update.sh, backup.sh, restore.sh, uninstall.sh
# shellcheck disable=SC2034

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
BACKUP_DIR="$PROJECT_DIR/backups"
CERT_DIR="$PROJECT_DIR/nginx/certs"
APP_UID=10001
SERVICE_NAME="leitstelle-bavaria"
LOG_FILE="$PROJECT_DIR/install.log"

if [ -t 1 ]; then
  C_RED=$'\e[31m'; C_GREEN=$'\e[32m'; C_YELLOW=$'\e[33m'; C_BLUE=$'\e[34m'; C_BOLD=$'\e[1m'; C_RESET=$'\e[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_RESET=""
fi

CURRENT_STEP=""

step()  { CURRENT_STEP="$*"; printf '\n%s==> %s%s\n' "$C_BOLD$C_BLUE" "$*" "$C_RESET"; }
info()  { printf '    %s\n' "$*"; }
ok()    { printf '    %s✔ %s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
warn()  { printf '    %s! %s%s\n' "$C_YELLOW" "$*" "$C_RESET"; }
fail()  {
  printf '\n%s✘ FEHLER: %s%s\n' "$C_RED$C_BOLD" "$*" "$C_RESET" >&2
  [ -n "$CURRENT_STEP" ] && printf '  Schritt: %s\n' "$CURRENT_STEP" >&2
  exit 1
}

on_error() {
  local code=$? line=${1:-?}
  printf '\n%s✘ Unerwarteter Fehler (Code %s, Zeile %s) im Schritt: %s%s\n' "$C_RED$C_BOLD" "$code" "$line" "${CURRENT_STEP:-unbekannt}" "$C_RESET" >&2
  printf '  Details stehen weiter oben bzw. in %s\n' "$LOG_FILE" >&2
  printf '  Hilfe: README.md → Abschnitt „Fehlerbehebung“.\n' >&2
}
trap 'on_error $LINENO' ERR

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    fail "Bitte mit Administratorrechten ausführen: sudo $0 $*"
  fi
}

confirm() {
  # confirm "Frage" [standard j/n]
  local prompt="$1" def="${2:-n}" ans
  local hint="[j/N]"; [ "$def" = "j" ] && hint="[J/n]"
  read -r -p "    $prompt $hint " ans || true
  ans="${ans:-$def}"
  [[ "$ans" =~ ^[jJyY] ]]
}

ask() {
  # ask VAR "Frage" [standard] [regex] [fehlermeldung]
  local __var="$1" prompt="$2" def="${3:-}" re="${4:-}" msg="${5:-Ungültige Eingabe.}" val
  while true; do
    if [ -n "$def" ]; then read -r -p "    $prompt [$def]: " val || true; val="${val:-$def}"
    else read -r -p "    $prompt: " val || true; fi
    val="$(printf '%s' "$val" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [ -z "$val" ]; then warn "Eingabe erforderlich."; continue; fi
    if [ -n "$re" ] && ! [[ "$val" =~ $re ]]; then warn "$msg"; continue; fi
    printf -v "$__var" '%s' "$val"; return 0
  done
}

ask_secret() {
  # ask_secret VAR "Frage" [vorhandener Wert → Enter behält ihn]
  local __var="$1" prompt="$2" existing="${3:-}" val
  while true; do
    if [ -n "$existing" ]; then read -r -s -p "    $prompt [Enter = unverändert]: " val || true
    else read -r -s -p "    $prompt: " val || true; fi
    echo
    if [ -z "$val" ] && [ -n "$existing" ]; then val="$existing"; fi
    if [ -z "$val" ]; then warn "Eingabe erforderlich."; continue; fi
    printf -v "$__var" '%s' "$val"; return 0
  done
}

env_quote() {
  # Quoting, das Docker Compose, python-dotenv und bash gleich verstehen
  local v="$1"
  if [[ "$v" =~ ^[A-Za-z0-9_./:@%+,=-]*$ ]]; then printf '%s' "$v"; return; fi
  if [[ "$v" != *"'"* ]]; then printf "'%s'" "$v"; return; fi
  if [[ "$v" == *'$'* || "$v" == *'`'* ]]; then
    fail "Werte mit Hochkomma (') UND Dollarzeichen (\$) werden nicht unterstützt. Bitte das Passwort ändern."
  fi
  v="${v//\\/\\\\}"; v="${v//\"/\\\"}"
  printf '"%s"' "$v"
}

get_env() {
  # get_env KEY → Wert aus .env (ohne Anführungszeichen)
  [ -f "$ENV_FILE" ] || return 0
  local line
  line="$(grep -E "^$1=" "$ENV_FILE" | tail -n1 || true)"
  line="${line#*=}"
  if [[ "$line" =~ ^\'(.*)\'$ ]]; then line="${BASH_REMATCH[1]}"
  elif [[ "$line" =~ ^\"(.*)\"$ ]]; then line="${BASH_REMATCH[1]}"; line="${line//\\\"/\"}"; line="${line//\\\\/\\}"; fi
  printf '%s' "$line"
}

set_env() {
  # set_env KEY VALUE – ersetzt oder ergänzt einen Eintrag in .env
  local key="$1" val tmp
  val="$(env_quote "$2")"
  tmp="$(mktemp)"
  if grep -qE "^$key=" "$ENV_FILE" 2>/dev/null; then
    awk -v k="$key" -v v="$val" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"; printf '%s=%s\n' "$key" "$val" >> "$tmp"
  fi
  cat "$tmp" > "$ENV_FILE"; rm -f "$tmp"
}

secure_env_file() {
  chown "$APP_UID:$APP_UID" "$ENV_FILE" 2>/dev/null || true
  chmod 600 "$ENV_FILE"
}

compose() {
  (cd "$PROJECT_DIR" && docker compose "$@")
}

docker_ready() {
  command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

tailscale_ip() {
  command -v tailscale >/dev/null 2>&1 || return 0
  tailscale ip -4 2>/dev/null | head -n1 || true
}

tailscale_dns() {
  command -v tailscale >/dev/null 2>&1 || return 0
  tailscale status --json 2>/dev/null | grep -o '"DNSName": *"[^"]*"' | head -n1 | sed 's/.*"\([^"]*\)"$/\1/; s/\.$//' || true
}

wait_for_healthy() {
  # wait_for_healthy service sekunden
  local svc="$1" timeout="${2:-180}" waited=0 cid state
  cid="$(compose ps -q "$svc" 2>/dev/null || true)"
  [ -n "$cid" ] || return 1
  while [ "$waited" -lt "$timeout" ]; do
    state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
    case "$state" in
      healthy|running) [ "$state" = healthy ] && return 0 ;;
      unhealthy|exited|dead) return 1 ;;
    esac
    sleep 5; waited=$((waited + 5))
  done
  return 1
}

show_status() {
  step "Systemstatus"
  compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}' 2>/dev/null || compose ps
  local ip; ip="$(get_env BIND_ADDRESS)"
  if [ -n "$ip" ] && curl -fsk --max-time 10 "https://$ip/api/health" >/dev/null 2>&1; then
    ok "Webpanel und API antworten unter https://$ip"
  else
    warn "Das Webpanel antwortet (noch) nicht unter https://${ip:-<Tailscale-IP>} – Logs: docker compose logs backend web"
  fi
}
