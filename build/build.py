#!/usr/bin/env python3
"""Build data for the Hi5 lobby screen.

Fetches the public Hi5 Google Calendar, expands recurring events, finds a
preview image and a link for each upcoming event, and writes:

    data/events.json        what the page renders
    data/img/<hash>.webp    resized preview images
    data/qr/<hash>.svg      QR codes for event links

Per-URL lookup results are cached in build/.cache/ (persisted by the
workflow's actions/cache step, not committed), so linked sites aren't
fetched every 15 minutes and commits only happen when screen content changes.

Image sources per event, first hit wins: a direct image URL or Google Drive
attachment, then the og:image of the first web page linked from the event.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import io
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import emoji
import icalendar
import recurring_ical_events
import requests
import segno
from PIL import Image, ImageOps

ICS_URL = (
    "https://calendar.google.com/calendar/ical/"
    "c_c601aaf767740129b6f90e9269a43adb02bfae28b4bb269626227be8c0087548"
    "%40group.calendar.google.com/public/basic.ics"
)
TZ = ZoneInfo("Europe/Oslo")
HORIZON_DAYS = 90
MAX_EVENTS = 21  # room for up to 3 list pages of 7; app.js MAX_PAGES decides how many show
IMAGE_MAX_PX = 1280
IMAGE_BACKGROUND = (38, 38, 38)  # flattens transparent PNGs onto the page's panel colour
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024
CACHE_TTL = dt.timedelta(hours=24)
ERROR_TTL = dt.timedelta(hours=1)
USER_AGENT = "Hi5-skjerm/1.0 (+https://github.com/Grunderhuset-Hi5/skjerm)"
# The TV's emoji font predates Unicode 12, so emoji in titles are drawn as Twemoji SVGs.
TWEMOJI_URL = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@16.0.1/assets/svg/{code}.svg"

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE_FILE = ROOT / "build" / ".cache" / "media-cache.json"
APP_FILES = ("index.html", "app.js", "style.css")

# Hosts whose og:image is a product logo, not something about the event.
NO_PREVIEW_HOSTS = {
    "accounts.google.com",
    "calendar.google.com",
    "docs.google.com",
    "drive.google.com",
    "meet.google.com",
    "teams.microsoft.com",
    "teams.live.com",
    "zoom.us",
}
# Hosts that make no sense as a QR target for people in the lobby.
NO_LINK_HOSTS = {"accounts.google.com", "calendar.google.com", "drive.google.com"}

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
DRIVE_ID_RE = re.compile(r"(?:/file/d/|[?&]id=)([\w-]{20,})")
URL_TRAILING_JUNK = ".,;:!?)]}"


# ---------------------------------------------------------------- calendar


@dataclass
class Event:
    uid: str
    title: str
    start: dt.datetime
    end: dt.datetime
    all_day: bool
    location: str
    urls: list[str] = field(default_factory=list)


def to_local(value: dt.date | dt.datetime) -> tuple[dt.datetime, bool]:
    """Return (aware datetime in Oslo time, is_all_day)."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:  # floating time: treat as local
            value = value.replace(tzinfo=TZ)
        return value.astimezone(TZ), False
    return dt.datetime.combine(value, dt.time(), TZ), True


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def unwrap_redirect(url: str) -> str:
    """Google Calendar sometimes wraps links as google.com/url?q=<real url>."""
    parsed = urlparse(url)
    if parsed.netloc in ("www.google.com", "google.com") and parsed.path == "/url":
        target = parse_qs(parsed.query).get("q")
        if target and target[0].startswith(("http://", "https://")):
            return target[0]
    return url


def extract_urls(*texts: Any) -> list[str]:
    """All http(s) URLs in the given texts, in order, deduplicated."""
    seen: set[str] = set()
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in URL_RE.finditer(html.unescape(str(text))):
            url = unwrap_redirect(match.group(0).rstrip(URL_TRAILING_JUNK))
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def clean_location(raw: Any) -> str:
    lines = [line.strip() for line in str(raw or "").splitlines()]
    return ", ".join(line for line in lines if line)


# Calendar convention: events at Hi5 have no location, or one starting with
# "Gründerhuset". Everything else is shown on screen as being elsewhere.
HOME_PREFIX = "grunderhuset"
ONLINE_RE = re.compile(r"\b(webinar|online|digital|digitalt|teams|zoom|meet|pa nett)\b")
COUNTRY_SUFFIX_RE = re.compile(r",\s*(norway|norge)\s*$", re.IGNORECASE)


