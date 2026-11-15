import json
import time
import uuid

import httpx2
import pytest

from ep.metadata.musicbrainz import (
    USER_AGENT,
    MusicBrainz,
    MusicBrainzError,
    MusicBrainzNotFound,
    RateLimiter,
    escape_lucene,
    lucene_query,
    normalize_isrc,
    render_artist_credit,
)
from tests.mock_musicbrainz import (
    BIG_FUN_RELEASE,
    FLOATING_POINTS,
    MOVEMENT_1,
    MOVEMENT_1_ISRC,
    PROMISES_BANDCAMP,
    PROMISES_RELEASE,
    PROMISES_RELEASE_GROUP,
    MockMusicBrainz,
    fixture,
)


@pytest.fixture
def mb() -> MockMusicBrainz:
    return MockMusicBrainz()


def test_get_release_with_tracks_credits_and_isrcs(mb: MockMusicBrainz) -> None:
    release = mb.get_release(uuid.UUID(PROMISES_RELEASE))
    assert release.title == "Promises"
    assert release.date == "2021-03-26"
    assert release.barcode == "680899009720"
    assert render_artist_credit(release.artist_credit) == (
        "Floating Points, Pharoah Sanders & The London Symphony Orchestra"
    )
    assert release.artist_credit[2].artist.name == "London Symphony Orchestra"
    assert release.artist_credit[2].artist.type == "Orchestra"
    assert release.release_group is not None
    assert str(release.release_group.id) == PROMISES_RELEASE_GROUP
    assert release.release_group.primary_type == "Album"
    [medium] = release.media
    assert medium.position == 1
    assert len(medium.tracks) == 9
    track = medium.tracks[0]
    assert track.position == 1
    assert track.recording.title == "Promises: Movement 1"
    assert track.recording.length == 384151
    assert track.recording.isrcs == [MOVEMENT_1_ISRC]

    [request] = mb.requests
    assert request.url.path == f"/ws/2/release/{PROMISES_RELEASE}"
    assert request.url.params["inc"] == "recordings+artist-credits+release-groups+isrcs+genres"
    assert request.url.params["fmt"] == "json"
    assert request.headers["User-Agent"] == USER_AGENT


def test_user_agent_has_app_version_and_contact() -> None:
    assert USER_AGENT == "EP/0.1.0 ( leonh@ndreke.de )"


def test_multi_disc_release(mb: MockMusicBrainz) -> None:
    release = mb.get_release(uuid.UUID(BIG_FUN_RELEASE))
    assert [medium.position for medium in release.media] == [1, 2]
    assert [len(medium.tracks) for medium in release.media] == [4, 4]
    assert release.release_group is not None
    assert release.release_group.first_release_date == "1974-04-19"


def test_other_lookups(mb: MockMusicBrainz) -> None:
    artist = mb.get_artist(uuid.UUID(FLOATING_POINTS))
    assert (artist.name, artist.type) == ("Floating Points", "Person")
    release_group = mb.get_release_group(uuid.UUID(PROMISES_RELEASE_GROUP))
    assert release_group.title == "Promises"
    assert len(release_group.artist_credit) == 3
    recording = mb.get_recording(uuid.UUID(MOVEMENT_1))
    assert recording.isrcs == [MOVEMENT_1_ISRC]
    assert [r.title for r in mb.recordings_by_isrc("us-lb1-20-10001")] == ["Promises: Movement 1"]
    assert mb.requests[-1].url.path == f"/ws/2/isrc/{MOVEMENT_1_ISRC}"


def test_lookup_url_resolves_bandcamp_page_to_release(mb: MockMusicBrainz) -> None:
    [release] = mb.lookup_url(PROMISES_BANDCAMP)  # two relations, one release
    assert str(release.id) == PROMISES_RELEASE
    assert mb.requests[0].url.params["inc"] == "release-rels"
    assert mb.lookup_url("https://example.bandcamp.com/album/unknown") == []


def test_barcode_and_searches(mb: MockMusicBrainz) -> None:
    releases = mb.releases_by_barcode("680899009720")
    # A UPC (12 digits) is also written as an EAN-13 with a leading zero.
    assert {r.barcode for r in releases} == {"680899009720", "0680899009720"}
    assert mb.requests[-1].url.params["query"] == 'barcode:"680899009720"'
    artists = mb.search_artists("bjork", limit=3)
    assert artists[0].name == "Björk"
    assert artists[0].score == 100
    assert mb.requests[-1].url.params["limit"] == "3"
    [promises, *_] = mb.search_releases(lucene_query(release="promises"))
    assert promises.title == "Promises"
    recordings = mb.search_recordings(lucene_query(recording="movement 1"))
    assert recordings[0].title == "Movement 1"
    assert recordings[0].artist_credit[1].joinphrase == " feat. "


