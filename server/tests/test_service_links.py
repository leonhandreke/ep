"""Links of artists and release groups to other services (``ep.service_links``), from
saved MusicBrainz and Wikidata responses."""

import uuid
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from ep.api.deps import get_musicbrainz, get_wikidata
from ep.main import app
from ep.metadata.musicbrainz import MBUrlRelation, MBUrlTarget, MusicBrainz, RateLimiter
from ep.metadata.wikidata import Wikidata, wikipedia_article
from ep.service_links import LinkService, classify, clear_caches
from tests.fakes import wikidata_transport
from tests.helpers import make_artist, make_release_group
from tests.mock_musicbrainz import (
    FLOATING_POINTS,
    PROMISES_RELEASE,
    PROMISES_RELEASE_GROUP,
    MockMusicBrainz,
)

FLOATING_POINTS_LINKS = [
    ("wikipedia", "https://en.wikipedia.org/wiki/Floating_Points", "en"),
    ("bandcamp", "https://floatingpoints.bandcamp.com/", None),
    ("spotify", "https://open.spotify.com/artist/2AR42Ur9PcchQDtEdwkv4L", None),
    ("musicbrainz", f"https://musicbrainz.org/artist/{FLOATING_POINTS}", None),
]


class Services:
    """The MusicBrainz and Wikidata the endpoints get; either can be made unreachable."""

    def __init__(self) -> None:
        self.musicbrainz = MockMusicBrainz()
        self.wikidata_requests: list[str] = []
        self.musicbrainz_down = self.wikidata_down = False

    def get_musicbrainz(self) -> MusicBrainz:
        if self.musicbrainz_down:
            return MusicBrainz(
                transport=httpx2.MockTransport(down), limiter=RateLimiter(0), retry_backoff=0
            )
        return self.musicbrainz

    def get_wikidata(self) -> Wikidata:
        transport = (
            httpx2.MockTransport(down)
            if self.wikidata_down
            else wikidata_transport(self.wikidata_requests)
        )
        return Wikidata(transport=transport, limiter=RateLimiter(0))


