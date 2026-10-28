#!/usr/bin/env python3
"""
PS Vita Twitch Adapter v2 -- bildet die alte Twitch-Kraken-API (v3, Stand 2014-2016)
auf Basis der heutigen Helix-API nach, damit die offizielle Vita-Twitch-App wieder laeuft.

Zugangsdaten kommen ausschliesslich aus der Umgebung (/etc/vita-twitch.env):
  TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET
Optionale Umgebungsvariablen (Defaults entsprechen der bestehenden Installation):
  VITA_TWITCH_HOST=127.0.0.1  VITA_TWITCH_PORT=8443
  VITA_TWITCH_CERT=/etc/ssl/certs/vita-twitch-test.crt
  VITA_TWITCH_KEY=/etc/ssl/private/vita-twitch-test.key
  VITA_TWITCH_LOG_DIR=/var/log/vita-twitch
  VITA_MAX_HEIGHT=720          hoechste HLS-Aufloesung, die an die Vita geht
  VITA_STRIP_NON_BMP=1         Emoji u.ae. aus Texten entfernen (alter Vita-Zeichensatz)
"""
import base64
import gzip
import html
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import re
import secrets
import ssl
import struct
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "2.29"

HOST = os.environ.get("VITA_TWITCH_HOST", "127.0.0.1")
PORT = int(os.environ.get("VITA_TWITCH_PORT", "8443"))
CERT = os.environ.get("VITA_TWITCH_CERT", "/etc/ssl/certs/vita-twitch-test.crt")
KEY = os.environ.get("VITA_TWITCH_KEY", "/etc/ssl/private/vita-twitch-test.key")
LOG_DIR = os.environ.get("VITA_TWITCH_LOG_DIR", "/var/log/vita-twitch")
MAX_HEIGHT = int(os.environ.get("VITA_MAX_HEIGHT", "720"))
PREF_HEIGHT = int(os.environ.get("VITA_PREF_HEIGHT", "480"))   # diese Variante steht zuerst
# HLS ueber den Adapter leiten (Vita-Player spricht kein modernes TLS): 1 = an
HLS_PROXY = os.environ.get("VITA_HLS_PROXY", "1") == "1"
HLS_HOSTS = tuple(h.strip() for h in os.environ.get(
    "VITA_HLS_HOSTS", "ttvnw.net,live-video.net,twitch.tv,jtvnw.net,cloudfront.net").split(",") if h.strip())
HLS_KEY = os.urandom(32)   # signiert Proxy-Links; neu pro Start
STATE_DIR = os.environ.get("VITA_STATE_DIR", "/var/lib/vita-twitch")
LOGIN_SCOPES = os.environ.get("VITA_LOGIN_SCOPES",
                              "user:read:follows user:read:email user:read:subscriptions")   # nur, was die App braucht (kein Chat)
STRIP_NON_BMP = os.environ.get("VITA_STRIP_NON_BMP", "1") != "0"
# Content-Type: "echo" = angefragte Kraken-Version zurueckmelden (z.B. vnd.twitchtv.v4+json),
#               "json" = application/json
CT_MODE = os.environ.get("VITA_CT_MODE", "echo")
# gzip: "auto" = wenn der Client es anbietet, "off" = nie
GZIP_MODE = os.environ.get("VITA_GZIP", "auto")
# Experiment-Schalter fuer die Antwortgroesse:
#   VITA_MAX_ITEMS=n  hoechstens n Eintraege pro Liste (0 = wie angefragt)
#   VITA_LITE=1       optionale, grosse Felder weglassen (verschachtelte _links,
#                     description, Banner-Felder, prev-Links)
MAX_ITEMS = int(os.environ.get("VITA_MAX_ITEMS", "0"))
LITE = os.environ.get("VITA_LITE", "0") == "1"
# Format-Varianten fuer die Suche nach dem v4-Vertrag:
#   VITA_ID_STRINGS=1     alle "_id"-Werte als String (Stil ab Kraken v5)
#   VITA_PREVIEW_STRING=1 stream.preview als einfacher URL-String (Stil Kraken v2)
#   VITA_HTTP_IMAGES=1    Bild-URLs mit http:// statt https://
ID_STRINGS = os.environ.get("VITA_ID_STRINGS", "0") == "1"
PREVIEW_STRING = os.environ.get("VITA_PREVIEW_STRING", "0") == "1"
HTTP_IMAGES = os.environ.get("VITA_HTTP_IMAGES", "0") == "1"
LITE_DROP = {"description", "banner", "background", "profile_banner",
             "profile_banner_background_color", "video_banner", "broadcaster_type", "prev"}

CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
# Optional: "auth-token"-Cookie von twitch.tv (Web-Login). Damit holt der Adapter die
# Wiedergabe-Freigabe im Namen dieses Kontos -> Turbo / Kanal-Abo (auch Prime) = werbefrei.
WEB_AUTH_TOKEN = os.environ.get("TWITCH_WEB_AUTH_TOKEN", "").strip()
# Hast du Twitch Turbo? Helix kann das nicht abfragen -> selbst angeben (VITA_TURBO=1).
TURBO = os.environ.get("VITA_TURBO", "0") == "1"
# Das Web-Token gilt nur fuer seinen Besitzer: Andere Konten spielen anonym ab und bekommen die
# Werbung, die Twitch ihnen zuteilt. VITA_WEB_TOKEN_SHARE=1 stellt das alte Verhalten (alle) wieder her.
WEB_OWNER = os.environ.get("VITA_WEB_TOKEN_OWNER", "").strip().lower() or None   # sonst beim Start ermittelt
WEB_SHARE = os.environ.get("VITA_WEB_TOKEN_SHARE", "0") == "1"
IP_MATCH_HOURS = float(os.environ.get("VITA_IP_MATCH_HOURS", "12"))
# Profilkarte: Hintergrundfarbe + Ersatz-Banner (Twitch liefert beides nicht mehr)
PROFILE_BG = os.environ.get("VITA_PROFILE_BG", "#1f1f23")
PUBLIC_BASE = os.environ.get("VITA_PUBLIC_BASE", "https://ytv.serverlele.de").rstrip("/")
PUBLIC_HOST = urllib.parse.urlsplit(PUBLIC_BASE).netloc
# Infoseite fuer Browser unter "/" (die App bekommt dort weiter JSON)
WWW_DIR = os.environ.get("VITA_WWW_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "www"))
REPO_URL = os.environ.get("VITA_REPO_URL", "https://git.serverlele.de/Serverlele30/vita-twitch")
REDIRECT_HOSTS = ("api.twitch.tv", "usher.twitch.tv", "usher.ttvnw.net", "usher.justin.tv",
                  "spade.twitch.tv", "api.mixpanel.com")
# Der Bildlader der App holt Bilder nur von Twitchs Bilderserver -> Ersatzbilder von dort
DEFAULT_BANNER = "https://static-cdn.jtvnw.net/ttv-static/404_preview-498x280.jpg"
# Banner abdunkeln, damit die helle Schrift (Follower/Aufrufe/Bio) lesbar bleibt: 1.0 = Original
BANNER_DIM = float(os.environ.get("VITA_BANNER_DIM", "0.4"))
# Diagnose/Auswahl: proxy (verkleinert, Standard), direct (Original-URL), none (kein Banner)
BANNER_MODE = os.environ.get("VITA_BANNER_MODE", "proxy")

HELIX = "https://api.twitch.tv/helix/"
MAX_PAGES = 10          # max. 10 Helix-Seiten a 100 = Offset bis 1000
WARM_INTERVAL = int(os.environ.get("VITA_WARM_INTERVAL", "30"))  # Sekunden
LIST_TTL = WARM_INTERVAL * 3   # Listen bleiben gueltig, bis der naechste Refresh sicher durch ist
SAMPLE_STREAMS = 500    # Stichprobe fuer Zuschauer-/Kanalzahlen je Spiel

# --------------------------------------------------------------------------
# Logging: Journal (stderr) + JSONL-Requestlog fuer die Analyse des App-Verhaltens
# --------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("vita-twitch")

reqlog = logging.getLogger("vita-twitch.requests")
reqlog.propagate = False
try:
    os.makedirs(LOG_DIR, exist_ok=True)
    _h = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "requests.jsonl"), maxBytes=2_000_000, backupCount=3)
    _h.setFormatter(logging.Formatter("%(message)s"))
    reqlog.addHandler(_h)
    reqlog.setLevel(logging.INFO)
except OSError as exc:  # Logdatei ist nice-to-have, kein Startabbruch
    log.warning("Requestlog in %s nicht moeglich: %s", LOG_DIR, exc)


