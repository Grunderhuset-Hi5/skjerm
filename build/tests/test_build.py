import datetime as dt
import io
import json

import pytest
import requests
from PIL import Image

import build
from build import TZ

NOW = dt.datetime(2026, 11, 17, 12, 0, tzinfo=TZ)

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
X-WR-CALNAME:Testkalender
BEGIN:VEVENT
UID:past
DTSTART;TZID=Europe/Oslo:20261116T100000
DTEND;TZID=Europe/Oslo:20261116T110000
SUMMARY:Past
END:VEVENT
BEGIN:VEVENT
UID:ongoing
DTSTART;TZID=Europe/Oslo:20261117T110000
DTEND;TZID=Europe/Oslo:20261117T130000
SUMMARY:Ongoing
END:VEVENT
BEGIN:VEVENT
UID:future
DTSTART:20261124T080000Z
DTEND:20261124T100000Z
SUMMARY:Webinar
LOCATION:Colab\\nKongegata 29
DESCRIPTION:Meld deg p\xc3\xa5: <a href="https://www.google.com/url?q=https://example.org/event?id%3D1&amp;sa=D">her</a>. Mer: https://example.org/event?id=1.
END:VEVENT
BEGIN:VEVENT
UID:weekly
DTSTART;TZID=Europe/Oslo:20261118T113000
DTEND;TZID=Europe/Oslo:20261118T123000
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Vaffelfest
END:VEVENT
BEGIN:VEVENT
UID:allday
DTSTART;VALUE=DATE:20261120
DTEND;VALUE=DATE:20261121
SUMMARY:Heldag
LOCATION:https://ghi5.no/book
END:VEVENT
BEGIN:VEVENT
UID:cancelled
DTSTART;TZID=Europe/Oslo:20261119T100000
DTEND;TZID=Europe/Oslo:20261119T110000
STATUS:CANCELLED
SUMMARY:Avlyst
END:VEVENT
BEGIN:VEVENT
UID:far
DTSTART;TZID=Europe/Oslo:20270601T100000
DTEND;TZID=Europe/Oslo:20270601T110000
SUMMARY:Too far
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def events():
    return build.select_events(build.load_calendar(ICS), NOW)


def test_selects_ongoing_and_upcoming_in_order(events):
    assert [e.title for e in events] == [
        "Ongoing", "Vaffelfest", "Heldag", "Webinar", "Vaffelfest", "Vaffelfest",
    ]


def test_recurring_instances_get_unique_ids(events):
    ids = [e.uid for e in events if e.title == "Vaffelfest"]
    assert len(set(ids)) == 3


def test_limit(events):
    assert len(build.select_events(build.load_calendar(ICS), NOW, limit=2)) == 2


def test_times_are_local(events):
    webinar = next(e for e in events if e.title == "Webinar")
    assert webinar.start == dt.datetime(2026, 11, 24, 9, 0, tzinfo=TZ)
    assert webinar.start.utcoffset() == dt.timedelta(hours=1)
    assert not webinar.all_day


def test_all_day_event_and_location_url(events):
    heldag = next(e for e in events if e.title == "Heldag")
    assert heldag.all_day
    assert heldag.start == dt.datetime(2026, 11, 20, tzinfo=TZ)
    assert heldag.end == dt.datetime(2026, 11, 21, tzinfo=TZ)
    assert heldag.location == ""
    assert heldag.urls == ["https://ghi5.no/book"]


def test_multiline_location_and_description_links(events):
    webinar = next(e for e in events if e.title == "Webinar")
    assert webinar.location == "Colab, Kongegata 29"
    assert webinar.urls == ["https://example.org/event?id=1"]


def test_extract_urls_trims_punctuation_and_dedupes():
    text = "Se https://a.no/x. Og (https://b.no/y), igjen https://a.no/x"
    assert build.extract_urls(text) == ["https://a.no/x", "https://b.no/y"]


def test_drive_download_url():
    url = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view?usp=drive_web"
    assert build.drive_download_url(url) == (
        "https://drive.google.com/uc?export=download&id=1AbCdEfGhIjKlMnOpQrStUvWxYz"
    )
    assert build.drive_download_url("https://example.org/file/d/x") is None


def test_find_og_image_resolves_relative():
    page = '<html><head><meta property="og:image" content="/img/a.jpg"></head></html>'
    assert build.find_og_image(page, "https://example.org/event/1") == "https://example.org/img/a.jpg"


def test_find_og_image_prefers_og_over_twitter():
    page = ('<meta name="twitter:image" content="https://x/t.jpg">'
            '<meta property="og:image" content="https://x/o.jpg">')
    assert build.find_og_image(page, "https://x/") == "https://x/o.jpg"


def test_write_json_if_changed_ignores_generated(tmp_path):
    path = tmp_path / "events.json"
    assert build.write_json_if_changed(path, {"generated": "a", "events": [1]})
    assert not build.write_json_if_changed(path, {"generated": "b", "events": [1]})
    assert json.loads(path.read_text())["generated"] == "a"
    assert build.write_json_if_changed(path, {"generated": "c", "events": [2]})


# ------------------------------------------------------------ media resolver