def down(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("unreachable", request=request)


@pytest.fixture
def services(client: TestClient) -> Iterator[Services]:
    clear_caches()
    services = Services()
    app.dependency_overrides[get_musicbrainz] = services.get_musicbrainz
    app.dependency_overrides[get_wikidata] = services.get_wikidata
    yield services
    del app.dependency_overrides[get_musicbrainz], app.dependency_overrides[get_wikidata]
    clear_caches()


def links(client: TestClient, path: str) -> list[tuple[str, str, str | None]]:
    response = client.get(path)
    assert response.status_code == 200, response.text
    return [(link["service"], link["url"], link["label"]) for link in response.json()]


def test_artist_links(client: TestClient, services: Services) -> None:
    artist = make_artist(client, "Floating Points", mbid=FLOATING_POINTS)
    path = f"/artists/{artist['id']}/links"
    # The four services in order, one each; the homepage, Apple Music, YouTube, Discogs,
    # AllMusic, ... are left out.
    assert links(client, path) == FLOATING_POINTS_LINKS
    assert [r.url.params["inc"] for r in services.musicbrainz.requests] == ["url-rels"]
    assert len(services.wikidata_requests) == 1
    assert "props=sitelinks%2Furls" in services.wikidata_requests[0]

    # Cached: asked neither again.
    assert links(client, path) == FLOATING_POINTS_LINKS
    assert (len(services.musicbrainz.requests), len(services.wikidata_requests)) == (1, 1)


def test_release_group_links(client: TestClient, services: Services) -> None:
    artist = make_artist(client, "Floating Points", mbid=FLOATING_POINTS)
    album = make_release_group(
        client, artist, "Promises", mbid=PROMISES_RELEASE_GROUP, release_mbid=PROMISES_RELEASE
    )
    # Wikipedia from the release group's Wikidata item, Bandcamp from the release; not
    # the release group's Discogs and AllMusic.
    assert links(client, f"/release-groups/{album['id']}/links") == [
        (
            "wikipedia",
            "https://en.wikipedia.org/wiki/Promises_(Floating_Points,_Pharoah_Sanders_and_the_"
            "London_Symphony_Orchestra_album)",
            "en",
        ),
        ("bandcamp", "https://floatingpoints.bandcamp.com/album/promises", None),
        ("musicbrainz", f"https://musicbrainz.org/release-group/{PROMISES_RELEASE_GROUP}", None),
    ]
    assert [r.url.path.split("/")[3] for r in services.musicbrainz.requests] == [
        "release-group",
        "release",
    ]


def test_links_without_mbid_or_unknown(client: TestClient, services: Services) -> None:
    artist = make_artist(client)
    assert client.get(f"/artists/{artist['id']}/links").json() == []
    album = make_release_group(client, artist)
    assert client.get(f"/release-groups/{album['id']}/links").json() == []
    assert client.get(f"/artists/{uuid.uuid4()}/links").status_code == 404
    assert client.get(f"/release-groups/{uuid.uuid4()}/links").status_code == 404
    assert services.musicbrainz.requests == []

    # An MBID MusicBrainz does not know (any more): only the MusicBrainz link.
    gone = str(uuid.uuid4())
    unknown = make_artist(client, "Gone", mbid=gone)
    assert links(client, f"/artists/{unknown['id']}/links") == [
        ("musicbrainz", f"https://musicbrainz.org/artist/{gone}", None)
    ]


def test_musicbrainz_unreachable(client: TestClient, services: Services) -> None:
    artist = make_artist(client, "Floating Points", mbid=FLOATING_POINTS)
    path = f"/artists/{artist['id']}/links"
    services.musicbrainz_down = True
    response = client.get(path)
    assert response.status_code == 502
    assert "MusicBrainz request failed" in response.json()["detail"]
    # Not cached: asked again once it is back.
    services.musicbrainz_down = False
    assert links(client, path) == FLOATING_POINTS_LINKS


def test_wikidata_unreachable(client: TestClient, services: Services) -> None:
    artist = make_artist(client, "Floating Points", mbid=FLOATING_POINTS)
    path = f"/artists/{artist['id']}/links"
    services.wikidata_down = True
    assert links(client, path) == FLOATING_POINTS_LINKS[1:]  # all but Wikipedia
    # Not cached: Wikipedia is there once Wikidata is back (MusicBrainz is not asked again).
    services.wikidata_down = False
    assert links(client, path) == FLOATING_POINTS_LINKS
    assert len(services.musicbrainz.requests) == 1


def relation(type: str, url: str, **fields: Any) -> MBUrlRelation:
    return MBUrlRelation(type=type, url=MBUrlTarget(resource=url), **fields)


@pytest.mark.parametrize(
    ("type", "url", "service"),
    [
        ("official homepage", "https://artist.bandcamp.com/", LinkService.bandcamp),
        ("official homepage", "https://artist.example/", None),
        ("bandcamp", "https://artist.bandcamp.com/", LinkService.bandcamp),
        ("purchase for download", "https://artist.bandcamp.com/album/x", LinkService.bandcamp),
        ("free streaming", "https://open.spotify.com/album/x", LinkService.spotify),
        ("free streaming", "https://www.deezer.com/album/1", None),
        ("streaming", "https://music.apple.com/gb/album/1", None),
        ("youtube", "https://www.youtube.com/channel/x", None),
        ("discogs", "https://www.discogs.com/master/1", None),
        ("wikipedia", "https://de.wikipedia.org/wiki/X", LinkService.wikipedia),
        ("allmusic", "https://www.allmusic.com/album/x", None),
        ("wikidata", "https://www.wikidata.org/wiki/Q1", None),
        ("social network", "https://twitter.com/x", None),
        ("other databases", "not a url", None),
    ],
)
def test_classify(type: str, url: str, service: LinkService | None) -> None:
    assert classify(relation(type, url)) == service


def test_wikipedia_article() -> None:
    sitelinks = {
        "commonswiki": "https://commons.wikimedia.org/wiki/Category:X",
        "enwikiquote": "https://en.wikiquote.org/wiki/X",
        "frwiki": "https://fr.wikipedia.org/wiki/X",
        "dewiki": "https://de.wikipedia.org/wiki/X",
    }
    # No English article: the first language by site id.
    assert wikipedia_article(sitelinks) == ("https://de.wikipedia.org/wiki/X", "de")
    sitelinks["enwiki"] = "https://en.wikipedia.org/wiki/X"
    assert wikipedia_article(sitelinks) == ("https://en.wikipedia.org/wiki/X", "en")
    assert wikipedia_article({"commonswiki": sitelinks["commonswiki"]}) is None


def test_direct_wikipedia_relationship_wins(client: TestClient, services: Services) -> None:
    """A ``wikipedia`` relationship is used as it is, without asking Wikidata."""
    from ep.service_links import _links

    found = _links(
        [
            relation("wikidata", "https://www.wikidata.org/wiki/Q22277802"),
            relation("wikipedia", "https://fr.wikipedia.org/wiki/X"),
            relation("bandcamp", "https://old.bandcamp.com/", ended=True),
        ],
        services.get_wikidata(),
        "https://musicbrainz.org/artist/x",
    )
    assert [(link.service, link.url, link.label) for link in found] == [
        (LinkService.wikipedia, "https://fr.wikipedia.org/wiki/X", "fr"),
        (LinkService.musicbrainz, "https://musicbrainz.org/artist/x", None),
    ]
    assert services.wikidata_requests == []
