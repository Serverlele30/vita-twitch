<div align="center">

# 📺 vita-twitch

**Bringt die offizielle Twitch-App der PS Vita zurück, ohne Patch und ohne Mod an der App.**

![PS Vita](https://img.shields.io/badge/PS%20Vita-PCSB00878-003791?style=flat-square)
![Twitch](https://img.shields.io/badge/Twitch-Helix%20API-9147ff?style=flat-square)
![Python](https://img.shields.io/badge/Python-3-3776ab?style=flat-square)
![Abhängigkeiten](https://img.shields.io/badge/Framework-keins-2ea44f?style=flat-square)

Live-Streams · VODs · Login · gefolgte Kanäle · werbefrei mit Turbo, Abo oder Prime

**Live:** [ytv.serverlele.de](https://ytv.serverlele.de)

</div>

---

Twitch hat die alte **Kraken-API (v5)** abgeschaltet. Seitdem zeigt die Vita-App nur noch Ladekreise und Fehlercodes. **vita-twitch** ist ein kleiner Server, der sich der App gegenüber genau wie das Twitch von 2014 verhält. Hinter den Kulissen holt er alles aus der heutigen **Helix-API** und bereitet die Streams so auf, dass die Vita sie abspielen kann.

Die App bleibt unverändert. Auf der Vita kommt nur das Plugin [tls_fixer](#-für-vita-besitzer) dazu, das die Twitch-Adressen auf den eigenen Server umleitet.

## ✨ Was geht

| | |
|---|---|
| 🔴 **Live-Streams** | H.264 bis 720p, Standard 480p, über einen signierten HLS-Proxy |
| 🎞️ **VODs** | Übertragungen und Highlights |
| 🏠 **Startseite und Suche** | Empfehlungen, Top-Spiele, Spiele-Verzeichnis, Suche nach Streams, Kanälen und Spielen |
| 🔑 **Login** | 6-stelliger Code auf der Vita, im Browser bestätigen (Twitch Device Code Flow), Sitzung erneuert sich selbst |
| 💜 **Gefolgte Kanäle** | Live-Übersicht und Kanalliste |
| 👤 **Profile** | Follower, Aufrufe, Banner |
| 🚫 **Werbefrei** | pro Konto mit dem eigenen Twitch-Token: Turbo = alles werbefrei, Abo oder Prime = abonnierte Kanäle |
| 🌐 **Startseite** | Code eingeben, Werbefrei verknüpfen, Einrichtung, FAQ, **Live-Zähler** für verbundene Vitas |
| ⚡ **Schnell** | Cache, vorgewärmte Daten, Statistik-Anfragen der App werden sofort beantwortet |

## 🧭 So funktioniert es

```
 ┌──────────────┐  tls_fixer leitet api.twitch.tv,
 │   PS Vita    │  usher.*, spade, mixpanel um
 │  Twitch-App  │──────────────────────────────┐
 └──────────────┘                              ▼
                                ┌───────────────────────────┐
                                │ nginx  :443               │  RSA-Zertifikat und die alten
                                │ (Nginx Proxy Manager)     │  CBC-Cipher, die die Vita kennt
                                └─────────────┬─────────────┘
                                              ▼
                                ┌───────────────────────────┐
                                │ adapter.py  127.0.0.1:8443│  Kraken v5 → Helix
                                │                           │  Login · HLS-Proxy · Startseite
                                └─────────────┬─────────────┘
                                              ▼
                                   Twitch Helix-API · yt-dlp
```

<details>
<summary><b>Die drei Stolpersteine, an denen es vorher scheiterte</b></summary>

1. **TLS:** Der ältere TLS-Stack der Vita beherrscht nur `TLS_RSA_WITH_AES_*_CBC_SHA(256)`. Moderne Server mit ECDSA-Zertifikat lehnt er ab. Deshalb gibt es ein eigenes RSA-Zertifikat und eine passende Cipher-Liste.
2. **Die blockierte Warteschlange:** Der HTTP-Client der App arbeitet alles **nacheinander** ab. Jede Statistik-Anfrage an `spade.twitch.tv` oder `api.mixpanel.com` lief ins Leere und hielt die App bis zu einer halben Minute auf. Den Login traf das mit fast 5 Minuten. Der Adapter bestätigt diese Anfragen jetzt sofort, damit läuft der Login in Sekunden durch.
3. **Wiedergabe:** Twitchs heutige Streams tragen Erweiterungen, mit denen die Vita nichts anfangen kann. Der Adapter baut eine eigene Master-Playlist nur aus H.264-Varianten und leitet Playlists und Segmente über signierte Links weiter.

</details>

## 🎮 Für Vita-Besitzer

Du brauchst eine Vita mit **HENkaku oder Enso**, **tls_fixer** und **iTLS** sowie die **Twitch-App** (PCSB00878).

1. In die `redirects.txt` von tls_fixer eintragen (Vorlage: [`vita/redirects.txt`](vita/redirects.txt), oder herunterladen unter `https://<host>/redirects.txt`):
   ```
   api.twitch.tv    <host>
   usher.twitch.tv  <host>
   usher.ttvnw.net  <host>
   usher.justin.tv  <host>
   spade.twitch.tv  <host>
   api.mixpanel.com <host>
   ```
2. Vita neu starten und die Twitch-App öffnen. Die Startseite lädt.
3. **Anmelden:** Die Vita zeigt einen Code. Den gibst du auf `https://<host>/` ein und bestätigst bei Twitch.
4. **Optional werbefrei:** Auf derselben Seite unter *Werbefrei* das Lesezeichen auf twitch.tv anklicken. Eine Anleitung steht dort.

## 🖥️ Eigenen Server aufsetzen

**Voraussetzungen:** Linux mit Python 3, [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), Pillow, `python3-cryptography`, Nginx Proxy Manager als Docker-Container `npm-npm-1` und eine Twitch-Anwendung von [dev.twitch.tv](https://dev.twitch.tv/console) mit Typ *Confidential* und aktiviertem Device Code Flow.

```bash
git clone https://git.serverlele.de/Serverlele30/vita-twitch.git
cd vita-twitch
bash install.sh                      # legt /etc/vita-twitch.env an, falls sie fehlt
nano /etc/vita-twitch.env            # Client-ID, Secret, VITA_PUBLIC_BASE eintragen
bash install.sh                      # noch einmal: installiert, prüft nginx und startet den Dienst
vita-twitch-check https://<host> --verify
```

`install.sh` sichert vorher alles nach `/root/vita-twitch-backup-<Zeit>`, testet die nginx-Konfiguration und stellt den alten Stand wieder her, wenn etwas schiefgeht. Eine vorhandene env-Datei bleibt unangetastet. Den Beispiel-Host `ytv.serverlele.de` ersetzt du in `nginx/`, `cron/` und `vita/` durch deinen eigenen.

<details>
<summary><b>Einstellungen in <code>/etc/vita-twitch.env</code></b></summary>

| Variable | Standard | Bedeutung |
|---|---|---|
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | – | Twitch-Anwendung (**Pflicht**) |
| `VITA_PUBLIC_BASE` | `https://ytv.serverlele.de` | eigener Host, nach außen sichtbar |
| `TWITCH_WEB_AUTH_TOKEN` | – | Website-Token des Betreibers, gilt nur für dessen eigenes Konto |
| `VITA_WEB_TOKEN_SHARE` | `0` | `1` = Betreiber-Token für alle Konten (nicht empfohlen) |
| `VITA_TURBO` | `0` | dem Betreiber-Konto in der App Turbo anzeigen |
| `VITA_PREF_HEIGHT` / `VITA_MAX_HEIGHT` | `480` / `720` | bevorzugte bzw. höchste Auflösung |
| `VITA_ONLINE_WINDOW` | `600` | Sekunden, die eine Vita im Live-Zähler als verbunden gilt |
| `VITA_IP_MATCH_HOURS` | `12` | Zuordnung Stream → Konto über die IP der letzten Aktivität |
| `VITA_PROFILE_BG` / `VITA_BANNER_DIM` | `#1f1f23` / `0.4` | Darstellung der Profilkarte |
| `VITA_REPO_URL` | dieses Repo | Link zum Quellcode auf der Startseite |

</details>

<details>
<summary><b>Dateien im Repo</b></summary>

| Pfad | Ziel auf dem Server |
|---|---|
| `adapter.py` | `/opt/vita-twitch/adapter.py`, der gesamte Adapter als eine Datei |
| `www/index.html` | `/opt/vita-twitch/www/`, die Startseite |
| `systemd/vita-twitch.service` | `/etc/systemd/system/` |
| `nginx/vita-twitch.conf` | `/root/npm/data/nginx/custom/http.conf` |
| `cron/vita-twitch-cert` | `/etc/cron.d/`, verlängert das RSA-Zertifikat |
| `bin/vita-timeline` | Zeitleiste aller Vita-Anfragen mit markierten Pausen |
| `bin/vita-twitch-check` | prüft über 1500 Punkte aller Endpunkte |
| `vita/redirects.txt` | Vorlage für tls_fixer |
| `dns/dnsmasq-vita.conf` | nur für Geräte ohne tls_fixer, im Normalbetrieb nicht nötig |
| `install.sh` · `vita-git-export.sh` | Installation · Live-Stand ins Repo übernehmen, mit Secret-Prüfung |

</details>

## 🔍 Diagnose

```bash
journalctl -u vita-twitch -f -g Sony-PSV          # alles, was die Vita anfragt
journalctl -u vita-twitch -g "Stream .* fuer"     # welches Konto mit oder ohne Werbung schaut
vita-timeline 10                                  # wo entstehen Wartezeiten?
curl -s https://<host>/health                     # Status und Live-Zähler
```

## 🔒 Sicherheit und Datenschutz

- Zugangsdaten stehen nur in `/etc/vita-twitch.env` (Rechte 600), nie im Code. Logs schwärzen `token`, `oauth_token` und `sig`.
- Die Twitch-Tokens aus dem Code-Login bleiben auf dem Server. Die Vita bekommt nur einen eigenen Sitzungsschlüssel.
- Die freiwillig hinterlegten Website-Tokens werden mit AES-GCM verschlüsselt gespeichert. Twitch bestätigt, wem ein Token gehört, und es wirkt nur für dieses Konto. Stündlich wird es geprüft, ungültige werden automatisch gelöscht. Das Lesezeichen übergibt das Token im `#`-Teil der Adresse, und der wird nie an einen Server gesendet.
- Stream-Links sind per HMAC signiert und nur zu Twitch-Hosts erlaubt. Wiedergabe-Tokens sind signiert und lassen sich nicht fälschen.
- Der Live-Zähler zeigt nach außen nur Anzahlen, keine IPs.

## 🚧 Grenzen

- **Kein Chat:** Die Vita-App hatte nie einen.
- **Folgen und Entfolgen:** Das hat Twitch per API abgeschafft.
- **Werbefrei nur mit eigenem Website-Token:** Twitch gibt werbefreie Streams nur an Logins der offiziellen Website heraus.
- **Kein QR-Code-Login:** Die App kann ihn nicht anzeigen.
- **Kanal-Aufrufe** sind die Summe der Video-Aufrufe, weil Helix keinen Kanalwert mehr liefert.
- **PS3:** Die PS3 hat kein tls_fixer und bräuchte einen eigenen Weg.

## 📜 Hinweis

Inoffizielles Hobbyprojekt ohne Verbindung zu Twitch Interactive oder Sony Interactive Entertainment. „Twitch“ und „PlayStation“ sind Marken ihrer jeweiligen Inhaber. Das Repo enthält keinen Code der App, keine Lizenzdateien und keine Zugangsdaten. Die Nutzung erfolgt auf eigenes Risiko.

Änderungen stehen in der [CHANGELOG.md](CHANGELOG.md).