def _fold(text: str) -> str:
    """Lowercase, accents stripped: 'Gründerhuset' -> 'grunderhuset'."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def place_info(location: str) -> tuple[Optional[str], Optional[str]]:
    """(label, kind) for events away from Hi5; (None, None) for events at Hi5.

    kind is "online" for webinars/online meetings, otherwise "away".
    """
    folded = _fold(location)
    if not folded or folded.startswith(HOME_PREFIX):
        return None, None
    label = COUNTRY_SUFFIX_RE.sub("", location).strip()
    return label, ("online" if ONLINE_RE.search(folded) else "away")


def load_calendar(ics: bytes) -> icalendar.Calendar:
    return icalendar.Calendar.from_ical(ics)


def select_events(
    cal: icalendar.Calendar,
    now: dt.datetime,
    horizon_days: int = HORIZON_DAYS,
    limit: int = MAX_EVENTS,
) -> list[Event]:
    """Ongoing and upcoming events within the horizon, soonest first."""
    window_end = now + dt.timedelta(days=horizon_days)
    events: list[Event] = []
    for comp in recurring_ical_events.of(cal).between(now, window_end):
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        start, all_day = to_local(comp["DTSTART"].dt)
        if comp.get("DTEND") is not None:
            end, _ = to_local(comp["DTEND"].dt)
        elif comp.get("DURATION") is not None:
            end = start + comp["DURATION"].dt
        else:
            end = start + (dt.timedelta(days=1) if all_day else dt.timedelta(0))
        if start < now and end <= now:
            continue

        location = clean_location(comp.get("LOCATION"))
        location_urls = extract_urls(location)
        if location_urls and location.strip() in location_urls:
            location = ""  # the location *is* a link; use it as a link, not a place
        urls = extract_urls(
            comp.get("URL"),
            *[str(a) for a in _as_list(comp.get("ATTACH"))],
            comp.get("DESCRIPTION"),
            *location_urls,
        )
        events.append(
            Event(
                uid=f"{comp.get('UID', '')}@{start.isoformat()}",
                title=str(comp.get("SUMMARY", "")).strip() or "Uten tittel",
                start=start,
                end=end,
                all_day=all_day,
                location=location,
                urls=urls,
            )
        )
    events.sort(key=lambda e: (e.start, e.title))
    return events[:limit]


# ------------------------------------------------------------------ media


class _MetaImageParser(HTMLParser):
    KEYS = ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in self.KEYS and a.get("content"):
                self.found.setdefault(key, a["content"].strip())
        elif tag == "link" and a.get("rel", "").lower() == "image_src" and a.get("href"):
            self.found.setdefault("image_src", a["href"].strip())


def find_og_image(page_html: str, base_url: str) -> Optional[str]:
    parser = _MetaImageParser()
    try:
        parser.feed(page_html)
    except Exception:  # malformed HTML: use whatever was found before the error
        pass
    for key in (*_MetaImageParser.KEYS, "image_src"):
        if key in parser.found:
            return urljoin(base_url, parser.found[key])
    return None


def drive_download_url(url: str) -> Optional[str]:
    """Direct-download URL for a Google Drive file link, if it is one."""
    if urlparse(url).netloc != "drive.google.com":
        return None
    match = DRIVE_ID_RE.search(url)
    return f"https://drive.google.com/uc?export=download&id={match.group(1)}" if match else None


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class HttpSession(Protocol):
    def get(self, url: str, timeout: float) -> requests.Response: ...


class FetchError(Exception):
    pass


class MediaResolver:
    """Looks up links and images for events, with a persistent per-URL cache."""

    def __init__(self, session: HttpSession, data_dir: Path, cache: dict[str, dict], now: dt.datetime):
        self.session = session
        self.data_dir = data_dir
        self.cache = cache
        self.now = now
        self.used_urls: set[str] = set()
        self._site_defaults: dict[str, Optional[str]] = {}

    # -- public

    def resolve(self, urls: Iterable[str]) -> tuple[Optional[str], Optional[str]]:
        """Return (image path relative to site root, link for the QR code)."""
        image: Optional[str] = None
        link: Optional[str] = None
        for url in urls:
            info = self.inspect(url)
            if link is None and info["kind"] == "page" and _host(url) not in NO_LINK_HOSTS:
                link = url
            if image is None and info.get("image"):
                image = info["image"]
        return image, link

    def inspect(self, url: str) -> dict:
        self.used_urls.add(url)
        old = self.cache.get(url)
        if old and self._is_fresh(old):
            return old
        try:
            entry = self._inspect_uncached(url)
        except (requests.RequestException, FetchError) as exc:
            print(f"warn: {url}: {exc}", file=sys.stderr)
            if old and old.get("kind") != "error":
                return old  # keep last good result rather than flapping
            entry = {"kind": "error", "image": None}
        entry["checked"] = self.now.isoformat(timespec="seconds")
        self.cache[url] = entry
        return entry

    def prune_cache(self) -> dict[str, dict]:
        return {url: self.cache[url] for url in sorted(self.used_urls) if url in self.cache}

    # -- internals

    def _is_fresh(self, entry: dict) -> bool:
        if entry.get("image") and not (self.data_dir.parent / entry["image"]).exists():
            return False
        ttl = ERROR_TTL if entry.get("kind") == "error" else CACHE_TTL
        try:
            checked = dt.datetime.fromisoformat(entry["checked"])
        except (KeyError, ValueError):
            return False
        return self.now - checked < ttl

    def _get(self, url: str) -> requests.Response:
        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        if len(resp.content) > MAX_DOWNLOAD_BYTES:
            raise FetchError(f"too large ({len(resp.content)} bytes)")
        return resp

    def _inspect_uncached(self, url: str) -> dict:
        drive = drive_download_url(url)
        if drive:
            return {"kind": "image", "image": self._try_image(drive, key=url)}
        resp = self._get(url)
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype.startswith("image/"):
            return {"kind": "image", "image": self._save_image(resp.content, key=url)}
        image = None
        if ctype in ("text/html", "application/xhtml+xml"):
            hosts = {_host(url), _host(resp.url)}
            if not hosts & NO_PREVIEW_HOSTS:
                og = find_og_image(resp.text, resp.url)
                if og and og != self._site_default_image(resp.url):
                    image = self._try_image(og, key=og)
        return {"kind": "page", "image": image}

    def _site_default_image(self, page_url: str) -> Optional[str]:
        """The og:image of the site's front page. Pages that only inherit it get no image."""
        parsed = urlparse(page_url)
        root = f"{parsed.scheme}://{parsed.netloc}/"
        if root not in self._site_defaults:
            try:
                resp = self._get(root)
                self._site_defaults[root] = find_og_image(resp.text, resp.url)
            except (requests.RequestException, FetchError):
                self._site_defaults[root] = None
        return self._site_defaults[root]

    def _try_image(self, url: str, key: str) -> Optional[str]:
        rel = f"data/img/{short_hash(key)}.webp"
        if (self.data_dir.parent / rel).exists():
            return rel  # already have it; skip the download
        try:
            resp = self._get(url)
            return self._save_image(resp.content, key=key)
        except (requests.RequestException, FetchError, OSError) as exc:
            print(f"warn: image {url}: {exc}", file=sys.stderr)
            return None

    def _save_image(self, data: bytes, key: str) -> str:
        """Resize to WebP. Raises OSError if data isn't an image."""
        rel = f"data/img/{short_hash(key)}.webp"
        path = self.data_dir.parent / rel
        if path.exists():
            return rel
        with Image.open(io.BytesIO(data)) as src:
            img = ImageOps.exif_transpose(src)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                flat = Image.new("RGB", img.size, IMAGE_BACKGROUND)
                flat.paste(img, mask=img.getchannel("A"))
                img = flat
            else:
                img = img.convert("RGB")
            img.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX), Image.Resampling.LANCZOS)
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path, "WEBP", quality=80, method=6)
        return rel


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def save_qr(link: str, data_dir: Path) -> str:
    rel = f"data/qr/{short_hash(link)}.svg"
    path = data_dir.parent / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        segno.make(link, error="m").save(
            str(path), kind="svg", border=2, dark="#000000", light="#ffffff",
            xmldecl=False, omitsize=True,
        )
    return rel


