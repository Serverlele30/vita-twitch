#!/bin/bash
# Installiert MediaMTX + ffmpeg fuer die N8-Twitch-App auf dem vita-twitch-Server.
# Aufruf: sudo bash install-mediamtx.sh [MediaMTX-Version, z.B. v1.15.3]
set -euo pipefail
cd "$(dirname "$0")"

VER=${1:-}
ARCH=$(uname -m)
case "$ARCH" in
  x86_64) MARCH=amd64 ;;
  aarch64) MARCH=arm64 ;;
  armv7l) MARCH=armv7 ;;
  *) echo "Unbekannte Architektur $ARCH"; exit 1 ;;
esac

apt-get update -qq
apt-get install -y -qq ffmpeg curl ca-certificates

if [ -z "$VER" ]; then
  VER=$(curl -fsSL https://api.github.com/repos/bluenviron/mediamtx/releases/latest | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1 || true)
fi
echo ">> MediaMTX $VER ($MARCH)"

id mediamtx >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mediamtx
install -d -o root -g root /opt/mediamtx

TMP=$(mktemp -d)
curl -fsSL -o "$TMP/mtx.tar.gz" \
  "https://github.com/bluenviron/mediamtx/releases/download/$VER/mediamtx_${VER}_linux_${MARCH}.tar.gz"
tar -xzf "$TMP/mtx.tar.gz" -C "$TMP"
install -m 755 "$TMP/mediamtx" /opt/mediamtx/mediamtx
rm -rf "$TMP"

[ -f /opt/mediamtx/mediamtx.yml ] && cp /opt/mediamtx/mediamtx.yml "/opt/mediamtx/mediamtx.yml.bak.$(date +%s)"
install -m 644 mediamtx.yml /opt/mediamtx/mediamtx.yml
install -m 755 n8-transcode.sh /opt/mediamtx/n8-transcode.sh
[ -f /etc/default/n8-twitch ] || install -m 644 n8-twitch.default /etc/default/n8-twitch
install -m 644 mediamtx.service /etc/systemd/system/mediamtx.service

systemctl daemon-reload
systemctl enable --now mediamtx
sleep 1
systemctl --no-pager --lines=5 status mediamtx || true

if command -v ufw >/dev/null && ufw status | grep -q active; then
  ufw allow 8554/tcp
  ufw allow 8002:8003/udp
fi
echo ">> Fertig. Test: rtsp://<host>:8554/tw/<kanal>  (Netcup-Firewall: 8554/tcp, 8002-8003/udp freigeben)"
