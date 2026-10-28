#!/bin/bash
# PS-Vita-Twitch – Installation / Aktualisierung auf dem VPS (idempotent, mit Backup)
# Aufruf: bash install.sh            (im entpackten Paketordner, als root)
set -euo pipefail
cd "$(dirname "$0")"

NPM=npm-npm-1
NPM_DATA=/root/npm/data
NGINX_CUSTOM=$NPM_DATA/nginx/custom/http.conf
TS=$(date +%Y%m%d-%H%M%S)
HOST=$(grep -oP '^VITA_PUBLIC_BASE=https?://\K[^/]+' /etc/vita-twitch.env 2>/dev/null || true)
HOST=${HOST:-ytv.serverlele.de}
BK=/root/vita-twitch-backup-$TS

ok()   { echo "  [ok] $*"; }
warn() { echo "  [!!] $*"; }

echo "== Voraussetzungen"
python3 -c "import yt_dlp" 2>/dev/null && ok "yt-dlp" || { warn "yt-dlp fehlt: pip3 install --break-system-packages yt-dlp"; exit 1; }
python3 -c "import cryptography" 2>/dev/null && ok "cryptography" || { apt-get install -y -q python3-cryptography >/dev/null && ok "cryptography installiert"; }
python3 -c "import PIL" 2>/dev/null   && ok "Pillow" || { apt-get install -y -q python3-pil >/dev/null && ok "Pillow installiert"; }
docker inspect $NPM >/dev/null 2>&1 && ok "NPM-Container $NPM" || { warn "NPM-Container $NPM nicht gefunden"; exit 1; }
python3 -m py_compile adapter.py && ok "adapter.py Syntax"

echo "== Backup nach $BK"
mkdir -p "$BK"; chmod 700 "$BK"
for f in /opt/vita-twitch/adapter.py /etc/systemd/system/vita-twitch.service "$NGINX_CUSTOM" /etc/cron.d/vita-twitch-cert; do
  [ -e "$f" ] && cp -a "$f" "$BK/" && ok "gesichert: $f"
done
[ -d /etc/systemd/system/vita-twitch.service.d ] && cp -a /etc/systemd/system/vita-twitch.service.d "$BK/"

echo "== Dateien"
install -d -m 750 /opt/vita-twitch /var/log/vita-twitch
install -m 750 adapter.py /opt/vita-twitch/adapter.py
install -d -m 755 /opt/vita-twitch/www
install -m 644 www/index.html /opt/vita-twitch/www/
rm -f /opt/vita-twitch/www/link.html   # seit 2.27 alles auf einer Seite
install -m 750 bin/vita-timeline bin/vita-twitch-check /usr/local/sbin/
install -m 644 systemd/vita-twitch.service /etc/systemd/system/vita-twitch.service
rm -rf /etc/systemd/system/vita-twitch.service.d   # Drop-ins sind jetzt in der Unit enthalten
install -m 644 cron/vita-twitch-cert /etc/cron.d/vita-twitch-cert
if [ ! -f /etc/vita-twitch.env ]; then
  install -m 600 vita-twitch.env.example /etc/vita-twitch.env
  warn "/etc/vita-twitch.env neu angelegt - TWITCH_CLIENT_ID/SECRET eintragen und erneut starten"
else
  chmod 600 /etc/vita-twitch.env; ok "/etc/vita-twitch.env vorhanden (unveraendert)"
fi
# internes Zertifikat fuer nginx -> Adapter (nur 127.0.0.1)
if [ ! -f /etc/ssl/private/vita-twitch-test.key ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=127.0.0.1 \
    -keyout /etc/ssl/private/vita-twitch-test.key -out /etc/ssl/certs/vita-twitch-test.crt 2>/dev/null
  chmod 600 /etc/ssl/private/vita-twitch-test.key; ok "internes Zertifikat erzeugt"
fi

echo "== nginx (NPM)"
if [ ! -f /root/npm/letsencrypt/live/ytv-rsa/fullchain.pem ]; then
  warn "RSA-Zertifikat ytv-rsa fehlt. Einmalig (NPM-Host $HOST muss dafuer kurz aktiv sein):"
  echo "     docker exec $NPM certbot certonly --config /etc/letsencrypt.ini --cert-name ytv-rsa --key-type rsa \\"
  echo "       --rsa-key-size 2048 --webroot -w /data/letsencrypt-acme-challenge -d $HOST --agree-tos --non-interactive"
  echo "     danach NPM-Host $HOST deaktivieren und install.sh erneut ausfuehren."
else
  install -d "$(dirname "$NGINX_CUSTOM")"
  install -m 644 nginx/vita-twitch.conf "$NGINX_CUSTOM"
  if docker exec $NPM nginx -t >/dev/null 2>&1; then
    docker exec $NPM nginx -s reload; ok "nginx neu geladen"
  else
    warn "nginx-Test fehlgeschlagen - alte Konfiguration wird wiederhergestellt"
    docker exec $NPM nginx -t || true
    [ -f "$BK/http.conf" ] && cp -a "$BK/http.conf" "$NGINX_CUSTOM" || rm -f "$NGINX_CUSTOM"
    docker exec $NPM nginx -s reload; exit 1
  fi
fi

echo "== Dienst"
systemctl daemon-reload
systemctl enable vita-twitch >/dev/null 2>&1
systemctl restart vita-twitch
for i in $(seq 1 15); do curl -sk -m 2 https://127.0.0.1:8443/health >/dev/null 2>&1 && break; sleep 1; done
curl -sk https://127.0.0.1:8443/health && echo
curl -s -m 5 -o /dev/null -w "  oeffentlich: %{http_code}\n" https://$HOST/health || true
echo
echo "Fertig. Backup: $BK"
echo "Test:  vita-twitch-check https://$HOST --verify   |   Diagnose: vita-timeline 10"