class ApiError(Exception):
    """Fehler, der als Kraken-Fehlerobjekt an die App geht."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


REASONS = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 422: "Unprocessable Entity",
           429: "Too Many Requests", 500: "Internal Server Error",
           502: "Bad Gateway", 503: "Service Unavailable"}


# --------------------------------------------------------------------------
# Kleiner TTL-Cache
# --------------------------------------------------------------------------
class TTLCache:
    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if hit and hit[0] > time.time():
                return hit[1]
            self._d.pop(key, None)
            return None

    def set(self, key, value, ttl):
        with self._lock:
            if len(self._d) > 5000:
                self._d.clear()
            self._d[key] = (time.time() + ttl, value)


cache = TTLCache()

# --------------------------------------------------------------------------
# Twitch Helix
# --------------------------------------------------------------------------
_token = None
_token_expiry = 0.0
_token_lock = threading.Lock()


def http_json(url, method="GET", data=None, headers=None, timeout=15):
    hdrs = {"User-Agent": "VitaTwitchAdapter/" + VERSION}
    hdrs.update(headers or {})
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_token(force=False):
    global _token, _token_expiry
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ApiError(503, "Adapter not configured (TWITCH_CLIENT_ID/SECRET missing)")
    with _token_lock:
        if not force and _token and time.time() < _token_expiry - 120:
            return _token
        try:
            res = http_json("https://id.twitch.tv/oauth2/token", method="POST", data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "client_credentials",
            })
        except urllib.error.HTTPError as exc:
            # Antworttext kann Hinweise enthalten, aber nie das Secret
            log.error("Token-Abruf fehlgeschlagen: HTTP %s", exc.code)
            raise ApiError(502, "Twitch authentication failed") from exc
        except Exception as exc:
            log.error("Token-Abruf fehlgeschlagen: %s", type(exc).__name__)
            raise ApiError(502, "Twitch authentication unreachable") from exc
        _token = res["access_token"]
        _token_expiry = time.time() + int(res.get("expires_in", 3600))
        log.info("Neues App-Access-Token erhalten (gueltig %ss)", res.get("expires_in"))
        return _token


def helix(path, params=None):
    """GET auf Helix mit Wiederholung bei 401 (Token neu), 5xx und Netzwerkfehlern."""
    if isinstance(params, dict):
        params = list(params.items())
    query = urllib.parse.urlencode(params or [], doseq=True)
    url = HELIX + path + ("?" + query if query else "")
    force = False
    last = None
    for attempt in range(1, 4):
        headers = {"Client-Id": CLIENT_ID, "Authorization": "Bearer " + get_token(force=force)}
        force = False
        try:
            return http_json(url, headers=headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace")
            if exc.code == 401:
                log.warning("Helix 401 auf %s - Token wird erneuert", path)
                force, last = True, ApiError(502, "Twitch API authentication failed")
                continue
            if exc.code >= 500:
                log.warning("Helix HTTP %s auf %s (Versuch %d/3)", exc.code, path, attempt)
                last = ApiError(502, "Twitch API returned HTTP %d" % exc.code)
                time.sleep(0.3 * attempt)
                continue
            log.error("Helix HTTP %s auf %s: %s", exc.code, path, detail)
            if exc.code == 429:
                raise ApiError(503, "Twitch rate limit reached, try again") from exc
            if exc.code == 400:
                raise ApiError(400, "Invalid request") from exc
            raise ApiError(502, "Twitch API returned HTTP %d" % exc.code) from exc
        except ApiError:
            raise
        except Exception as exc:
            log.warning("Helix-Aufruf %s fehlgeschlagen (Versuch %d/3): %s: %s",
                        path, attempt, type(exc).__name__, exc)
            last = ApiError(502, "Twitch API unreachable")
            time.sleep(0.3 * attempt)
    log.error("Helix %s nach 3 Versuchen fehlgeschlagen", path)
    raise last


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------
def as_int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def num_id(value):
    s = str(value or "")
    return int(s) if s.isdigit() else 0


def cap(limit):
    return min(limit, MAX_ITEMS) if MAX_ITEMS > 0 else limit


def lite(obj, depth=0):
    if isinstance(obj, list):
        return [lite(x, depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {k: lite(v, depth + 1) for k, v in obj.items()
                if k not in LITE_DROP and not (k == "_links" and depth > 0)}
    return obj


def variant(obj, key=None):
    """Wendet die per Schalter gewaehlten Format-Varianten an."""
    if isinstance(obj, list):
        return [variant(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "preview" and PREVIEW_STRING and isinstance(v, dict):
                out[k] = variant(v.get("medium", ""))
            elif k == "_id" and ID_STRINGS and isinstance(v, int) and not isinstance(v, bool):
                out[k] = str(v)
            else:
                out[k] = variant(v, k)
        return out
    if isinstance(obj, str) and HTTP_IMAGES and obj.startswith("https://static-cdn.jtvnw.net/"):
        return "http://" + obj[len("https://"):]
    return obj


def first(params, *names, default=""):
    for n in names:
        v = params.get(n)
        if v and v[0] != "":
            return v[0]
    return default


def art(template, w, h):
    return (template or "").replace("{width}", str(w)).replace("{height}", str(h))


_NON_BMP = re.compile("[\U00010000-\U0010FFFF]")
_CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize(obj):
    """Emoji/Steuerzeichen entfernen - die alte App rendert nur die BMP."""
    if isinstance(obj, str):
        obj = _CTRL.sub("", obj)
        return _NON_BMP.sub("", obj).strip() if STRIP_NON_BMP else obj
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    return obj


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def users_by_ids(ids):
    out, missing = {}, []
    for uid in dict.fromkeys(str(i) for i in ids if i):
        hit = cache.get(("user", uid))
        if hit:
            out[uid] = hit
        else:
            missing.append(uid)
    for part in chunks(missing, 100):
        for u in helix("users", [("id", x) for x in part]).get("data", []):
            cache.set(("user", u["id"]), u, 3600)
            cache.set(("login", u["login"].lower()), u, 3600)
            out[u["id"]] = u
    return out


def user_by_login(login):
    login = (login or "").lower().strip()
    if not re.fullmatch(r"[a-z0-9_]{1,25}", login):
        return None
    hit = cache.get(("login", login))
    if hit:
        return hit
    data = helix("users", {"login": login}).get("data", [])
    if not data:
        return None
    u = data[0]
    cache.set(("login", login), u, 600)
    cache.set(("user", u["id"]), u, 600)
    return u


def game_id_by_name(name):
    key = ("gamename", name.lower())
    hit = cache.get(key)
    if hit is not None:
        return hit
    data = helix("games", {"name": name}).get("data", [])
    gid = data[0]["id"] if data else ""
    cache.set(key, gid, 3600)
    return gid


# --------------------------------------------------------------------------
# Kraken-v3-Objekte
# --------------------------------------------------------------------------
def channel_links(base, login):
    c = base + "/kraken/channels/" + login
    return {"self": c, "follows": c + "/follows", "commercial": c + "/commercial",
            "stream_key": c + "/stream_key", "chat": base + "/kraken/chat/" + login,
            "features": c + "/features", "subscriptions": c + "/subscriptions",
            "editors": c + "/editors", "teams": c + "/teams", "videos": c + "/videos"}


def channel_obj(base, user, title="", game="", language="", updated=""):
    login = user.get("login", "")
    return {
        "mature": False,
        "status": title,
        "broadcaster_language": language,
        "display_name": user.get("display_name") or login,
        "game": game or None,
        "delay": None,
        "language": language,
        "_id": num_id(user.get("id")),
        "name": login,
        "created_at": user.get("created_at", ""),
        "updated_at": updated or user.get("created_at", ""),
        "logo": user.get("profile_image_url") or None,
        "banner": None,
        "video_banner": user.get("offline_image_url") or None,
        "background": None,
        "profile_banner": user.get("offline_image_url") or DEFAULT_BANNER,
        "profile_banner_background_color": PROFILE_BG,
        "partner": user.get("broadcaster_type") == "partner",
        "url": "http://www.twitch.tv/" + login,
        "views": int(user.get("view_count") or 0),
        "followers": 0,
        "broadcaster_type": user.get("broadcaster_type") or "",
        "description": user.get("description") or "",
        "_links": channel_links(base, login),
    }


def stream_obj(base, s, user):
    login = s.get("user_login") or user.get("login", "")
    user = dict(user or {})
    user.setdefault("login", login)
    user.setdefault("id", s.get("user_id"))
    user.setdefault("display_name", s.get("user_name") or login)
    tpl = s.get("thumbnail_url") or ""
    return {
        "_id": num_id(s.get("id")),
        "game": s.get("game_name") or None,
        "viewers": int(s.get("viewer_count") or 0),
        "created_at": s.get("started_at", ""),
        "video_height": 720,       # Helix liefert das nicht mehr -> plausibler Wert
        "average_fps": 30.0,       # dito
        "delay": 0,
        "is_playlist": False,
        "stream_type": "live",
        "_links": {"self": base + "/kraken/streams/" + login},
        "preview": {"small": art(tpl, 80, 45), "medium": art(tpl, 320, 180),
                    "large": art(tpl, 640, 360), "template": tpl},
        "channel": channel_obj(base, user, s.get("title", ""), s.get("game_name", ""),
                               s.get("language", ""), s.get("started_at", "")),
    }


def streams_to_kraken(base, streams):
    users = users_by_ids([s.get("user_id") for s in streams])
    return [stream_obj(base, s, users.get(str(s.get("user_id")), {})) for s in streams]


def game_obj(gid, name, box_tpl, popularity=0):
    # search/categories liefert feste Groessen (-52x72.jpg) statt {width}x{height}
    box_tpl = re.sub(r"-\d+x\d+(\.\w+)$", r"-{width}x{height}\1", box_tpl or "")
    return {
        "name": name,
        "box": {"large": art(box_tpl, 272, 380), "medium": art(box_tpl, 136, 190),
                "small": art(box_tpl, 52, 72), "template": box_tpl},
        "logo": {"large": art(box_tpl, 240, 144), "medium": art(box_tpl, 120, 72),
                 "small": art(box_tpl, 60, 36), "template": box_tpl},
        "_links": {},
        "_id": num_id(gid),
        "giantbomb_id": 0,
        "popularity": popularity,
    }


def page_links(base, path, params, offset, limit):
    keep = [(k, v) for k, vals in params.items() for v in vals if k not in ("offset", "limit")]

    def url(off):
        return base + path + "?" + urllib.parse.urlencode(keep + [("limit", limit), ("offset", off)])

    links = {"self": url(offset), "next": url(offset + limit)}
    if offset > 0:
        links["prev"] = url(max(0, offset - limit))
    return links


# --------------------------------------------------------------------------
# Datenbeschaffung
# --------------------------------------------------------------------------
def collect_streams(filters, need, force=False):
    """Sammelt Helix-Streams (cursor-basiert) bis 'need' erreicht ist. Gecacht."""
    key = ("streams", tuple(filters))
    state = None if force else cache.get(key)
    state = state or {"items": [], "cursor": None, "done": False, "pages": 0}
    state = dict(state, items=list(state["items"]))
    while len(state["items"]) < need and not state["done"] and state["pages"] < MAX_PAGES:
        q = [("first", 100)] + list(filters)
        if state["cursor"]:
            q.append(("after", state["cursor"]))
        try:
            res = helix("streams", q)
        except ApiError:
            stale = cache.get(("stale",) + key)
            if stale and len(stale["items"]) >= min(need, len(stale["items"])) and stale["items"]:
                log.warning("Helix-Ausfall - liefere gecachte Streamliste (%d Eintraege)",
                            len(stale["items"]))
                return stale
            if state["items"]:
                state["done"] = True
                break
            raise
        data = res.get("data", [])
        state["pages"] += 1
        state["items"].extend(data)
        state["cursor"] = (res.get("pagination") or {}).get("cursor")
        if not data or not state["cursor"]:
            state["done"] = True
    cache.set(key, state, LIST_TTL if not filters else 30)
    if state["items"]:
        cache.set(("stale",) + key, state, 3600)
    return state


def stream_filters(params):
    """Kraken-Filter -> Helix-Filter. None = Filter kann nichts liefern."""
    f = []
    game = first(params, "game")
    if game:
        gid = game_id_by_name(game)
        if not gid:
            return None
        f.append(("game_id", gid))
    chans = first(params, "channel")
    if chans:
        logins = [c.strip().lower() for c in chans.split(",") if c.strip()][:100]
        f.extend(("user_login", c) for c in logins)
    lang = first(params, "language", "broadcaster_language")
    if lang:
        f.extend(("language", l) for l in lang.split(",")[:10] if l)
    # broadcaster_software=octodad (PS4-Uebertragungen) gibt es in Helix nicht mehr.
    # Bewusst ignoriert -> die App bekommt die allgemeine Liste.
    return f


def list_streams(params):
    limit = cap(as_int(first(params, "limit", default="25"), 25, 1, 100))
    offset = as_int(first(params, "offset", default="0"), 0, 0, 900)
    filters = stream_filters(params)
    if filters is None:
        return [], 0, limit, offset
    st = collect_streams(filters, offset + limit)
    page = st["items"][offset:offset + limit]
    total = len(st["items"]) if st["done"] else len(st["items"]) + limit
    return page, total, limit, offset


def game_sample():
    st = collect_streams((), SAMPLE_STREAMS)
    viewers, channels = Counter(), Counter()
    for s in st["items"]:
        gid = str(s.get("game_id") or "")
        if gid:
            viewers[gid] += int(s.get("viewer_count") or 0)
            channels[gid] += 1
    return viewers, channels, st["items"]


def top_games_list(need, force=False):
    key = ("topgames",)
    state = None if force else cache.get(key)
    state = state or {"items": [], "cursor": None, "done": False}
    state = dict(state, items=list(state["items"]))
    pages = 0
    while len(state["items"]) < need and not state["done"] and pages < MAX_PAGES:
        q = [("first", 100)]
        if state["cursor"]:
            q.append(("after", state["cursor"]))
        try:
            res = helix("games/top", q)
        except ApiError:
            stale = cache.get(("stale",) + key)
            if stale and stale["items"]:
                log.warning("Helix-Ausfall - liefere gecachte Spieleliste")
                return stale
            if state["items"]:
                state["done"] = True
                break
            raise
        data = res.get("data", [])
        pages += 1
        state["items"].extend(data)
        state["cursor"] = (res.get("pagination") or {}).get("cursor")
        if not data or not state["cursor"]:
            state["done"] = True
    cache.set(key, state, max(120, LIST_TTL))
    if state["items"]:
        cache.set(("stale",) + key, state, 3600)
    return state


# --------------------------------------------------------------------------
# Endpunkte (geben (status, payload) zurueck)
# --------------------------------------------------------------------------
def ep_root(base, params, req):
    return 200, {
        "identified": bool(first(params, "client_id") or req.headers.get("Client-ID")),
        "_links": {"user": base + "/kraken/user", "channel": base + "/kraken/channel",
                   "search": base + "/kraken/search", "streams": base + "/kraken/streams",
                   "ingests": base + "/kraken/ingests", "teams": base + "/kraken/teams"},
        "token": {"valid": False, "authorization": None},
    }


def ep_streams(base, params, req):
    page, total, limit, offset = list_streams(params)
    return 200, {"_total": total,
                 "_links": dict(page_links(base, "/kraken/streams", params, offset, limit),
                                summary=base + "/kraken/streams/summary",
                                followed=base + "/kraken/streams/followed",
                                featured=base + "/kraken/streams/featured"),
                 "streams": streams_to_kraken(base, page)}


def ep_featured(base, params, req):
    page, total, limit, offset = list_streams(params)
    feats = []
    for i, s in enumerate(streams_to_kraken(base, page)):
        feats.append({
            "image": s["preview"]["large"],
            "text": "<p>" + (s["channel"]["status"] or "") + "</p>",
            "title": s["channel"]["display_name"],
            "sponsored": False,
            "priority": max(1, 10 - i),
            "scheduled": False,
            "stream": s,
        })
    return 200, {"_links": page_links(base, "/kraken/streams/featured", params, offset, limit),
                 "featured": feats}


def ep_summary(base, params, req):
    filters = stream_filters(params)
    if filters is None:
        viewers = channels = 0
    else:
        st = collect_streams(filters, SAMPLE_STREAMS)
        viewers = sum(int(s.get("viewer_count") or 0) for s in st["items"])
        channels = len(st["items"])
    return 200, {"viewers": viewers, "channels": channels,
                 "_links": {"self": base + "/kraken/streams/summary"}}


def ep_stream_by_channel(base, params, req, login):
    user = user_by_login(login)
    if not user:
        raise ApiError(404, "Channel '%s' does not exist" % login)
    data = helix("streams", {"user_id": user["id"]}).get("data", [])
    stream = stream_obj(base, data[0], user) if data else None
    return 200, {"_links": {"self": base + "/kraken/streams/" + user["login"],
                            "channel": base + "/kraken/channels/" + user["login"]},
                 "stream": stream}


def ep_channel(base, params, req, login):
    user = user_by_login(login)
    if not user:
        raise ApiError(404, "Channel '%s' does not exist" % login)
    info = helix("channels", {"broadcaster_id": user["id"]}).get("data", [])
    info = info[0] if info else {}
    ch = channel_obj(base, user, info.get("title", ""), info.get("game_name", ""),
                     info.get("broadcaster_language", ""))
    ch["followers"] = follower_count(req, params, user["id"])
    ch["views"] = channel_views(user)
    src = user.get("offline_image_url") or channel_banner(user)
    if BANNER_MODE == "none":
        ch["profile_banner"] = None
    elif BANNER_MODE == "direct":
        ch["profile_banner"] = src
    else:
        ch["profile_banner"] = image_proxy_link(src, 498, 280)
    log.info("Profil %s: Banner=%s Farbe=%s", login, BANNER_MODE, ch["profile_banner_background_color"])
    return 200, ch


def image_proxy_link(url, w, h):
    """Bild ueber den Adapter verkleinert ausliefern (Offline-Bilder sind 1920x1080-PNGs mit
    mehreren MB - die laedt die Vita nicht). Ohne Pillow: Original-URL."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return url
    blob = _b64e(("%dx%d|" % (w, h) + url).encode())
    sig = _b64e(hmac.new(HLS_KEY, b"img" + blob.encode(), hashlib.sha256).digest()[:12])
    return "%s/img/r/%s/%s.jpg" % (PUBLIC_BASE, sig, blob)


