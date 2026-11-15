#!/usr/bin/env bash
# Erstellt bzw. erneuert das HTTPS-Zertifikat für das Webpanel.
# 1. Bevorzugt: echtes Zertifikat über Tailscale (MagicDNS + HTTPS im Tailnet aktiviert)
# 2. Ersatz: selbstsigniertes Zertifikat für die Tailscale-IP und den Gerätenamen
#   Aufruf: scripts/renew-cert.sh [--reload]
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

RELOAD=0; [ "${1:-}" = "--reload" ] && RELOAD=1
mkdir -p "$CERT_DIR"
CRT="$CERT_DIR/panel.crt"; KEY="$CERT_DIR/panel.key"
TS_IP="$(tailscale_ip)"; TS_DNS="$(tailscale_dns)"

if [ -n "$TS_DNS" ] && tailscale cert --cert-file "$CRT.new" --key-file "$KEY.new" "$TS_DNS" >/dev/null 2>&1; then
  mv "$CRT.new" "$CRT"; mv "$KEY.new" "$KEY"
  ok "Tailscale-Zertifikat für $TS_DNS installiert (von Browsern vertraut)"
else
  rm -f "$CRT.new" "$KEY.new"
  if [ -f "$CRT" ] && openssl x509 -checkend 2592000 -noout -in "$CRT" >/dev/null 2>&1; then
    ok "Vorhandenes Zertifikat ist noch mindestens 30 Tage gültig"
  else
    SAN="IP:127.0.0.1"
    [ -n "$TS_IP" ] && SAN="$SAN,IP:$TS_IP"
    [ -n "$TS_DNS" ] && SAN="$SAN,DNS:$TS_DNS"
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=${TS_DNS:-leitstelle-bavaria}" \
      -addext "subjectAltName=$SAN" -keyout "$KEY" -out "$CRT" >/dev/null 2>&1
    warn "Selbstsigniertes Zertifikat erstellt ($SAN). Der Browser zeigt einmalig eine Warnung."
    warn "Für ein vertrauenswürdiges Zertifikat: im Tailscale-Adminbereich MagicDNS und HTTPS aktivieren."
  fi
fi
chmod 644 "$CRT"; chmod 600 "$KEY"
# nginx (im Container als root) liest den Schlüssel
if [ "$RELOAD" = 1 ] && docker_ready; then
  compose exec -T web nginx -s reload >/dev/null 2>&1 && ok "Nginx neu geladen" || true
fi
