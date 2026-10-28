#!/bin/bash
# vita-git-export.sh – Live-Stand vom VPS in das Git-Repo uebernehmen, auf Secrets pruefen, committen.
# Aufruf:  bash vita-git-export.sh ["Commit-Nachricht"]
# Mehrfach ausfuehrbar: Es wird nur committet, wenn sich etwas geaendert hat.
set -euo pipefail

REPO=${VITA_REPO:-/root/git/vita-twitch}
HERE=$(cd "$(dirname "$0")" && pwd)
PKG=${VITA_PKG:-$HERE}   # Fallback-Quelle, wenn eine Datei live fehlt
MSG=${1:-}

die() { echo "FEHLER: $*" >&2; exit 1; }
cp_first() {  # cp_first ZIEL QUELLE1 [QUELLE2 ...]  -> erste vorhandene Quelle kopieren
  local dst=$1; shift
  for src in "$@"; do [ -f "$src" ] && { [ "$src" -ef "$REPO/$dst" ] && return 0; install -D -m 644 "$src" "$REPO/$dst"; return 0; }; done
  echo "  [--] nicht gefunden: $dst"
}

mkdir -p "$REPO"; cd "$REPO"
[ -d .git ] || { git init -q -b main; echo "Repo angelegt: $REPO"; }

echo "== Live-Dateien uebernehmen"
cp_first adapter.py                    /opt/vita-twitch/adapter.py "$PKG/adapter.py"
cp_first www/index.html                /opt/vita-twitch/www/index.html "$PKG/www/index.html"
rm -f "$REPO/www/link.html"   # seit 2.27 alles auf einer Seite
cp_first systemd/vita-twitch.service   /etc/systemd/system/vita-twitch.service "$PKG/systemd/vita-twitch.service"
cp_first nginx/vita-twitch.conf        /root/npm/data/nginx/custom/http.conf "$PKG/nginx/vita-twitch.conf"
cp_first bin/vita-cert-renew           /usr/local/sbin/vita-cert-renew "$PKG/bin/vita-cert-renew"
cp_first systemd/vita-cert-renew.service /etc/systemd/system/vita-cert-renew.service "$PKG/systemd/vita-cert-renew.service"
cp_first systemd/vita-cert-renew.timer /etc/systemd/system/vita-cert-renew.timer "$PKG/systemd/vita-cert-renew.timer"
rm -rf "$REPO/cron"   # seit 2.29: systemd-Timer statt Cron
cp_first bin/vita-timeline             /usr/local/sbin/vita-timeline "$PKG/bin/vita-timeline"
cp_first bin/vita-twitch-check         /usr/local/sbin/vita-twitch-check "$PKG/bin/vita-twitch-check"
cp_first dns/dnsmasq-vita.conf         /etc/dnsmasq.d/vita.conf /root/vita-dnsmasq.conf.bak
# Drop-ins (falls install.sh noch nicht gelaufen ist)
if [ -d /etc/systemd/system/vita-twitch.service.d ]; then
  for f in /etc/systemd/system/vita-twitch.service.d/*.conf; do cp_first "systemd/vita-twitch.service.d/$(basename "$f")" "$f"; done
fi

echo "== Repo-eigene Dateien"
# Diese Dateien gibt es nicht "live" auf dem Server, sie gehoeren dem Repo.
# Wird das Skript aus einem Update-Ordner gestartet, ersetzt es sie durch die Fassung von dort.
for f in README.md CHANGELOG.md .gitignore vita-git-export.sh install.sh vita/redirects.txt vita-twitch.env.example; do
  if [ "$HERE" != "$REPO" ] && [ -f "$HERE/$f" ]; then
    install -D -m 644 "$HERE/$f" "$REPO/$f"
  elif [ ! -f "$REPO/$f" ]; then
    cp_first "$f" "$PKG/$f"
  fi
done
chmod 755 bin/* install.sh vita-git-export.sh 2>/dev/null || true

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
for p in sessions.json 'webtokens*' hls.key '*.rif' 'zrif*' '*.pem' '*.key' '*.env' eboot.bin; do
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