# ------------------------------------------------------------------ emoji


def twemoji_code(seq: str) -> str:
    """Twemoji file name for an emoji sequence (same rule as twemoji.js)."""
    if "‍" not in seq:
        seq = seq.replace("️", "")
    return "-".join(f"{ord(c):x}" for c in seq)


def _wants_image(seq: str) -> bool:
    # Plain text-style symbols (©, ™, ↔) render fine in the TV's normal font.
    return "️" in seq or any(ord(c) >= 0x1F000 for c in seq)


def split_emoji(text: str) -> list[tuple[str, str]]:
    """Split into ('text', s) and ('emoji', s) parts, in order."""
    parts: list[tuple[str, str]] = []
    pos = 0
    for match in emoji.emoji_list(text):
        if not _wants_image(match["emoji"]):
            continue
        if match["match_start"] > pos:
            parts.append(("text", text[pos:match["match_start"]]))
        parts.append(("emoji", match["emoji"]))
        pos = match["match_end"]
    if pos < len(text):
        parts.append(("text", text[pos:]))
    return parts


def title_parts(title: str, emoji_src) -> Optional[list[dict]]:
    """Title as text/emoji parts for the page, or None if it has no emoji."""
    parts = split_emoji(title)
    if not any(kind == "emoji" for kind, _ in parts):
        return None
    out = []
    for kind, value in parts:
        src = emoji_src(value) if kind == "emoji" else None
        out.append({"emoji": value, "src": src} if src else {"text": value})
    return out