def _png(color=(200, 50, 50), size=(40, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, url, content=b"", ctype="text/html", status=200):
        self.url = url
        self.content = content
        self.headers = {"content-type": ctype}
        self.status_code = status

    @property
    def text(self):
        return self.content.decode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, timeout):
        self.calls.append(url)
        if url not in self.routes:
            return FakeResponse(url, status=404)
        content, ctype = self.routes[url]
        return FakeResponse(url, content, ctype)


def _page(og):
    return f'<meta property="og:image" content="{og}">'.encode()


@pytest.fixture
def site():
    return {
        "https://ev.no/": (_page("https://ev.no/default.png"), "text/html"),
        "https://ev.no/event/1": (_page("https://ev.no/poster.png"), "text/html"),
        "https://ev.no/event/2": (_page("https://ev.no/default.png"), "text/html"),
        "https://ev.no/poster.png": (_png(), "image/png"),
        "https://ev.no/default.png": (_png((0, 0, 0)), "image/png"),
        "https://img.no/direct.jpg": (_png((0, 200, 0), (3000, 1500)), "image/jpeg"),
    }


def _resolver(tmp_path, routes, cache=None, now=NOW):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return build.MediaResolver(FakeSession(routes), data, {} if cache is None else cache, now)


def test_og_image_from_event_page(tmp_path, site):
    r = _resolver(tmp_path, site)
    image, link = r.resolve(["https://ev.no/event/1"])
    assert link == "https://ev.no/event/1"
    assert image.startswith("data/img/") and (tmp_path / image).exists()


def test_site_default_og_image_is_ignored(tmp_path, site):
    image, link = _resolver(tmp_path, site).resolve(["https://ev.no/event/2"])
    assert image is None
    assert link == "https://ev.no/event/2"


def test_direct_image_wins_and_is_resized(tmp_path, site):
    image, link = _resolver(tmp_path, site).resolve(
        ["https://img.no/direct.jpg", "https://ev.no/event/1"])
    assert link == "https://ev.no/event/1"
    with Image.open(tmp_path / image) as im:
        assert im.format == "WEBP"
        assert im.size == (1280, 640)


def test_broken_link_is_not_used(tmp_path, site):
    image, link = _resolver(tmp_path, site).resolve(["https://gone.no/x", "https://ev.no/event/1"])
    assert link == "https://ev.no/event/1"


def test_drive_links_never_become_qr_links(tmp_path):
    url = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view"
    image, link = _resolver(tmp_path, {}).resolve([url])
    assert link is None and image is None


def test_cache_avoids_refetch_and_survives_outage(tmp_path, site):
    cache = {}
    first = _resolver(tmp_path, site, cache)
    first.resolve(["https://ev.no/event/1"])
    assert cache["https://ev.no/event/1"]["image"]

    # Within TTL: no network at all.
    second = _resolver(tmp_path, {}, cache, NOW + dt.timedelta(hours=1))
    assert second.resolve(["https://ev.no/event/1"])[0]
    assert second.session.calls == []

    # After TTL with the site down: keep the last good result.
    third = _resolver(tmp_path, {}, cache, NOW + dt.timedelta(days=2))
    image, link = third.resolve(["https://ev.no/event/1"])
    assert image and link == "https://ev.no/event/1"


@pytest.mark.parametrize("seq, code", [
    ("🧇", "1f9c7"),
    ("❤️", "2764"),                       # FE0F dropped when there's no ZWJ
    ("👩‍💻", "1f469-200d-1f4bb"),          # ZWJ sequence
    ("🇳🇴", "1f1f3-1f1f4"),                # flag
    ("👍🏽", "1f44d-1f3fd"),                # skin tone
])
def test_twemoji_code(seq, code):
    assert build.twemoji_code(seq) == code


def test_split_emoji_leaves_plain_symbols_as_text():
    assert build.split_emoji("Vaffelfest 🧇 © Hi5") == [
        ("text", "Vaffelfest "), ("emoji", "🧇"), ("text", " © Hi5"),
    ]


def test_title_parts():
    src = lambda seq: f"data/emoji/{build.twemoji_code(seq)}.svg"  # noqa: E731
    assert build.title_parts("Allmøte", src) is None
    assert build.title_parts("🧇 Vaffelfest", src) == [
        {"emoji": "🧇", "src": "data/emoji/1f9c7.svg"}, {"text": " Vaffelfest"},
    ]
    # Download failed: keep the character as text.
    assert build.title_parts("Vaffel 🧇", lambda seq: None) == [{"text": "Vaffel "}, {"text": "🧇"}]


def test_emoji_store_downloads_once(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    url = build.TWEMOJI_URL.format(code="1f9c7")
    session = FakeSession({url: (svg, "image/svg+xml")})
    store = build.EmojiStore(session, tmp_path / "data")
    assert store("🧇") == "data/emoji/1f9c7.svg"
    assert store("🧇") == "data/emoji/1f9c7.svg"
    assert session.calls == [url]
    assert store.used == {"1f9c7.svg"}


def test_qr_is_svg(tmp_path):
    data = tmp_path / "data"
    rel = build.save_qr("https://example.org/x", data)
    svg = (tmp_path / rel).read_text()
    assert svg.startswith("<svg") and "viewBox" in svg
