# Changelog

## 2.31 – Nokia N8: Wiedergabe
- `/n8/ram/<login>.ram` und `/n8/ram/vod/<id>.ram`: RealPlayer-Verweis auf den RTSP-Stream (`audio/x-pn-realaudio`), den der Symbian-Browser zuverlaessig an den Videoplayer weitergibt
- `/n8/stream/<login>` liefert zusaetzlich `ram`, neu `/n8/vod/<id>` fuer VODs
- Eigene Bild-Links (Banner) gehen an das N8 ebenfalls per `http://`

## 2.30 – Nokia N8
- Hostname jetzt `ttv.serverlele.de` (vorher `ytv`), RSA-Zertifikat heisst jetzt `ttv-rsa`
- N8-Clients (`?client=n8` oder User-Agent `N8Twitch`) bekommen alle Bild-URLs ueber den eigenen Bildproxy per `http://`, verkleinert und ohne Abdunkeln
- Neuer Endpunkt `/n8/stream/<login>` liefert die RTSP-Adresse (MediaMTX, Standard `rtsp://<host>:8554/tw/<login>`)
- Neue Variablen `VITA_N8_RTSP_BASE` und `VITA_N8_HTTP_BASE`

## 2.29 – Sicherheit
- Browser-Seiten nur über HTTPS: Port 80 leitet um, `POST /link` über HTTP wird abgelehnt
- Strenge Content-Security-Policy (Skript per Hash), Schutz gegen Einbetten (`frame-ancestors 'none'`, `X-Frame-Options`), Permissions-Policy, COOP
- Code-Freigabe nur noch per Formular (POST): ein geteilter Link `/?code=…` füllt nur das Feld vor und führt nicht mehr direkt zu Twitch, das erschwert Phishing
- Login-Rechte verkleinert: kein `chat:read`/`chat:edit` mehr (die App hat keinen Chat)
- Zertifikat: `vita-cert-renew` + systemd-Timer statt Cron, täglich, mit Ablauf-Prüfung, Warnung unter 14 Tagen und `--dry-run`
- Limits gegen Missbrauch: Login-Start 20, Code-Eingabe 30, Token-Verknüpfung 10 je 10 Minuten und IP; Speicher dafür wird aufgeräumt

## 2.28
- Live-Zähler auf der Startseite: verbundene Vitas, davon angemeldet, schauen gerade (über `/health`, nur Anzahlen)

## 2.27
- Eine einzige Startseite für Anmelden, Werbefrei-Verknüpfung, Einrichtung und Infos
- `/activate` und `/link` leiten nur noch auf die Startseite weiter, `/?code=XXXXXX` führt direkt zu Twitch
- Lesezeichen-Button öffnet beim Klick eine Anleitung mit „Code kopieren“

## 2.26
- Jeder kann sein eigenes Website-Token hinterlegen, per Lesezeichen auf twitch.tv oder von Hand
- Twitch bestätigt, wem das Token gehört, es wirkt nur für dieses Konto, Turbo wird erkannt
- Gespeichert mit AES-GCM, stündlich geprüft, ungültige Tokens werden automatisch entfernt

## 2.25
- Das Betreiber-Token gilt nur noch für dessen eigenes Konto, alle anderen spielen anonym ab
- Wiedergabe-Tokens werden signiert; erkannt wird das Konto über Sitzung, Token oder IP

## 2.24
- Startseite für Browser mit Anleitung, Live-Status und `redirects.txt` zum Download

## 2.23
- QR-Code-Login entfernt (die App kann ihn nicht anzeigen), der 6-stellige Code bleibt
- Hostname nur noch über `VITA_PUBLIC_BASE`

## 2.22 und früher
- Kraken-v5-Nachbau auf Basis von Helix: Streams, Spiele, Suche, Kanäle, Videos, Nutzer, Follows
- Login per Kurz-Code (Device Code Flow), Sitzungen bleiben über Neustarts erhalten
- HLS-Proxy mit dauerhaftem Signaturschlüssel, Master-Playlist nur mit H.264
- Werbefrei über das Website-Token, Abo-Status über Helix
- Statistik-Anfragen (spade, mixpanel) werden sofort beantwortet: Login von ~5 min auf Sekunden
- Profile mit Followern, Aufrufen und Banner (Bild-Proxy)
- RSA-Zertifikat und CBC-Cipher für den TLS-Stack der Vita