def test_not_found(mb: MockMusicBrainz) -> None:
    with pytest.raises(MusicBrainzNotFound):
        mb.get_release(uuid.uuid4())
    assert mb.recordings_by_isrc("XX0000000000") == []


def _client(*responses: httpx2.Response) -> tuple[MusicBrainz, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []
    queue = list(responses)

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return queue.pop(0)

    client = MusicBrainz(
        transport=httpx2.MockTransport(handle), limiter=RateLimiter(0), retry_backoff=0
    )
    return client, requests


def test_retries_on_503() -> None:
    busy = httpx2.Response(503, json={"error": "busy"})
    artist = {"id": FLOATING_POINTS, "name": "Floating Points", "sort-name": "Floating Points"}
    client, requests = _client(busy, busy, httpx2.Response(200, json=artist))
    assert client.get_artist(uuid.UUID(FLOATING_POINTS)).name == "Floating Points"
    assert len(requests) == 3


def test_gives_up_after_retries() -> None:
    client, requests = _client(*[httpx2.Response(503, json={"error": "busy"})] * 4)
    with pytest.raises(MusicBrainzError, match="503"):
        client.get_artist(uuid.uuid4())
    assert len(requests) == 4


def test_other_errors_are_not_retried() -> None:
    client, requests = _client(httpx2.Response(400, json={"error": "bad query"}))
    with pytest.raises(MusicBrainzError, match="400"):
        client.search_artists("(")
    assert len(requests) == 1


def test_rate_limiter_spaces_requests() -> None:
    limiter = RateLimiter(0.05)
    start = time.monotonic()
    for _ in range(3):
        limiter.wait()
    assert time.monotonic() - start >= 0.1


def test_partial_release_fails() -> None:
    release = json.loads(fixture("release_promises"))
    release["media"][0]["tracks"] = release["media"][0]["tracks"][:5]
    client, _ = _client(httpx2.Response(200, json=release))
    with pytest.raises(MusicBrainzError, match="has 9 tracks, got 5"):
        client.get_release(uuid.UUID(PROMISES_RELEASE))


def test_error_body_is_truncated() -> None:
    client, _ = _client(httpx2.Response(500, text="<html>" + "x" * 10_000))
    with pytest.raises(MusicBrainzError) as error:
        client.get_artist(uuid.uuid4())
    assert len(str(error.value)) < 300


def test_front_cover() -> None:
    image = b"\xff\xd8\xff fake jpeg"
    mbid = uuid.UUID(PROMISES_RELEASE)
    redirect = httpx2.Response(307, headers={"Location": "https://archive.org/cover.jpg"})
    client, requests = _client(redirect, httpx2.Response(200, content=image))
    assert client.front_cover("release", mbid) == image
    assert str(requests[0].url) == f"https://coverartarchive.org/release/{mbid}/front-500"
    client, requests = _client(httpx2.Response(404))
    assert client.front_cover("release-group", mbid, size=None) is None
    assert str(requests[0].url) == f"https://coverartarchive.org/release-group/{mbid}/front"


def test_lucene() -> None:
    assert escape_lucene('AC/DC: "Back" (in) black!') == r"AC\/DC\: \"Back\" \(in\) black\!"
    assert lucene_query("x", recording='Say "Hi"', artist=None, release_group="Y") == (
        r'x AND recording:"Say \"Hi\"" AND releasegroup:"Y"'
    )
    with pytest.raises(ValueError):
        lucene_query(artist=None)


def test_normalize_isrc() -> None:
    assert normalize_isrc(" us-pr3-65-00123 ") == "USPR36500123"


# Live MusicBrainz (opt-in: pytest -m network)


@pytest.mark.network
def test_live_release_and_url_lookup() -> None:
    with MusicBrainz() as mb:
        release = mb.get_release(uuid.UUID(PROMISES_RELEASE))
        assert release.title == "Promises"
        assert sum(len(medium.tracks) for medium in release.media) == 9
        assert PROMISES_RELEASE in {str(r.id) for r in mb.lookup_url(PROMISES_BANDCAMP)}
        assert mb.search_artists("bjork", limit=1)[0].name == "Björk"
        assert [r.id for r in mb.recordings_by_isrc(MOVEMENT_1_ISRC)] == [uuid.UUID(MOVEMENT_1)]


@pytest.mark.network
def test_live_front_cover() -> None:
    with MusicBrainz() as mb:
        cover = mb.front_cover("release-group", uuid.UUID(PROMISES_RELEASE_GROUP), size=250)
    assert cover is not None
    assert cover[:3] == b"\xff\xd8\xff" or cover[:4] == b"\x89PNG"
