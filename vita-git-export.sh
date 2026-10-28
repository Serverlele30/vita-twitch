#!/bin/bash
# vita-git-export.sh – Live-Stand vom VPS in das Git-Repo uebernehmen, auf Secrets pruefen, committen.
# Aufruf:  bash vita-git-export.sh ["Commit-Nachricht"]
# Mehrfach ausfuehrbar: Es wird nur committet, wenn sich etwas geaendert hat.
set -euo pipefail

REPO=${VITA_REPO:-/root/git/vita-twitch}
PKG=${VITA_PKG:-/root/vita-twitch-paket/vita-twitch}   # entpacktes Installationspaket
HERE=$(cd "$(dirname "$0")" && pwd)
MSG=${1:-}

die() { echo "FEHLER: $*" >&2; exit 1; }
cp_first() {  # cp_first ZIEL QUELLE1 [QUELLE2 ...]  -> erste vorhandene Quelle kopieren
  local dst=$1; shift
  for src in "$@"; do [ -f "$src" ] && { install -D -m 644 "$src" "$REPO/$dst"; return 0; }; done
  echo "  [--] nicht gefunden: $dst"
}

mkdir -p "$REPO"; cd "$REPO"
[ -d .git ] || { git init -q -b main; echo "Repo angelegt: $REPO"; }

echo "== Live-Dateien uebernehmen"
cp_first adapter.py                    /opt/vita-twitch/adapter.py "$PKG/adapter.py"
cp_first systemd/vita-twitch.service   /etc/systemd/system/vita-twitch.service "$PKG/systemd/vita-twitch.service"
cp_first nginx/vita-twitch.conf        /root/npm/data/nginx/custom/http.conf "$PKG/nginx/vita-twitch.conf"
cp_first cron/vita-twitch-cert         /etc/cron.d/vita-twitch-cert "$PKG/cron/vita-twitch-cert"
cp_first bin/vita-timeline             /usr/local/sbin/vita-timeline "$PKG/bin/vita-timeline"
cp_first bin/vita-twitch-check         /usr/local/sbin/vita-twitch-check "$PKG/bin/vita-twitch-check"
cp_first install.sh                    "$PKG/install.sh"
cp_first vita/redirects.txt            "$PKG/vita/redirects.txt"
cp_first vita-twitch.env.example       "$PKG/vita-twitch.env.example"
cp_first dns/dnsmasq-vita.conf         /etc/dnsmasq.d/vita.conf /root/vita-dnsmasq.conf.bak
# Drop-ins (falls install.sh noch nicht gelaufen ist)
if [ -d /etc/systemd/system/vita-twitch.service.d ]; then
  for f in /etc/systemd/system/vita-twitch.service.d/*.conf; do cp_first "systemd/vita-twitch.service.d/$(basename "$f")" "$f"; done
fi
chmod 755 bin/* install.sh 2>/dev/null || true
# Repo-Dateien (README, .gitignore, dieses Skript) nur anlegen, wenn sie fehlen
for f in README.md .gitignore vita-git-export.sh; do
  [ -f "$REPO/$f" ] || { [ -f "$HERE/$f" ] && install -m 644 "$HERE/$f" "$REPO/$f"; }
done
[ -f vita-git-export.sh ] && chmod 755 vita-git-export.sh

echo "== Secret-Pruefung"
# 1) echte Werte aus der env-Datei duerfen nirgends im Repo stehen
if [ -r /etc/vita-twitch.env ]; then
  while IFS='=' read -r k v; do
    case "$k" in TWITCH_CLIENT_SECRET|TWITCH_WEB_AUTH_TOKEN|TWITCH_CLIENT_ID) ;; *) continue;; esac
    v=${v%\"}; v=${v#\"}; [ ${#v} -ge 8 ] || continue
    grep -rqF --exclude-dir=.git -- "$v" . && die "Wert von $k steht im Repo – abgebrochen"
  done < <(grep -E '^[A-Z_]+=' /etc/vita-twitch.env)
fi
# 2) env-Beispiel muss leer sein
grep -qE '^TWITCH_(CLIENT_SECRET|WEB_AUTH_TOKEN)=.+' vita-twitch.env.example 2>/dev/null && die "env-Beispiel enthaelt Werte"
# 3) typische Fundstuecke
grep -rlE --exclude-dir=.git -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' . && die "privater Schluessel im Repo"
for p in sessions.json hls.key '*.rif' 'zrif*' '*.pem' '*.key' '*.env' eboot.bin; do
  [ -n "$(find . -path ./.git -prune -o -name "$p" -print)" ] && die "verbotene Datei im Repo: $p"
done
echo "  [ok] keine Secrets gefunden"

echo "== Commit"
git add -A
if git diff --cached --quiet; then echo "Keine Aenderungen."; exit 0; fi
VER=$(grep -oP '^VERSION = "\K[^"]+' adapter.py || echo "?")
git status --short
git commit -q -m "${MSG:-Adapter $VER – Stand vom $(date +%F)}"
git log --oneline -1
echo
echo "Hochladen:  cd $REPO && git push"
