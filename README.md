# vita-twitch

Mit diesem Projekt läuft die **offizielle Twitch-App der PS Vita** (PCSB00878) wieder. Twitch hat die alte Kraken-API (v5) abgeschaltet, deshalb lädt die App von sich aus nichts mehr. Dieser Adapter bildet die Kraken-API auf Basis der heutigen **Helix-API** nach. Er liefert Live-Streams und VODs als Vita-taugliches HLS und stellt einen Login mit Kurz-Code bzw. QR-Code bereit.

Die App bleibt unverändert, es wird kein Patch eingespielt. Die Vita leitet die Twitch-Hostnamen nur per tls_fixer auf den eigenen Server um.

## Funktionen
- Startseite, Top-Spiele, Spiele-Verzeichnis, Suche (Streams, Kanäle, Spiele)
- Live-Wiedergabe: H.264 bis 720p, bevorzugt 480p, über einen signierten HLS-Proxy
- VODs: Übertragungen und Highlights
- Login per 6-stelligem Code oder QR-Code über `/activate`, im Hintergrund der Twitch Device Code Flow; die Sitzung wird automatisch erneuert
- Gefolgte Kanäle, Profile mit Follower-Zahl, Aufrufen und Banner
- **Werbefrei mit Turbo/Abo**, wenn das `auth-token` des Web-Kontos hinterlegt ist
- Analytics-Endpunkte (spade, mixpanel) werden sofort beantwortet, damit die App nicht blockiert

## Aufbau
```
PS Vita (tls_fixer: api.twitch.tv, usher.*, spade.twitch.tv, api.mixpanel.com -> eigener Host)
  -> nginx im Nginx-Proxy-Manager-Container, Port 80/443, RSA-Zertifikat + alte CBC-Cipher
     -> adapter.py (Python, 127.0.0.1:8443, systemd vita-twitch.service)
        -> Twitch Helix-API / yt-dlp
```
Warum RSA und CBC: Der ältere TLS-Stack der Vita kennt nur `TLS_RSA_WITH_AES_*_CBC_SHA(256)`.

## Repo-Inhalt
| Pfad | Ziel auf dem Server |
|---|---|
| `adapter.py` | `/opt/vita-twitch/adapter.py` |
| `systemd/vita-twitch.service` | `/etc/systemd/system/` |
| `nginx/vita-twitch.conf` | `/root/npm/data/nginx/custom/http.conf` (NPM) |
| `cron/vita-twitch-cert` | `/etc/cron.d/` – Verlängerung des RSA-Zertifikats |
| `bin/vita-timeline`, `bin/vita-twitch-check` | `/usr/local/sbin/` – Diagnose und Endpunkt-Prüfung |
| `vita-twitch.env.example` | Vorlage für `/etc/vita-twitch.env` (chmod 600) |
| `vita/redirects.txt` | tls_fixer-Konfiguration auf der Vita |
| `dns/dnsmasq-vita.conf` | nur für Geräte ohne tls_fixer (z. B. Tests, PS3); im Normalbetrieb nicht nötig |
| `install.sh` | Installation bzw. Update mit Backup und nginx-Rollback |
| `vita-git-export.sh` | übernimmt den Live-Stand vom Server ins Repo, mit Secret-Prüfung |

## Installation
```bash
git clone <repo-url> /root/vita-twitch-src
cd /root/vita-twitch-src
bash install.sh
```
Voraussetzungen: Python 3, `yt-dlp`, Pillow, `qrcode`, Nginx Proxy Manager als Container `npm-npm-1` und eine Twitch-Anwendung (Client-ID und Secret) von dev.twitch.tv. Die Zugangsdaten gehören **ausschließlich** in `/etc/vita-twitch.env`.

## Vita
- HENkaku/Enso, **tls_fixer** und **iTLS** installiert, `vita/redirects.txt` nach `ur0:tai/` kopieren.
- Login: Den Code auf der Vita unter `https://<host>/activate` eingeben oder den QR-Code scannen.

## Diagnose
```bash
journalctl -u vita-twitch -f -g Sony-PSV
vita-timeline 10
vita-twitch-check https://<host> --verify
```

## Grenzen
- Kein Chat: Die Vita-App hatte nie einen.
- Folgen und Entfolgen sind nicht möglich, weil Twitch das per API abgeschafft hat.
- Kanal-Aufrufe = Summe der Video-Aufrufe, da Helix keine Kanal-Aufrufe mehr liefert.
- Werbefreiheit nur mit hinterlegtem Web-`auth-token`; dieser ist so viel wert wie ein Passwort und gehört nur in die env-Datei.

## Hinweis
Ein inoffizielles Hobbyprojekt ohne Verbindung zu Twitch oder Sony. Das Repo enthält keinen App-Code, keine Lizenzdateien und keine Zugangsdaten.
