"""Bandcamp pages: the saved Knotwilg and Floating Points album pages, and variations."""

import html
import json
import re

import httpx2
import pytest

from ep.metadata.bandcamp import (
    Bandcamp,
    BandcampBlocked,
    BandcampError,
    BandcampNotFound,
    canonical_url,
    parse_release_page,
    simplify_name,
)
from tests.fakes import BANDCAMP, KNOTWILG, PROMISES, web_transport


def page(name: str) -> str:
    return (BANDCAMP / name).read_text()


def with_tralbum(text: str, change: dict) -> str:
    """The page with its ``data-tralbum`` JSON changed (keys merged, ``current`` too)."""
    m = re.search(r'data-tralbum="(.+?)"', text, re.DOTALL)
    assert m is not None
    data = json.loads(html.unescape(m.group(1)))
    current = {**data["current"], **change.pop("current", {})}
    data = {**data, **change, "current": current}
    return text[: m.start(1)] + html.escape(json.dumps(data)) + text[m.end(1) :]


def test_label_page_album_is_by_its_artist_not_the_page_owner() -> None:
    release = parse_release_page(page("knotwilg_leitmotiv-2.html"), KNOTWILG)
    assert release.artist == "Maxine Funke"
    assert release.band == "Knotwilg"
    assert release.label == "Knotwilg"
    assert release.title == "Leitmotiv"
    assert release.kind == "album"
    assert release.date == "2026-11-01"
    assert release.barcode is None
    assert release.cover_url == "https://f4.bcbits.com/img/a4266366245_0.jpg"
    assert release.tags == ["Folk", "experimental folk", "nzmusic", "singersongwriter", "Belgium"]
    assert [(t.number, t.title, t.length_ms) for t in release.tracks] == [
        (1, "Whole Country", 211000),
        (2, "I Know", 254465),
        (3, "Trash", 270103),
        (4, "They Wanna Live", 333306),
        (5, "Stormbird And The Burning Star", 550466),
    ]
    assert all(track.artist is None for track in release.tracks)
    assert release.tracks[0].url == "https://knotwilg.bandcamp.com/track/whole-country"
    assert release.hidden_tracks == 0


def test_artist_page_album() -> None:
    release = parse_release_page(page("floatingpoints_promises.html"), PROMISES)
    assert release.artist == "Floating Points, Pharoah Sanders & The London Symphony Orchestra"
    # The page owner is part of the credit: not a label; the physical label is.
    assert release.label == "Luaka Bop"
    assert release.date == "2021-03-26"
    # The download's UPC is the CD's: not the digital release's barcode (as in Harmony).
    assert release.barcode is None
    assert len(release.tracks) == 9
    assert release.tracks[8].title == "Movement 9"


def test_compilation_track_artists_and_titles() -> None:
    text = with_tralbum(
        page("knotwilg_leitmotiv-2.html"),
        {
            "artist": "Various Artists",
            "trackinfo": [
                {"track_num": 1, "title": "Maxine Funke - Trash", "artist": "Maxine Funke",
                 "duration": 270.1, "title_link": "/track/trash", "file": {"mp3-128": "x"}},
                {"track_num": 2, "title": "Untitled - Part 2", "artist": None,
                 "duration": 0.0, "title_link": None, "file": None},
            ],
        },
    )  # fmt: skip
    release = parse_release_page(text, KNOTWILG)
    first, second = release.tracks
    assert (first.artist, first.title, first.streamable) == ("Maxine Funke", "Trash", True)
    # Without a track artist, " - " is part of the title.
    assert (second.artist, second.title, second.url) == (None, "Untitled - Part 2", None)
    assert second.length_ms is None and not second.streamable


def test_barcode_date_and_hidden_tracks() -> None:
    text = with_tralbum(
        page("knotwilg_leitmotiv-2.html"),
        {"current": {"upc": "5050580000000", "release_date": "01 Jan 1975 00:00:00 GMT"}},
    )
    text = text.replace('content="5 track album"', 'content="7 track album"')
    release = parse_release_page(text, KNOTWILG)
    assert release.barcode == "5050580000000"
    # Before Bandcamp existed: the publish date.
    assert release.date == "2026-09-24"
    assert release.hidden_tracks == 2


def test_track_page_of_an_album() -> None:
    text = with_tralbum(
        page("knotwilg_leitmotiv-2.html"),
        {
            "item_type": "track",
            "album_url": "/album/leitmotiv-2",
            "current": {"type": "track", "isrc": "BEA012600001"},
        },
    )
    release = parse_release_page(text, "https://knotwilg.bandcamp.com/track/whole-country")
    assert release.kind == "track"
    assert release.album_url == KNOTWILG
    assert release.tracks[0].isrc == "BEA012600001"


def test_page_without_release_data() -> None:
    with pytest.raises(BandcampError, match="no embedded release data"):
        parse_release_page("<html></html>", KNOTWILG)
    challenge = '<html><link href="/_fs-ch-1T1wmsGaOgGaSxcX/assets/styles.css" /></html>'
    with pytest.raises(BandcampBlocked, match="bot check"):
        parse_release_page(challenge, KNOTWILG)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (KNOTWILG, KNOTWILG),
        ("http://Knotwilg.bandcamp.com/album/leitmotiv-2/?from=discover_page#x", KNOTWILG),
        ("https://zeug.bandcamp.com/track/yeltsa-kcir", "https://zeug.bandcamp.com/track/yeltsa-kcir"),
        ("https://knotwilg.bandcamp.com/", None),
        ("https://knotwilg.bandcamp.com/music", None),
        ("https://bandcamp.com/discover", None),
        ("https://example.com/album/x", None),
        ("not a url", None),
    ],
)  # fmt: skip
def test_canonical_url(url: str, expected: str | None) -> None:
    assert canonical_url(url) == expected


def test_simplify_name() -> None:
    assert simplify_name("Floating Points") == "floatingpoints"
    assert simplify_name("Björk & Co.") == "bjorkco"


def test_fetch_sends_no_contact_details() -> None:
    requests: list[httpx2.Request] = []
    transport = web_transport()

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return transport.handle_request(request)

    with Bandcamp(transport=httpx2.MockTransport(handle)) as bandcamp:
        release = bandcamp.get_release(KNOTWILG + "?from=discover_page")
        with pytest.raises(BandcampNotFound):
            bandcamp.get_release("https://knotwilg.bandcamp.com/album/gone")
    assert release.artist == "Maxine Funke"
    assert str(requests[0].url) == KNOTWILG
    user_agent = requests[0].headers["User-Agent"]
    assert user_agent.startswith("EP/") and "@" not in user_agent


@pytest.mark.network
def test_network_knotwilg_page() -> None:
    with Bandcamp() as bandcamp:
        release = bandcamp.get_release(KNOTWILG)
    assert release.artist == "Maxine Funke"
    assert release.label == "Knotwilg"
    assert len(release.tracks) == 5