def ep_image(base, params, req, sig, blob):
    good = _b64e(hmac.new(HLS_KEY, b"img" + blob.encode(), hashlib.sha256).digest()[:12])
    if not hmac.compare_digest(good, sig):
        raise ApiError(403, "Invalid image link")
    size, url = _b64d(blob).decode().split("|", 1)
    w, h = (int(x) for x in size.split("x"))
    if (urllib.parse.urlsplit(url).hostname or "") != "static-cdn.jtvnw.net":
        raise ApiError(403, "Host not allowed")
    key = ("img", blob, BANNER_DIM)
    data = cache.get(key)
    if data is None:
        from io import BytesIO
        from PIL import Image
        try:
            with upstream(url, timeout=10) as resp:
                raw = resp.read(8_000_000)
        except Exception as exc:
            raise ApiError(404, "Image unavailable: %s" % type(exc).__name__)
        im = Image.open(BytesIO(raw)).convert("RGB")
        scale = max(w / im.width, h / im.height)          # fuellen, mittig zuschneiden
        im = im.resize((max(w, round(im.width * scale)), max(h, round(im.height * scale))), Image.LANCZOS)
        left, top = (im.width - w) // 2, (im.height - h) // 2
        im = im.crop((left, top, left + w, top + h))
        if BANNER_DIM < 1.0:
            from PIL import ImageEnhance
            im = ImageEnhance.Brightness(im).enhance(BANNER_DIM)
        out = BytesIO()
        im.save(out, "JPEG", quality=82, progressive=False, optimize=True)
        data = out.getvalue()
        cache.set(key, data, 3600)
        log.info("Bild verkleinert: %d kB -> %d kB (%dx%d)", len(raw) // 1024, len(data) // 1024, w, h)
    return 200, data


def channel_banner(user):
    """Banner fuer die Profilkarte: Live-Vorschau, sonst letztes Video, sonst Twitch-Standardbild."""
    for s_ in (cache.get(("streams", ())) or {}).get("items", []):
        if s_.get("user_id") == user["id"]:
            return art(s_.get("thumbnail_url"), 498, 280)
    items = cache.get(("videos", user["id"], "archive")) or cache.get(("videos", user["id"], "highlight"))
    if items is None:
        try:
            items = helix("videos", [("user_id", user["id"]), ("first", 1)]).get("data", [])
        except ApiError:
            items = []
    for v in items:
        tpl = (v.get("thumbnail_url") or "").replace("%{width}", "{width}").replace("%{height}", "{height}")
        if "{width}" in tpl:
            return art(tpl, 498, 280)
    return DEFAULT_BANNER


_follower_warned = [0.0]


def follower_count(req, params, broadcaster_id):
    """Gesamtzahl Follower (Helix liefert sie nur mit Nutzer-Token) - 10 min gecacht."""
    hit = cache.get(("followers", str(broadcaster_id)))
    if hit is not None:
        return hit
    try:
        sess = session_from(req, params) if req is not None else None
    except ApiError:
        sess = None
    sess = sess or any_session()
    if not sess:
        return 0
    try:
        total = int(helix_as(sess, "channels/followers", {"broadcaster_id": broadcaster_id, "first": 1}).get("total") or 0)
    except ApiError as exc:
        if time.time() - _follower_warned[0] > 300:
            _follower_warned[0] = time.time()
            log.warning("Follower-Zahl nicht abrufbar: %s", exc.message)
        return 0
    cache.set(("followers", str(broadcaster_id)), total, 600)
    return total


def fill_followers(channels, req=None, params=None):
    """Follower-Zahlen fuer mehrere Kanal-Objekte parallel nachtragen (Listen, Suche)."""
    from concurrent.futures import ThreadPoolExecutor
    todo = [c for c in channels if c.get("_id")]
    if not todo:
        return channels
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c, n in zip(todo, ex.map(lambda c: follower_count(None, None, c["_id"]), todo)):
            c["followers"] = n
    return channels


def channel_views(user):
    """Twitch hat Kanal-Aufrufe abgeschafft. Ersatz: Summe der Aufrufe aller Videos (VODs + Highlights)."""
    stale = int(user.get("view_count") or 0)
    if stale:
        return stale
    key = ("views", user["id"])
    hit = cache.get(key)
    if hit is not None:
        return hit
    total = 0
    for vtype in ("archive", "highlight"):
        items = cache.get(("videos", user["id"], vtype))
        if items is None:
            try:
                items = helix("videos", [("user_id", user["id"]), ("type", vtype), ("first", 100)]).get("data", [])
            except ApiError:
                items = []
        total += sum(int(v.get("view_count") or 0) for v in items)
    cache.set(key, total, 600)
    return total


def parse_duration(d):
    """Helix-Dauer '3h8m33s' -> Sekunden."""
    total = 0
    for num, unit in re.findall(r"(\d+)([hms])", d or ""):
        total += int(num) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def video_obj(base, v, game=""):
    tpl = (v.get("thumbnail_url") or "").replace("%{width}", "{width}").replace("%{height}", "{height}")
    if "{width}" not in tpl:
        tpl = ""  # laufende Aufzeichnung ohne Vorschaubild -> App zeigt Platzhalter
    login = v.get("user_login", "")
    return {
        "_id": "v" + str(v.get("id", "")),
        "title": v.get("title", ""),
        "description": v.get("description") or None,
        "status": "recorded",
        "broadcast_type": "highlight" if v.get("type") == "highlight" else "archive",
        "broadcast_id": num_id(v.get("stream_id")),
        "tag_list": "",
        "game": game or None,
        "length": parse_duration(v.get("duration")),
        "views": int(v.get("view_count") or 0),
        "created_at": v.get("created_at", ""),
        "recorded_at": v.get("created_at", ""),
        "published_at": v.get("published_at", ""),
        "url": v.get("url", ""),
        "paywalled": v.get("viewable") == "private",
        "preview": {"small": art(tpl, 80, 45), "medium": art(tpl, 320, 180),
                    "large": art(tpl, 640, 360), "template": tpl},
        "thumbnails": [],
        "muted_segments": v.get("muted_segments") or None,
        "channel": {"name": login, "display_name": v.get("user_name") or login},
        "_links": {"self": base + "/kraken/videos/v" + str(v.get("id", "")),
                   "channel": base + "/kraken/channels/" + login},
    }


def ep_channel_videos(base, params, req, login):
    """Vergangene Uebertragungen (broadcasts=true/broadcast_type=archive) bzw. Highlights."""
    limit = as_int(first(params, "limit", default="10"), 10, 1, 100)
    offset = as_int(first(params, "offset", default="0"), 0, 0, 300)
    archive = first(params, "broadcasts") == "true" or "archive" in first(params, "broadcast_type")
    vtype = "archive" if archive else (first(params, "broadcast_type") or "highlight").split(",")[0]
    if vtype not in ("archive", "highlight", "upload"):
        vtype = "highlight"
    user = user_by_login(login)
    if not user:
        raise ApiError(404, "Channel '%s' does not exist" % login)
    key = ("videos", user["id"], vtype)
    items = cache.get(key)
    if items is None:
        items, cursor = [], None
        while len(items) < min(offset + limit, 300):
            q = [("user_id", user["id"]), ("type", vtype), ("first", 100)] + ([("after", cursor)] if cursor else [])
            res = helix("videos", q)
            items.extend(res.get("data", []))
            cursor = (res.get("pagination") or {}).get("cursor")
            if not cursor or not res.get("data"):
                break
        cache.set(key, items, 300)
    page = items[offset:offset + limit]
    return 200, {"_total": len(items),
                 "_links": page_links(base, "/kraken/channels/%s/videos" % login, params, offset, limit),
                 "videos": [video_obj(base, v) for v in page]}


def ep_video(base, params, req, vid):
    data = helix("videos", {"id": vid}).get("data", [])
    if not data:
        raise ApiError(404, "Video v%s does not exist" % vid)
    return 200, video_obj(base, data[0])


def ep_vod_token(base, params, req, vid):
    exp = int(time.time()) + 1200
    who, _src = viewer_login(req, params)
    tok = {"vod_id": int(vid), "expires": exp, "adapter": "vita-twitch"}
    if who:
        tok["user"] = who
    token = json.dumps(tok, separators=(",", ":"))
    return 200, {"token": token, "sig": sign_token(token), "mobile_restricted": False}


def ep_vod_hls(base, params, req, vid):
    who, src = viewer_login(req, params)
    web = web_token_for(who)
    key = ("vodhls", vid, hashlib.sha256(web.encode()).hexdigest()[:12] if web else "anon")
    playlist = cache.get(key)
    if playlist is None:
        log.info("VOD %s fuer %s (%s): %s", vid, who or "nicht angemeldet", src or "-",
                 "mit Konto-Vorteilen" if web else "anonym")
        info = ytdlp_extract("https://www.twitch.tv/videos/" + vid, "Video not available: ", web=web)
        playlist = build_master(info, "http://" + req._host())
        cache.set(key, playlist, 300)
    return 200, playlist


def ep_user(base, params, req, login):
    user = user_by_login(login)
    if not user:
        raise ApiError(404, "User '%s' does not exist" % login)
    return 200, {
        "display_name": user.get("display_name") or user["login"],
        "_id": num_id(user["id"]),
        "name": user["login"],
        "type": "user",
        "bio": user.get("description") or None,
        "created_at": user.get("created_at", ""),
        "updated_at": user.get("created_at", ""),
        "logo": user.get("profile_image_url") or None,
        "_links": {"self": base + "/kraken/users/" + user["login"]},
    }


def ep_top_games(base, params, req):
    limit = cap(as_int(first(params, "limit", default="10"), 10, 1, 100))
    offset = as_int(first(params, "offset", default="0"), 0, 0, 900)
    st = top_games_list(offset + limit)
    viewers, channels, _ = game_sample()
    top = []
    for g in st["items"][offset:offset + limit]:
        gid = str(g.get("id", ""))
        top.append({
            "game": game_obj(gid, g.get("name", ""), g.get("box_art_url", ""), viewers[gid]),
            "viewers": viewers[gid],
            "channels": channels[gid],
        })
    total = len(st["items"]) if st["done"] else len(st["items"]) + limit
    return 200, {"_total": total,
                 "_links": page_links(base, "/kraken/games/top", params, offset, limit),
                 "top": top}


def ep_games_by_id(base, params, req):
    ids = []
    for key in ("ids", "id"):
        for value in params.get(key, []):
            ids.extend(x for x in value.split(",") if x)
    ids = ids[:100]
    games = []
    if ids:
        for g in helix("games", [("id", x) for x in ids]).get("data", []):
            games.append(game_obj(g.get("id"), g.get("name", ""), g.get("box_art_url", "")))
    return 200, {"_total": len(games), "_links": {}, "games": games}


def search_query(params):
    q = first(params, "q", "query").strip()
    if not q:
        raise ApiError(400, "Missing required parameter 'query'")
    return q[:100]


def ep_search_streams(base, params, req):
    q = search_query(params)
    limit = as_int(first(params, "limit", default="25"), 25, 1, 100)
    offset = as_int(first(params, "offset", default="0"), 0, 0, 100)
    res = helix("search/channels", {"query": q, "live_only": "true",
                                    "first": min(100, offset + limit)})
    ids = [c["id"] for c in res.get("data", []) if c.get("is_live")]
    streams = []
    for part in chunks(ids, 100):
        streams.extend(helix("streams", [("user_id", x) for x in part] + [("first", 100)])
                       .get("data", []))
    streams.sort(key=lambda s: int(s.get("viewer_count") or 0), reverse=True)
    page = streams[offset:offset + limit]
    return 200, {"_total": len(streams),
                 "_links": page_links(base, "/kraken/search/streams", params, offset, limit),
                 "streams": streams_to_kraken(base, page)}


def ep_search_channels(base, params, req):
    q = search_query(params)
    limit = as_int(first(params, "limit", default="25"), 25, 1, 100)
    offset = as_int(first(params, "offset", default="0"), 0, 0, 100)
    res = helix("search/channels", {"query": q, "first": min(100, offset + limit)})
    found = res.get("data", [])[offset:offset + limit]
    users = users_by_ids([c["id"] for c in found])
    chans = []
    for c in found:
        u = users.get(c["id"]) or {"id": c["id"], "login": c.get("broadcaster_login", ""),
                                    "display_name": c.get("display_name", "")}
        chans.append(channel_obj(base, u, c.get("title", ""), c.get("game_name", ""),
                                 c.get("broadcaster_language", "")))
    return 200, {"_total": len(res.get("data", [])),
                 "_links": page_links(base, "/kraken/search/channels", params, offset, limit),
                 "channels": fill_followers(chans)}


def ep_search_games(base, params, req):
    q = search_query(params)
    res = helix("search/categories", {"query": q, "first": 25})
    viewers, _, _ = game_sample()
    games = [game_obj(g.get("id"), g.get("name", ""), g.get("box_art_url", ""),
                      viewers[str(g.get("id"))]) for g in res.get("data", [])]
    if first(params, "live") == "true":
        games = [g for g in games if g["popularity"] > 0] or games
    return 200, {"_links": {"self": base + "/kraken/search/games?" +
                            urllib.parse.urlencode({"q": q, "type": "suggest"})},
                 "games": games}


# ---------------------------- Wiedergabe ----------------------------------
def sign_token(token):
    return hmac.new(HLS_KEY, token.encode(), hashlib.sha256).hexdigest()[:40]


def viewer_login(req, params):
    """Welches Twitch-Konto schaut? -> (login, Quelle) oder (None, None).
    1) Sitzung an der Anfrage  2) signiertes Wiedergabe-Token  3) letzte Sitzung von derselben IP."""
    try:
        sess = session_from(req, params)
    except ApiError:
        sess = None
    if sess:
        return sess.get("login"), "Sitzung"
    tok, sig = first(params, "token"), first(params, "sig")
    if tok and sig and hmac.compare_digest(sign_token(tok), sig):
        try:
            t = json.loads(tok)
            if t.get("user") and t.get("expires", 0) > time.time():
                return t["user"], "Token"
        except ValueError:
            pass
    ip, best = req._client_ip(), None
    with _sess_lock:
        for v in _sessions.values():
            if v.get("ip") == ip and v.get("seen", 0) > time.time() - IP_MATCH_HOURS * 3600:
                if not best or v["seen"] > best["seen"]:
                    best = v
    if best:
        return best.get("login"), "IP"
    return None, None


# --------------------------------------------------------------------------
# Eigene Web-Tokens der Nutzer (freiwillig ueber /link hinterlegt)
# Twitch gibt werbefreie Wiedergabe (Turbo/Abo) nur an Tokens der offiziellen Website aus.
# Jeder kann deshalb sein eigenes auth-token hinterlegen; es wird NUR fuer genau dieses Konto benutzt.
# --------------------------------------------------------------------------
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"      # oeffentliche Client-ID der Twitch-Website
TOKENS_FILE = os.path.join(STATE_DIR, "webtokens.json")
_webtok = {}            # login -> {"user_id", "token", "turbo", "checked", "added"}
_webtok_lock = threading.Lock()
_rate = {}              # (Bereich, IP) -> [Zeitstempel]
_rate_lock = threading.Lock()


def rate_ok(area, ip, limit, window):
    """Einfaches Limit: hoechstens `limit` Aufrufe je `window` Sekunden und IP."""
    now = time.time()
    with _rate_lock:
        if len(_rate) > 5000:   # aufraeumen, damit der Speicher nicht waechst
            for k in [k for k, v in _rate.items() if not v or v[-1] < now - 3600]:
                _rate.pop(k, None)
        hits = [t for t in _rate.get((area, ip), []) if t > now - window]
        if len(hits) >= limit:
            _rate[(area, ip)] = hits
            return False
        _rate[(area, ip)] = hits + [now]
        return True
REVALIDATE = 3600       # Sekunden zwischen zwei Pruefungen eines Tokens


def _cipher():
    """AES-GCM mit eigenem Schluessel in STATE_DIR (falls python3-cryptography vorhanden)."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return None
    path = os.path.join(STATE_DIR, "webtokens.key")
    try:
        with open(path, "rb") as fh:
            key = fh.read()
    except OSError:
        key = AESGCM.generate_key(bit_length=256)
        os.makedirs(STATE_DIR, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
    return AESGCM(key)


def _load_webtokens():
    try:
        with open(TOKENS_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as exc:
        log.error("webtokens.json nicht lesbar: %s", exc)
        return
    aes = _cipher()
    for login, v in raw.items():
        try:
            if "enc" in v:
                if not aes:
                    log.error("Gespeicherte Web-Tokens sind verschluesselt, python3-cryptography fehlt")
                    return
                blob = base64.b64decode(v.pop("enc"))
                v["token"] = aes.decrypt(blob[:12], blob[12:], login.encode()).decode()
            _webtok[login] = v
        except Exception as exc:
            log.error("Web-Token von %s nicht lesbar: %s", login, type(exc).__name__)
    log.info("%d eigene Web-Tokens geladen%s", len(_webtok), "" if aes else " (UNVERSCHLUESSELT gespeichert)")


def _save_webtokens():
    aes = _cipher()
    out = {}
    for login, v in _webtok.items():
        v = dict(v)
        if aes:
            nonce = secrets.token_bytes(12)
            v["enc"] = base64.b64encode(nonce + aes.encrypt(nonce, v.pop("token").encode(), login.encode())).decode()
        out[login] = v
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = TOKENS_FILE + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(out, fh)
        os.replace(tmp, TOKENS_FILE)
    except Exception as exc:
        log.error("Web-Tokens nicht gespeichert: %s", exc)


def check_web_token(token):
    """Prueft ein auth-token bei Twitch -> {"login", "user_id", "turbo"}; ApiError wenn ungueltig."""
    if not re.fullmatch(r"[a-z0-9]{20,40}", token or ""):
        raise ApiError(400, "Das sieht nicht wie ein auth-token aus (30 Zeichen, a-z und 0-9).")
    try:
        v = http_json("https://id.twitch.tv/oauth2/validate", headers={"Authorization": "OAuth " + token})
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ApiError(401, "Twitch kennt dieses Token nicht (abgemeldet oder abgelaufen).")
        raise ApiError(502, "Twitch-Pruefung fehlgeschlagen (HTTP %d)" % exc.code)
    if v.get("client_id") != WEB_CLIENT_ID:
        log.warning("/link: Token mit fremder Client-ID %s abgelehnt", (v.get("client_id") or "?")[:6] + "...")
        raise ApiError(400, "Das ist kein Token der Twitch-Website (auth-token-Cookie verwenden).")
    turbo = False
    try:
        req = urllib.request.Request("https://gql.twitch.tv/gql", method="POST",
                                     data=json.dumps({"query": "query{currentUser{hasTurbo}}"}).encode(),
                                     headers={"Client-Id": WEB_CLIENT_ID, "Authorization": "OAuth " + token,
                                              "Content-Type": "application/json",
                                              "User-Agent": "Mozilla/5.0 VitaTwitchAdapter/" + VERSION})
        with urllib.request.urlopen(req, timeout=10) as resp:
            turbo = bool(((json.loads(resp.read()).get("data") or {}).get("currentUser") or {}).get("hasTurbo"))
    except Exception as exc:
        log.warning("Turbo-Abfrage fehlgeschlagen: %s", type(exc).__name__)
    return {"login": (v.get("login") or "").lower(), "user_id": v.get("user_id", ""), "turbo": turbo}


def user_web_token(login):
    """Eigenes Web-Token dieses Kontos (stuendlich bei Twitch geprueft) oder None."""
    if not login:
        return None
    login = login.lower()
    with _webtok_lock:
        ent = _webtok.get(login)
    if not ent:
        return None
    if ent.get("checked", 0) < time.time() - REVALIDATE:
        try:
            info = check_web_token(ent["token"])
        except ApiError as exc:
            if exc.status in (400, 401):
                log.warning("Web-Token von %s ungueltig geworden - entfernt", login)
                with _webtok_lock:
                    _webtok.pop(login, None)
                    _save_webtokens()
                return None
            return ent["token"]          # Twitch gerade nicht erreichbar -> weiter benutzen
        with _webtok_lock:
            ent.update(checked=time.time(), turbo=info["turbo"])
            _save_webtokens()
    return ent["token"]


def web_token_for(login):
    """Welches Web-Token gilt fuer dieses Konto? Eigenes > Betreiber-Token (nur Besitzer) > keins."""
    own = user_web_token(login)
    if own:
        return own
    if WEB_AUTH_TOKEN and (WEB_SHARE or bool(login and WEB_OWNER and login.lower() == WEB_OWNER)):
        return WEB_AUTH_TOKEN
    return None


def has_turbo(login):
    if not login:
        return False
    with _webtok_lock:
        ent = _webtok.get(login.lower())
    if ent:
        return bool(ent.get("turbo"))
    return TURBO and bool(WEB_AUTH_TOKEN) and bool(WEB_OWNER) and login.lower() == WEB_OWNER


def link_action(req, form):
    """POST /link: action=add|remove, auth_token=... -> (status, dict)."""
    now = time.time()
    if not rate_ok("link", req._client_ip(), 10, 600):
        return 429, {"ok": False, "error": "Zu viele Versuche. Bitte in ein paar Minuten noch einmal."}
    token = (form.get("auth_token") or "").strip().strip('"').lower()
    if token.startswith("auth-token="):
        token = token.split("=", 1)[1]
    try:
        info = check_web_token(token)
    except ApiError as exc:
        return exc.status, {"ok": False, "error": exc.message}
    login = info["login"]
    with _webtok_lock:
        if form.get("action") == "remove":
            gone = _webtok.pop(login, None) is not None
            _save_webtokens()
            log.info("Web-Token von %s entfernt", login)
            return 200, {"ok": True, "login": login, "removed": gone}
        _webtok[login] = {"user_id": info["user_id"], "token": token, "turbo": info["turbo"],
                          "checked": now, "added": _webtok.get(login, {}).get("added", now)}
        _save_webtokens()
    log.info("Web-Token von %s hinterlegt (Turbo: %s)", login, "ja" if info["turbo"] else "nein")
    return 200, {"ok": True, "login": login, "turbo": info["turbo"]}


def ep_access_token(base, params, req, login):
    user = user_by_login(login)
    if not user:
        raise ApiError(404, "Channel '%s' does not exist" % login)
    exp = int(time.time()) + 1200
    who, _src = viewer_login(req, params)
    tok = {"channel": user["login"], "expires": exp, "adapter": "vita-twitch"}
    if who:
        tok["user"] = who
    token = json.dumps(tok, separators=(",", ":"))
    return 200, {"token": token, "sig": sign_token(token), "mobile_restricted": False,
                 "expires_at": datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def write_cookie_file(directory, token):
    """Netscape-Cookiedatei mit dem auth-token fuer *.twitch.tv (nur fuer diesen Aufruf)."""
    path = os.path.join(directory, "cookies.txt")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("# Netscape HTTP Cookie File\n")
        fh.write(".twitch.tv\tTRUE\t/\tTRUE\t%d\tauth-token\t%s\n" % (int(time.time()) + 86400 * 365, token))
    return path


def run_ytdlp(login, web=None):
    return ytdlp_extract("https://www.twitch.tv/" + login, "Stream not available: ", login, web)


def ytdlp_extract(url, errprefix, login_for_fallback=None, web=None):
    """web: auth-token, mit dem abgespielt wird (None = anonym)."""
    """yt-dlp direkt im Prozess (spart ~2 s Start pro Stream); Fallback: Unterprozess."""
    try:
        import yt_dlp
    except ImportError:
        if login_for_fallback:
            return run_ytdlp_subprocess(login_for_fallback, web)
        raise ApiError(503, "yt-dlp not installed")
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="vita-yt-")
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "cachedir": False,
            "skip_download": True, "socket_timeout": 15, "logger": _YtLog()}
    if web:
        opts["cookiefile"] = write_cookie_file(tmpdir, web)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc).replace("ERROR: ", "").strip().splitlines()[-1][:200]
        raise ApiError(404, errprefix + msg)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


class _YtLog:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        log.info("yt-dlp: %s", str(msg)[:200])


def run_ytdlp_subprocess(login, web=None):
    """Liefert die yt-dlp-Formatliste eines Live-Kanals (enthaelt die Variant-Playlists)."""
    cmd = [sys.executable, "-m", "yt_dlp", "-J", "--no-warnings", "--no-cache-dir",
           "--no-playlist", "https://www.twitch.tv/" + login]
    env = dict(os.environ, HOME="/tmp")
    for k in ("TWITCH_CLIENT_SECRET", "TWITCH_WEB_AUTH_TOKEN"):
        env.pop(k, None)  # Secrets nicht ueber die Umgebung an den Kindprozess
    tmpdir = None
    try:
        if web:
            import tempfile
            tmpdir = tempfile.mkdtemp(prefix="vita-yt-")
            cmd[3:3] = ["--cookies", write_cookie_file(tmpdir, web)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()[-1:] or ["unbekannt"]
        raise ApiError(404, "Stream not available: " + err[0][:200])
    return json.loads(p.stdout)


def build_master(info, base_http=None):
    fmts = []
    for f in info.get("formats", []):
        vc = (f.get("vcodec") or "")
        if not f.get("url") or vc == "none" or not vc.startswith("avc1") or not f.get("height"):
            continue
        fmts.append(f)
    if not fmts:
        raise ApiError(404, "No H.264 variant available")
    ok = [f for f in fmts if f["height"] <= MAX_HEIGHT]
    if not ok:  # nur Source > MAX_HEIGHT -> trotzdem die kleinste anbieten
        ok = [min(fmts, key=lambda f: f["height"])]
        log.warning("Nur Varianten > %sp verfuegbar, liefere %sp", MAX_HEIGHT, ok[0]["height"])
    ok.sort(key=lambda f: (abs(f["height"] - PREF_HEIGHT), -(f.get("fps") or 0)))
    lines = ["#EXTM3U"]
    for f in ok:
        bw = int((f.get("tbr") or 1500) * 1000)
        attrs = ["PROGRAM-ID=1", "BANDWIDTH=%d" % bw]
        if f.get("width"):
            attrs.append("RESOLUTION=%dx%d" % (f["width"], f["height"]))
        ac = f.get("acodec") or "mp4a.40.2"
        attrs.append('CODECS="%s,%s"' % (f["vcodec"], ac if ac != "none" else "mp4a.40.2"))
        name = f.get("format_id") or "%dp" % f["height"]
        lines.append('#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="%s",NAME="%s",AUTOSELECT=YES,DEFAULT=YES'
                     % (name, name))
        lines.append("#EXT-X-STREAM-INF:" + ",".join(attrs) + ',VIDEO="%s"' % name)
        lines.append(proxy_link(base_http, f["url"], "m3u8") if (HLS_PROXY and base_http) else f["url"])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# HLS-Proxy: Playlists und Segmente ueber den Adapter, signiert, nur Twitch-Hosts
# --------------------------------------------------------------------------
def _b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def proxy_link(base_http, url, kind):
    blob = _b64e(url.encode())
    sig = _b64e(hmac.new(HLS_KEY, blob.encode(), hashlib.sha256).digest()[:12])
    return "%s/hls/%s/%s.%s" % (base_http, sig, blob, "m3u8" if kind == "m3u8" else "ts")


def proxy_target(sig, blob):
    good = _b64e(hmac.new(HLS_KEY, blob.encode(), hashlib.sha256).digest()[:12])
    if not hmac.compare_digest(good, sig):
        raise ApiError(403, "Invalid proxy link")
    url = _b64d(blob).decode()
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not url.startswith(("https://", "http://")) or not any(
            host == h or host.endswith("." + h) for h in HLS_HOSTS):
        raise ApiError(403, "Host not allowed")
    return url


_last_ad_log = [0.0]


def rewrite_playlist(text, src_url, base_http):
    if "stitched-ad" in text or "Amazon|" in text:
        if time.time() - _last_ad_log[0] > 60:
            _last_ad_log[0] = time.time()
            log.info("Werbung im Stream (anonyme Wiedergabe oder Konto ohne Turbo/Abo)")
    out = []
    for line in text.splitlines():
        t = line.strip()
        if t and not t.startswith("#"):
            absu = urllib.parse.urljoin(src_url, t)
            kind = "m3u8" if ".m3u8" in urllib.parse.urlsplit(absu).path else "ts"
            out.append(proxy_link(base_http, absu, kind))
        elif t.startswith("#EXT-X-TWITCH-PREFETCH"):
            continue  # Low-Latency-Erweiterung, fuer die Vita nutzlos
        elif t.startswith("#EXT-X-MAP:") and 'URI="' in t:
            m = re.search(r'URI="([^"]+)"', t)
            absu = urllib.parse.urljoin(src_url, m.group(1))
            out.append(t.replace(m.group(1), proxy_link(base_http, absu, "ts")))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def upstream(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VitaTwitchAdapter/" + VERSION})
    return urllib.request.urlopen(req, timeout=timeout)


def ep_hls(base, params, req, login):
    # usher-Stil: /api/channel/hls/<login>.m3u8 (Host egal)
    login = login.lower()
    if not re.fullmatch(r"[a-z0-9_]{1,25}", login):
        raise ApiError(404, "Channel not found")
    who, src = viewer_login(req, params)
    web = web_token_for(who)
    key = ("hls", login, hashlib.sha256(web.encode()).hexdigest()[:12] if web else "anon")
    playlist = cache.get(key)
    if playlist is None:
        log.info("Stream %s fuer %s (%s): %s", login, who or "nicht angemeldet", src or "-",
                 "mit Konto-Vorteilen (Turbo/Abo)" if web else "anonym - Werbung nach Twitch-Regeln")
        info = run_ytdlp(login, web)
        playlist = build_master(info, "http://" + req._host())
        cache.set(key, playlist, 20)
    return 200, playlist


# --------------------------------------------------------------------------
# Login (alter PSN-/Aktivierungscode-Ablauf -> Twitch Device Code Flow)
# Die App bekommt einen eigenen Sitzungsschluessel; Twitch-Tokens bleiben auf dem Server.
# --------------------------------------------------------------------------
_pending = {}           # PSN-Token -> {"device_code", "user_code", "uri", "expires", "session"}
_short = {}             # 6-stelliger Vita-Code -> (Aktivierungs-URL, Ablaufzeit)
SHORT_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # ohne 0/O und 1/I
ACTIVATE_URL = os.environ.get("VITA_ACTIVATE_URL", PUBLIC_HOST)


def new_short_code():
    while True:
        c = "".join(secrets.choice(SHORT_ALPHABET) for _ in range(6))
        if c not in _short:
            return c
_sessions = {}          # Sitzungsschluessel -> {"access", "refresh", "expires", "user_id", "login"}
_sess_lock = threading.Lock()
SESS_FILE = os.path.join(STATE_DIR, "sessions.json")


def _load_hls_key():
    """Signaturschluessel fuer HLS-Proxy-Links dauerhaft speichern. Sonst werden nach jedem
    Neustart des Adapters die Links laufender Streams ungueltig (403) und der Player bleibt haengen."""
    global HLS_KEY
    path = os.path.join(STATE_DIR, "hls.key")
    try:
        with open(path, "rb") as fh:
            key = fh.read()
        if len(key) == 32:
            HLS_KEY = key
            return
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("HLS-Schluessel nicht lesbar: %s", exc)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(HLS_KEY)
        log.info("Neuer HLS-Schluessel angelegt")
    except Exception as exc:
        log.warning("HLS-Schluessel nicht gespeichert (Links gelten nur bis zum Neustart): %s", exc)


def _load_sessions():
    try:
        with open(SESS_FILE, encoding="utf-8") as fh:
            _sessions.update(json.load(fh))
        log.info("%d Login-Sitzung(en) geladen", len(_sessions))
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.error("Sitzungsdatei unlesbar: %s", exc)


def _save_sessions():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = SESS_FILE + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_sessions, fh)
        os.replace(tmp, SESS_FILE)
    except Exception as exc:
        log.error("Sitzungen nicht gespeichert: %s", exc)


def id_post(path, data):
    """POST auf id.twitch.tv; gibt (status, json) zurueck, ohne Secrets zu loggen."""
    try:
        return 200, http_json("https://id.twitch.tv/oauth2/" + path, method="POST", data=data)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read(500).decode("utf-8", "replace"))
        except Exception:
            body = {}
        return exc.code, body
    except Exception as exc:
        log.error("id.twitch.tv/%s nicht erreichbar: %s", path, type(exc).__name__)
        raise ApiError(503, "Twitch login service unreachable")


def ep_auth_generate(base, params, req):
    psn = first(params, "token")
    if not psn:
        raise ApiError(400, "missing token")
    if not rate_ok("generate", req._client_ip(), 20, 600):
        raise ApiError(429, "too many login attempts, try again later")
    with _sess_lock:
        p = _pending.get(psn)
        if p and p.get("session"):
            return 400, {"error": "Bad Request", "status": 400, "message": "already authenticated"}
        if p and p["expires"] > time.time() + 30:
            return 200, {"code": p["short"]}
    st, res = id_post("device", {"client_id": CLIENT_ID, "scopes": LOGIN_SCOPES})
    if st != 200 or "device_code" not in res:
        log.error("Device-Code-Start fehlgeschlagen: HTTP %s %s", st, res.get("message", ""))
        raise ApiError(503, "Twitch login unavailable")
    with _sess_lock:
        if len(_pending) > 200:
            _pending.clear()
        exp = time.time() + int(res.get("expires_in", 1800))
        uri = res.get("verification_uri") or "https://www.twitch.tv/activate"
        if "device-code=" not in uri:
            uri += ("&" if "?" in uri else "?") + "device-code=" + urllib.parse.quote(res["user_code"])
        short = new_short_code()
        _pending[psn] = {"device_code": res["device_code"], "user_code": res["user_code"],
                         "uri": uri, "expires": exp, "session": None, "short": short}
        for k in [k for k, v in _short.items() if v[1] < time.time()]:
            _short.pop(k, None)
        _short[short] = (uri, exp)
    log.info("Login gestartet: Vita-Code %s -> https://%s", short, ACTIVATE_URL)
    return 200, {"code": short}


def ep_auth_authenticate(base, params, req):
    psn = first(params, "token")
    with _sess_lock:
        p = _pending.get(psn)
    if not p or p["expires"] < time.time():
        return 400, {"error": "Bad Request", "status": 400, "message": "unknown or expired token"}
    if p.get("session"):
        return 200, {"oauth_token": p["session"]}
    st, res = id_post("token", {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                                "scopes": LOGIN_SCOPES, "device_code": p["device_code"],
                                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    if st != 200 or "access_token" not in res:
        msg = str(res.get("message", ""))
        if "pending" in msg or "slow_down" in msg:
            return 404, {"error": "Not Found", "status": 404, "message": "authorization_pending"}
        log.warning("Login abgebrochen: HTTP %s %s", st, msg)
        with _sess_lock:
            _pending.pop(psn, None)
        return 400, {"error": "Bad Request", "status": 400, "message": msg or "login failed"}
    # Wer hat sich angemeldet?
    me = http_json(HELIX + "users", headers={"Client-Id": CLIENT_ID,
                                             "Authorization": "Bearer " + res["access_token"]}).get("data", [{}])[0]
    sid = secrets.token_urlsafe(24)
    with _sess_lock:
        _sessions[sid] = {"access": res["access_token"], "refresh": res.get("refresh_token", ""),
                          "scopes": res.get("scope") or [],
                          "expires": time.time() + int(res.get("expires_in", 14400)),
                          "user_id": me.get("id", ""), "login": me.get("login", "")}
        p["session"] = sid
        _save_sessions()
    log.info("Login erfolgreich: %s", me.get("login", "?"))
    return 200, {"oauth_token": sid}


def session_from(req, params):
    """Sitzung aus 'Authorization: OAuth <sid>' oder ?oauth_token=; None wenn nicht eingeloggt."""
    auth = req.headers.get("Authorization", "")
    sid = auth.split(None, 1)[1].strip() if auth.lower().startswith(("oauth ", "bearer ")) else ""
    sid = sid or first(params, "oauth_token")
    if not sid:
        return None
    sess = fresh_session(sid)
    sess["ip"], sess["seen"] = req._client_ip(), time.time()   # nur im Speicher, fuer viewer_login()
    return sess


def any_session():
    """Irgendeine gueltige Login-Sitzung (fuer Daten, die Helix nur mit Nutzer-Token liefert)."""
    with _sess_lock:
        sids = list(_sessions)
    for sid in sids:
        try:
            return fresh_session(sid)
        except ApiError:
            continue
    return None


def fresh_session(sid):
    with _sess_lock:
        sess = _sessions.get(sid)
    if not sess:
        raise ApiError(401, "invalid oauth token")
    if sess["expires"] < time.time() + 300:
        st, res = id_post("token", {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                                    "grant_type": "refresh_token", "refresh_token": sess["refresh"]})
        if st != 200 or "access_token" not in res:
            log.warning("Token-Erneuerung fuer %s fehlgeschlagen: %s", sess.get("login"), res.get("message"))
            with _sess_lock:
                _sessions.pop(sid, None)
                _save_sessions()
            raise ApiError(401, "session expired, please log in again")
        with _sess_lock:
            sess.update(access=res["access_token"], refresh=res.get("refresh_token", sess["refresh"]),
                        expires=time.time() + int(res.get("expires_in", 14400)))
            _save_sessions()
        log.info("Twitch-Token fuer %s erneuert", sess.get("login"))
    return sess


def require_session(req, params):
    sess = session_from(req, params)
    if not sess:
        raise ApiError(401, "login required")
    return sess


def helix_as(sess, path, params=None):
    if isinstance(params, dict):
        params = list(params.items())
    url = HELIX + path + ("?" + urllib.parse.urlencode(params or [], doseq=True) if params else "")
    try:
        return http_json(url, headers={"Client-Id": CLIENT_ID, "Authorization": "Bearer " + sess["access"]})
    except urllib.error.HTTPError as exc:
        log.error("Helix (Nutzer) HTTP %s auf %s", exc.code, path)
        raise ApiError(401 if exc.code == 401 else 502, "Twitch API returned HTTP %d" % exc.code)
    except Exception as exc:
        raise ApiError(502, "Twitch API unreachable") from exc


def ep_me(base, params, req):
    sess = require_session(req, params)
    u = helix_as(sess, "users").get("data", [{}])[0]
    return 200, {
        "_id": num_id(u.get("id")), "name": u.get("login", ""), "display_name": u.get("display_name", ""),
        "type": "user", "bio": u.get("description") or None, "logo": u.get("profile_image_url") or None,
        "email": u.get("email") or None, "email_verified": bool(u.get("email")),
        "partnered": u.get("broadcaster_type") == "partner", "twitter_connected": False,
        "created_at": u.get("created_at", ""), "updated_at": u.get("created_at", ""),
        "notifications": {"push": False, "email": False},
        "_links": {"self": base + "/kraken/users/" + u.get("login", "")},
    }


def ep_followed_streams(base, params, req):
    sess = require_session(req, params)
    limit = as_int(first(params, "limit", default="25"), 25, 1, 100)
    offset = as_int(first(params, "offset", default="0"), 0, 0, 900)
    items, cursor = [], None
    while len(items) < offset + limit:
        q = [("user_id", sess["user_id"]), ("first", 100)] + ([("after", cursor)] if cursor else [])
        res = helix_as(sess, "streams/followed", q)
        items.extend(res.get("data", []))
        cursor = (res.get("pagination") or {}).get("cursor")
        if not cursor or not res.get("data"):
            break
    page = items[offset:offset + limit]
    return 200, {"_total": len(items), "_links": page_links(base, "/kraken/streams/followed", params, offset, limit),
                 "streams": streams_to_kraken(base, page)}


def ep_followed_channels(base, params, req, login):
    sess = require_session(req, params)
    limit = as_int(first(params, "limit", default="25"), 25, 1, 100)
    offset = as_int(first(params, "offset", default="0"), 0, 0, 900)
    items, cursor, total = [], None, 0
    while len(items) < offset + limit:
        q = [("user_id", sess["user_id"]), ("first", 100)] + ([("after", cursor)] if cursor else [])
        res = helix_as(sess, "channels/followed", q)
        total = int(res.get("total") or 0)
        items.extend(res.get("data", []))
        cursor = (res.get("pagination") or {}).get("cursor")
        if not cursor or not res.get("data"):
            break
    page = items[offset:offset + limit]
    users = users_by_ids([f["broadcaster_id"] for f in page])
    follows = []
    for f in page:
        u = users.get(f["broadcaster_id"]) or {"id": f["broadcaster_id"], "login": f.get("broadcaster_login", ""),
                                                "display_name": f.get("broadcaster_name", "")}
        follows.append({"created_at": f.get("followed_at", ""), "notifications": False,
                        "_links": {}, "channel": channel_obj(base, u)})
    fill_followers([f["channel"] for f in follows])
    return 200, {"_total": total or len(items),
                 "_links": page_links(base, "/kraken/users/%s/follows/channels" % login, params, offset, limit),
                 "follows": follows}


def ep_follow_status(base, params, req, login, target):
    sess = require_session(req, params)
    if req.command in ("PUT", "DELETE"):
        # Twitch hat Folgen/Entfolgen per API 2023 abgeschafft.
        raise ApiError(422, "Following via API is no longer supported by Twitch")
    tu = user_by_login(target)
    if not tu:
        raise ApiError(404, "Channel '%s' does not exist" % target)
    res = helix_as(sess, "channels/followed", {"user_id": sess["user_id"], "broadcaster_id": tu["id"]})
    data = res.get("data", [])
    if not data:
        raise ApiError(404, "%s is not following %s" % (login, target))
    return 200, {"created_at": data[0].get("followed_at", ""), "notifications": False, "_links": {},
                 "channel": channel_obj(base, tu)}


def activate_page(req, params, form=None):
    """Startseite fuer Browser.
    POST /activate (Formular) mit code -> Weiterleitung zur Twitch-Freigabe, sonst Seite mit Fehler.
    GET ?code=XXXXXX fuellt nur das Feld vor - ein geteilter Link fuehrt also nie direkt zu Twitch."""
    raw = (form or {}).get("code", "") if form is not None else first(params, "code")
    code = re.sub(r"[^A-Za-z0-9]", "", raw).upper()[:8]
    err = ""
    if form is not None:
        if not rate_ok("activate", req._client_ip(), 30, 600):
            err = "Zu viele Versuche. Bitte in ein paar Minuten noch einmal."
        elif code:
            hit = _short.get(code)
            if hit and hit[1] > time.time():
                log.info("Aktivierung: Code %s -> Twitch", code)
                return 302, hit[0], ""
            err = "Der Code %s ist unbekannt oder abgelaufen. Melde dich auf der Vita neu an, dann bekommst du einen neuen." % code
            log.warning("Aktivierung: unbekannter Code %s", code)
    elif req.path.split("?")[0].rstrip("/") == "/activate":
        return 302, ("/?code=%s#aktivieren" % code) if code else "/#aktivieren", ""
    page = static_page("index.html", {"{ACT_ERR}": err, "{ACT_CODE}": code,
                                      "{ACT_ERR_CLASS}": "show" if err else ""})
    if page is None:
        raise ApiError(404, "www/index.html missing")
    return 200, None, page


def html_security_headers(page):
    """CSP mit Hash des eingebetteten Skripts + Schutz gegen Einbetten (Clickjacking)."""
    hashes = " ".join("'sha256-%s'" % base64.b64encode(hashlib.sha256(m.encode()).digest()).decode()
                      for m in re.findall(r"<script>(.*?)</script>", page, re.S))
    csp = ("default-src 'none'; script-src %s; style-src 'unsafe-inline'; img-src 'self' data:; "
           "connect-src 'self'; form-action 'self' https://www.twitch.tv https://id.twitch.tv https://passport.twitch.tv; "
           "base-uri 'none'; frame-ancestors 'none'" % (hashes or "'none'"))
    return {"Content-Security-Policy": csp, "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
            "Cross-Origin-Opener-Policy": "same-origin"}


# --------------------------------------------------------------------------
# Zaehler: wie viele Vitas sind gerade da? (nur Anzahlen, keine IPs nach aussen)
# --------------------------------------------------------------------------
ONLINE_WINDOW = int(os.environ.get("VITA_ONLINE_WINDOW", "600"))   # Sekunden: "online"
WATCH_WINDOW = 90                                                  # Sekunden: "schaut gerade"
_presence = {}          # IP -> {"seen": t, "watch": t}
_presence_lock = threading.Lock()
_peak = {"n": 0, "at": 0.0}


def track_presence(ip, ua, path):
    is_vita = "Sony-PSV" in ua            # native Twitch-App (der Vita-Browser zaehlt nicht)
    watching = path.startswith("/hls/")
    if not (is_vita or watching):
        return
    now = time.time()
    with _presence_lock:
        p = _presence.setdefault(ip, {"seen": 0.0, "watch": 0.0})
        p["seen"] = now
        if watching:
            p["watch"] = now


def presence_stats():
    now = time.time()
    with _presence_lock:
        for ip in [ip for ip, p in _presence.items() if p["seen"] < now - ONLINE_WINDOW]:
            _presence.pop(ip, None)
        online = set(_presence)
        watching = sum(1 for p in _presence.values() if p["watch"] > now - WATCH_WINDOW)
    with _sess_lock:
        logged = {v.get("ip") for v in _sessions.values() if v.get("seen", 0) > now - ONLINE_WINDOW}
    if len(online) > _peak["n"]:
        _peak.update(n=len(online), at=now)
    return {"online": len(online), "angemeldet": len(online & logged), "schauen": watching,
            "rekord": _peak["n"], "fenster_min": ONLINE_WINDOW // 60}


def static_page(name, extra=None):
    """Seite aus www/, Platzhalter {HOST} {VERSION} {REPO} {REPO_SHORT} (+ extra) ersetzt."""
    try:
        with open(os.path.join(WWW_DIR, name), encoding="utf-8") as fh:
            page = fh.read()
    except OSError:
        return None
    short = re.sub(r"^https?://", "", REPO_URL).rstrip("/")
    pairs = [("{HOST}", PUBLIC_HOST), ("{VERSION}", VERSION), ("{REPO_SHORT}", short), ("{REPO}", REPO_URL)]
    pairs += list((extra or {"{ACT_ERR}": "", "{ACT_CODE}": "", "{ACT_ERR_CLASS}": ""}).items())
    for k, v in pairs:
        page = page.replace(k, html.escape(v))
    return page


def redirects_txt():
    lines = ["# tls_fixer: Twitch-App -> %s" % PUBLIC_HOST]
    lines += ["%s %s" % (h, PUBLIC_HOST) for h in REDIRECT_HOSTS]
    return "\n".join(lines) + "\n"


_banner_png = []


def solid_png(w, h, hexcolor):
    """Einfarbiges RGB-PNG (Ersatz-Banner fuer die Profilkarte)."""
    c = hexcolor.lstrip("#")
    rgb = bytes(int(c[i:i + 2], 16) for i in (0, 2, 4)) if re.fullmatch(r"[0-9a-fA-F]{6}", c) else b"\x1f\x1f\x23"
    row = b"\x00" + rgb * w

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * h, 9)) + chunk(b"IEND", b""))


def ep_banner(base, params, req):
    if not _banner_png:
        _banner_png.append(solid_png(498, 280, PROFILE_BG))
    return 200, _banner_png[0]


def is_subscribed(sess, broadcaster_login):
    """Echter Abo-Status (auch Prime) ueber Helix, 5 Minuten gecacht."""
    if "user:read:subscriptions" not in (sess.get("scopes") or []):
        if not sess.get("_scope_hint"):
            sess["_scope_hint"] = True
            log.info("Abo-Status fuer %s nicht abrufbar: bitte einmal ab- und neu anmelden (neue Berechtigung)",
                     sess.get("login"))
        return False
    tu = user_by_login(broadcaster_login)
    if not tu:
        return False
    key = ("sub", sess["user_id"], tu["id"])
    hit = cache.get(key)
    if hit is not None:
        return hit
    url = HELIX + "subscriptions/user?" + urllib.parse.urlencode({"broadcaster_id": tu["id"], "user_id": sess["user_id"]})
    try:
        data = http_json(url, headers={"Client-Id": CLIENT_ID, "Authorization": "Bearer " + sess["access"]})
        subbed = bool(data.get("data"))
    except urllib.error.HTTPError as exc:
        subbed = False
        if exc.code not in (404,):
            log.warning("Abo-Abfrage %s -> HTTP %s", broadcaster_login, exc.code)
    except Exception as exc:
        log.warning("Abo-Abfrage fehlgeschlagen: %s", type(exc).__name__)
        return False
    cache.set(key, subbed, 300)
    return subbed


def ep_viewer(base, params, req, login):
    """Alte Hidden-API: Abo-/Turbo-Status fuer einen Kanal. Muss schnell antworten,
    sonst wartet die Wiedergabeseite der App ~80 s auf diese Antwort."""
    try:
        sess = session_from(req, params)
    except ApiError:
        sess = None
    subbed = is_subscribed(sess, login) if sess else False
    turbo = has_turbo((sess or {}).get("login"))
    return 200, {"chansub": subbed, "turbo": turbo, "has_ad_free_subscription": subbed or turbo,
                 "is_admin": False, "is_staff": False, "login": (sess or {}).get("login", "")}


def ep_api_user(base, params, req):
    """Alte Hidden-API /api/user: die App wertet turbo_code aus (gesetzt = Turbo)."""
    try:
        sess = session_from(req, params)
    except ApiError:
        sess = None
    turbo = has_turbo((sess or {}).get("login"))
    return 200, {"turbo_code": "turbo" if turbo else None, "turbo": turbo,
                 "login": (sess or {}).get("login", ""), "name": (sess or {}).get("login", "")}


def ep_auth_disconnect(base, params, req):
    sid = first(params, "oauth_token")
    with _sess_lock:
        sess = _sessions.pop(sid, None)
        if sess:
            _save_sessions()
    if sess:
        log.info("Abgemeldet: %s", sess.get("login"))
    return 200, {"status": "ok"}


def ep_empty_follows(base, params, req, login):
    # gefolgte Spiele gibt es in Helix nicht mehr -> leere Liste im erwarteten Format
    return 200, {"_total": 0, "_links": {}, "follows": []}


# --------------------------------------------------------------------------
# Hintergrund-Refresh: haelt alles warm, was die App beim Start abfragt,
# damit diese Anfragen in Millisekunden beantwortet werden (App-Timeout ~1-2 s).
# --------------------------------------------------------------------------
def warm_once():
    t0 = time.time()
    st = collect_streams((), SAMPLE_STREAMS, force=True)
    top_games_list(100, force=True)
    users_by_ids([x.get("user_id") for x in st["items"][:200]])
    return len(st["items"]), int((time.time() - t0) * 1000)


def warm_loop():
    try:
        import yt_dlp  # noqa: F401  (einmal laden, damit der erste Stream schneller startet)
    except ImportError:
        log.warning("yt-dlp nicht als Modul verfuegbar - nutze Unterprozess")
    while True:
        try:
            n, ms = warm_once()
            log.info("Cache aufgewaermt: %d Streams, Top-Spiele, Kanaldaten (%d ms)", n, ms)
        except Exception as exc:
            log.warning("Aufwaermen fehlgeschlagen: %s", getattr(exc, "message", exc))
        time.sleep(WARM_INTERVAL)


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------
ROUTES = [
    (re.compile(r"^/kraken$"), ep_root),
    (re.compile(r"^/kraken/streams$"), ep_streams),
    (re.compile(r"^/kraken/streams/featured$"), ep_featured),
    (re.compile(r"^/kraken/streams/summary$"), ep_summary),
    (re.compile(r"^/kraken/streams/followed$"), ep_followed_streams),
    (re.compile(r"^/kraken/streams/([A-Za-z0-9_]+)$"), ep_stream_by_channel),
    (re.compile(r"^/kraken/channels/([A-Za-z0-9_]+)$"), ep_channel),
    (re.compile(r"^/kraken/channels/([A-Za-z0-9_]+)/videos$"), ep_channel_videos),
    (re.compile(r"^/kraken/videos/v?(\d+)$"), ep_video),
    (re.compile(r"^(?:/api)?/vods/(\d+)/access_token$"), ep_vod_token),
    (re.compile(r"^(?:/api)?/vod/v?(\d+)(?:\.m3u8)?$"), ep_vod_hls),
    (re.compile(r"^/kraken/users/([A-Za-z0-9_]+)$"), ep_user),
    (re.compile(r"^/kraken/games/top$"), ep_top_games),
    (re.compile(r"^/kraken/games$"), ep_games_by_id),
    (re.compile(r"^/kraken/search/streams$"), ep_search_streams),
    (re.compile(r"^/kraken/search/channels$"), ep_search_channels),
    (re.compile(r"^/kraken/search/games$"), ep_search_games),
    (re.compile(r"^/kraken/user$"), ep_me),
    (re.compile(r"^/kraken/users/([A-Za-z0-9_]+)/follows/channels$"), ep_followed_channels),
    (re.compile(r"^/kraken/users/([A-Za-z0-9_]+)/follows/channels/([A-Za-z0-9_]+)$"), ep_follow_status),
    (re.compile(r"^(?:/api)?/users/([A-Za-z0-9_]+)/follows/games(?:/live)?$"), ep_empty_follows),
    (re.compile(r"^/img/banner\.png$"), ep_banner),
    (re.compile(r"^/img/r/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)\.jpg$"), ep_image),
    (re.compile(r"^/api/channels/([A-Za-z0-9_]+)/viewer$"), ep_viewer),
    (re.compile(r"^/api/user$"), ep_api_user),
    (re.compile(r"^.*/disconnect$"), ep_auth_disconnect),
    (re.compile(r"^.*/generate$"), ep_auth_generate),
    (re.compile(r"^.*/authenticate$"), ep_auth_authenticate),
    (re.compile(r"^(?:/api)?/channels/([A-Za-z0-9_]+)/access_token(?:\.json)?$"), ep_access_token),
    (re.compile(r"^(?:/api)?/channel/hls/([A-Za-z0-9_]+)\.m3u8$"), ep_hls),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "VitaTwitchAdapter/" + VERSION
    sys_version = ""

    def _client_ip(self):
        return (self.headers.get("X-Real-IP")
                or (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                or self.client_address[0])

    def _wants_html(self):
        """Nur echte Browser bekommen die Infoseite - nie die Vita-App oder Twitch-Hostnamen."""
        ua = self.headers.get("User-Agent", "")
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return (self.command in ("GET", "HEAD") and "text/html" in self.headers.get("Accept", "")
                and "Sony-PSV" not in ua and not any(t in host for t in ("twitch", "ttvnw", "justin", "mixpanel")))

    def _insecure(self):
        return (self.headers.get("X-Forwarded-Proto") or "https").lower() == "http"

    def _redirect(self, code, location):
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        return code, 0

    def _send_text(self, code, text, ctype, head, cache=0, extra=None):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=%d" % cache if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)
        return code, len(body)

    def _host(self):
        h = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or PUBLIC_HOST)
        h = h.split(",")[0].strip()
        # Proxy-Links immer ueber den eigenen Host (dort gelten unsere TLS-/HTTP-Regeln)
        return PUBLIC_HOST if "twitch" in h or "ttvnw" in h or "justin" in h else h

    def _proxy(self, sig, blob, ext, head):
        url = proxy_target(sig, blob)
        try:
            resp = upstream(url)
        except urllib.error.HTTPError as exc:
            raise ApiError(404 if exc.code in (403, 404, 410) else 502,
                           "Upstream HTTP %d" % exc.code)
        except Exception as exc:
            raise ApiError(502, "Upstream unreachable: %s" % type(exc).__name__)
        with resp:
            if ext == "m3u8":
                text = resp.read().decode("utf-8", "replace")
                return self._send(200, rewrite_playlist(text, resp.geturl(), "http://" + self._host()), head)
            self.send_response(200)
            uct = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            self.send_header("Content-Type", uct if uct.startswith("video/") else "video/mp2t")
            ln = resp.headers.get("Content-Length")
            if ln:
                self.send_header("Content-Length", ln)
            else:
                self.close_connection = True
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            total = 0
            if not head:
                try:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        total += len(chunk)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    self.close_connection = True
                    return 499, total
            return 200, total

    def _base(self):
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host")
                or "api.twitch.tv").split(",")[0].strip()
        return "https://" + host

    def _send(self, code, payload, head=False):
        if isinstance(payload, bytes):  # Bilder (PNG/JPEG am Dateikopf erkannt)
            body = payload
            ctype = "image/jpeg" if payload[:2] == b"\xff\xd8" else "image/png"
        elif isinstance(payload, str):  # m3u8
            body = payload.encode("utf-8")
            ctype = "application/vnd.apple.mpegurl"
        else:
            if LITE:
                payload = lite(payload)
            if ID_STRINGS or PREVIEW_STRING or HTTP_IMAGES:
                payload = variant(payload)
            body = json.dumps(sanitize(payload), ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            ctype = "application/json; charset=utf-8"
            acc = self.headers.get("Accept", "")
            m = re.search(r"application/vnd\.twitchtv\.v\d+\+json", acc)
            if CT_MODE == "echo" and m:
                ctype = m.group(0) + "; charset=utf-8"
        raw_len = len(body)
        gz = (GZIP_MODE == "auto" and "gzip" in self.headers.get("Accept-Encoding", "").lower()
              and len(body) > 512)
        if gz:
            body = gzip.compress(body, 6)
        self._raw_len = raw_len
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if gz:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if not head:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            self.close_connection = True
            return 499, 0  # Client hat vorher aufgelegt
        return code, len(body)

    def _error(self, status, message, head=False):
        return self._send(status, {"error": REASONS.get(status, "Error"),
                                   "status": status, "message": message}, head)

    def _handle(self, head=False):
        t0 = time.time()
        parsed = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = re.sub(r"/+", "/", parsed.path).rstrip("/") or "/"
        base = self._base()
        status, size, matched = 500, 0, True
        try:
            pm = re.match(r"^/hls/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)\.(m3u8|ts)$", path)
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            if (path in ("/track", "/engage", "/decide", "/import") or host in ("spade.twitch.tv", "api.mixpanel.com")) \
                    or (self.command == "POST" and path == "/"):
                # Analytics (spade/mixpanel) sofort bestaetigen: sonst haengt die native HTTP-Warteschlange
                # der App bei jedem Aufruf ~34 s im Timeout und blockiert Login/Folgen-Abfragen.
                body = b"1"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "1")
                self.end_headers()
                if not head:
                    self.wfile.write(body)
                status, size = 200, 1
            elif (path == "/activate" or path == "/link" or (path in ("/", "/index.html") and self._wants_html())) \
                    and self._insecure():
                # Browser-Seiten nur ueber HTTPS (Port 80 ist nur fuer die Stream-Links der Vita da)
                if self.command == "POST":
                    status, size = self._send_text(403, '{"ok":false,"error":"Nur ueber HTTPS."}',
                                                   "application/json; charset=utf-8", head)
                else:
                    status, size = self._redirect(301, "https://" + PUBLIC_HOST + self.path)
            elif path == "/activate" or (path in ("/", "/index.html") and self._wants_html()):
                form = None
                if self.command == "POST":
                    form = dict(urllib.parse.parse_qsl(getattr(self, "_body", b"").decode("utf-8", "replace")))
                code, location, page = activate_page(self, params, form)
                if location:
                    status, size = self._redirect(code, location)
                else:
                    status, size = self._send_text(code, page, "text/html; charset=utf-8", head,
                                                   extra=html_security_headers(page))
            elif path == "/link" and self.command == "POST":
                form = dict(urllib.parse.parse_qsl(getattr(self, "_body", b"").decode("utf-8", "replace")))
                code, res = link_action(self, form)
                status, size = self._send_text(code, json.dumps(res, ensure_ascii=False),
                                               "application/json; charset=utf-8", head)
            elif path == "/link":
                status, size = self._redirect(302, "/#werbefrei")
            elif path == "/redirects.txt":
                status, size = self._send_text(200, redirects_txt(), "text/plain; charset=utf-8", head, cache=300,
                                               extra={"Content-Disposition": 'attachment; filename="redirects.txt"'})
            elif pm:
                status, size = self._proxy(pm.group(1), pm.group(2), pm.group(3), head)
            elif path in ("/", "/test", "/health"):
                status, size = self._send(200, {"ok": True, "service": "vita-twitch-adapter",
                                                "version": VERSION, "vitas": presence_stats()}, head)
            else:
                for rx, fn in ROUTES:
                    m = rx.match(path)
                    if m:
                        code, payload = fn(base, params, self, *m.groups())
                        status, size = self._send(code, payload, head)
                        break
                else:
                    matched = False
                    status, size = self._error(404, "Unsupported endpoint: " + path, head)
        except ApiError as exc:
            status, size = self._error(exc.status, exc.message, head)
        except (BrokenPipeError, ConnectionResetError):
            status = 499
        except subprocess.TimeoutExpired:
            log.error("yt-dlp Timeout fuer %s", path)
            status, size = self._error(502, "Upstream timeout", head)
        except Exception:
            log.exception("Unerwarteter Fehler bei %s", self.path)
            try:
                status, size = self._error(500, "Internal adapter error", head)
            except Exception:
                status = 500
        ms = int((time.time() - t0) * 1000)
        if status < 400 or (path.startswith("/hls/") and status != 403):   # 403 = gefaelschter Link
            track_presence(self._client_ip(), self.headers.get("User-Agent", ""), path)
        ua = self.headers.get("User-Agent", "-")
        level = logging.WARNING if (not matched or status >= 400) else logging.INFO
        safe_path = re.sub(r"((?:^|[?&])(?:token|oauth_token|sig)=)[^&]*", r"\1***", self.path)
        log.log(level, "%s %s %s -> %s %dB %dms UA=%r%s", self._client_ip(), self.command,
                safe_path, status, size, ms, ua, "" if matched else "  [UNBEKANNTER ENDPUNKT]")
        reqlog.info(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ip": self._client_ip(), "method": self.command,
            "host": self.headers.get("Host", ""), "path": safe_path, "status": status,
            "bytes": size, "ms": ms, "ua": ua, "accept": self.headers.get("Accept", ""),
            "client_id_header": bool(self.headers.get("Client-ID")),
            "raw_bytes": getattr(self, "_raw_len", size),
            "headers": {k: (v[:4] + "..." if k.lower() in ("authorization", "client-id", "cookie")
                            else v) for k, v in self.headers.items()},
            "auth_header": bool(self.headers.get("Authorization")),
            "known": matched,
        }, ensure_ascii=False))

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle(head=True)

    def do_POST(self):
        length = as_int(self.headers.get("Content-Length"), 0, 0, 65536)
        self._body = self.rfile.read(length) if length else b""
        self._handle()

    do_PUT = do_POST
    do_DELETE = do_GET

    def log_message(self, fmt, *args):
        pass  # eigenes Logging in _handle


def main():
    global WEB_OWNER
    if not CLIENT_ID or not CLIENT_SECRET:
        log.error("TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET fehlen in der Umgebung")
        sys.exit(2)
    if not os.path.isfile(CERT) or not os.path.isfile(KEY):
        log.error("TLS-Zertifikat oder Schluessel fehlt: %s / %s", CERT, KEY)
        sys.exit(2)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(CERT, KEY)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    log.info("vita-twitch-adapter %s lauscht auf https://%s:%s (max. %sp, CT=%s, gzip=%s, "
             "max_items=%s, lite=%s, id_str=%s, preview_str=%s, http_img=%s, hls_proxy=%s, pref=%sp)",
             VERSION, HOST, PORT, MAX_HEIGHT, CT_MODE, GZIP_MODE, MAX_ITEMS or "aus", LITE, ID_STRINGS,
             PREVIEW_STRING, HTTP_IMAGES, HLS_PROXY, PREF_HEIGHT)
    try:
        get_token()
    except ApiError as exc:
        log.error("Start-Check Twitch-Token fehlgeschlagen: %s (Dienst laeuft weiter)", exc.message)
    _load_sessions()
    _load_hls_key()
    _load_webtokens()
    if WEB_AUTH_TOKEN:
        try:
            v = http_json("https://id.twitch.tv/oauth2/validate", headers={"Authorization": "OAuth " + WEB_AUTH_TOKEN})
            WEB_OWNER = WEB_OWNER or (v.get("login") or "").lower() or None
            log.info("Web-Token gehoert %s - %s", WEB_OWNER or "?",
                     "gilt fuer ALLE Konten (VITA_WEB_TOKEN_SHARE=1)" if WEB_SHARE
                     else "nur dieses Konto spielt mit Turbo/Abo-Vorteilen, alle anderen anonym")
        except urllib.error.HTTPError as exc:
            log.error("TWITCH_WEB_AUTH_TOKEN ungueltig (HTTP %s) - Wiedergabe laeuft anonym mit Werbung", exc.code)
        except Exception as exc:
            log.warning("TWITCH_WEB_AUTH_TOKEN nicht pruefbar: %s", type(exc).__name__)
    else:
        log.info("Kein TWITCH_WEB_AUTH_TOKEN - Wiedergabe anonym (Twitch blendet Werbung ein)")
    threading.Thread(target=warm_loop, name="warm", daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