class EmojiStore:
    """Downloads Twemoji SVGs into data/emoji/ on first use."""

    def __init__(self, session: HttpSession, data_dir: Path):
        self.session = session
        self.data_dir = data_dir
        self.used: set[str] = set()

    def __call__(self, seq: str) -> Optional[str]:
        code = twemoji_code(seq)
        rel = f"data/emoji/{code}.svg"
        path = self.data_dir.parent / rel
        if not path.exists():
            try:
                resp = self.session.get(TWEMOJI_URL.format(code=code), timeout=20)
                resp.raise_for_status()
                if not resp.content.lstrip().startswith(b"<svg"):
                    raise FetchError("not an SVG")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(resp.content)
            except (requests.RequestException, FetchError) as exc:
                print(f"warn: emoji {seq!r} ({code}): {exc}", file=sys.stderr)
                return None  # the page shows the character as text instead
        self.used.add(path.name)
        return rel


# ----------------------------------------------------------------- output


def app_version(root: Path = ROOT) -> str:
    """Hash of the page's own files. The page reloads itself when this changes."""
    digest = hashlib.sha1()
    for name in APP_FILES:
        path = root / name
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def build_payload(
    cal: icalendar.Calendar, now: dt.datetime, resolver: MediaResolver, emoji_src, version: str
) -> dict:
    events = []
    for ev in select_events(cal, now):
        image, link = resolver.resolve(ev.urls)
        place, place_kind = place_info(ev.location)
        events.append({
            "id": ev.uid,
            "title": ev.title,
            "title_parts": title_parts(ev.title, emoji_src),
            "start": ev.start.isoformat(),
            "end": ev.end.isoformat(),
            "all_day": ev.all_day,
            "location": ev.location,
            "place": place,
            "place_kind": place_kind,
            "link": link,
            "image": image,
            "qr": save_qr(link, resolver.data_dir) if link else None,
        })
    return {
        "generated": now.isoformat(timespec="seconds"),
        "app_version": version,
        "calendar": str(cal.get("X-WR-CALNAME", "")),
        "events": events,
    }


def write_json_if_changed(path: Path, payload: dict, ignore: tuple[str, ...] = ("generated",)) -> bool:
    """Write only when content other than `ignore` keys changed, to avoid empty commits."""
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old = None
        strip = lambda d: {k: v for k, v in d.items() if k not in ignore}  # noqa: E731
        if isinstance(old, dict) and strip(old) == strip(payload):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return True


def prune_dir(directory: Path, keep: set[str]) -> list[str]:
    removed = []
    if directory.exists():
        for path in directory.iterdir():
            if path.is_file() and path.name not in keep:
                path.unlink()
                removed.append(path.name)
    return removed


def main() -> int:
    now = dt.datetime.now(TZ)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    resp = session.get(ICS_URL, timeout=30)
    resp.raise_for_status()
    cal = load_calendar(resp.content)

    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}  # first run or cache evicted: everything gets looked up again
    resolver = MediaResolver(session, DATA, cache, now)

    emoji_store = EmojiStore(session, DATA)
    payload = build_payload(cal, now, resolver, emoji_store, app_version())
    changed = write_json_if_changed(DATA / "events.json", payload)
    write_json_if_changed(CACHE_FILE, resolver.prune_cache(), ignore=())

    keep_img = {Path(e["image"]).name for e in payload["events"] if e["image"]}
    keep_qr = {Path(e["qr"]).name for e in payload["events"] if e["qr"]}
    removed = (prune_dir(DATA / "img", keep_img) + prune_dir(DATA / "qr", keep_qr)
               + prune_dir(DATA / "emoji", emoji_store.used))

    print(f"{len(payload['events'])} events, events.json {'updated' if changed else 'unchanged'}"
          f"{', removed ' + str(len(removed)) + ' stale files' if removed else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
