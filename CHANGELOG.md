# Changelog

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
