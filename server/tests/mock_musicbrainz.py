"""A MusicBrainz client that answers from saved responses in ``fixtures/musicbrainz``.

The fixtures are real responses, recorded once (minified):

- ``release_promises``: Floating Points, Pharoah Sanders & The London Symphony
  Orchestra, "Promises" (9 tracks, three-artist credit, ISRCs)
- ``release_big_fun``: Miles Davis, "Big Fun" (2 CDs, a track title that differs from
  its recording title)
- ``url_promises``: the Bandcamp page of "Promises" with its release relations
- lookups of an artist, release group, recording and ISRC, a barcode search, and
  searches for artists ("bjork"), releases and recordings
- ``search_release_promises_tracks``: a release search by title, artist and track count
  (``release:"Promises" AND artist:"Floating Points" AND tracks:"9"``), answering every
  such search mentioning "promises"; ``search_release_empty`` (no hits) answers the
  others
"""

import json
from pathlib import Path

import httpx2

from ep.metadata.musicbrainz import MusicBrainz, RateLimiter

FIXTURES = Path(__file__).parent / "fixtures" / "musicbrainz"

PROMISES_RELEASE = "f27dba9d-a46e-4f41-80ac-8e43d27e1152"
PROMISES_RELEASE_GROUP = "970f689d-1778-4953-b3ed-48d057625724"
PROMISES_BANDCAMP = "https://floatingpoints.bandcamp.com/album/promises"
BIG_FUN_RELEASE = "a6549c4e-6b9a-4bfa-8786-15705082a136"
FLOATING_POINTS = "69d9c5ba-7bba-4cb7-ab32-8ccc48ad4f97"
MOVEMENT_1 = "7a98380f-91e0-4ba4-8fcb-ded1dc0c8525"
MOVEMENT_1_ISRC = "USLB12010001"

LOOKUPS = {
    f"/ws/2/release/{PROMISES_RELEASE}": "release_promises",
    f"/ws/2/release/{BIG_FUN_RELEASE}": "release_big_fun",
    f"/ws/2/artist/{FLOATING_POINTS}": "artist_floating_points",
    f"/ws/2/release-group/{PROMISES_RELEASE_GROUP}": "release_group_promises",
    f"/ws/2/recording/{MOVEMENT_1}": "recording_movement_1",
    f"/ws/2/isrc/{MOVEMENT_1_ISRC}": "isrc_movement_1",
    "/ws/2/artist": "search_artist_bjork",
    "/ws/2/recording": "search_recording_movement",
}

NOT_FOUND = {
    "error": "Not Found",
    "help": "For usage, please see: https://musicbrainz.org/development/mmd",
}


def fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def fixture_name(request: httpx2.Request) -> str | None:
    path, params = request.url.path, request.url.params
    if path == "/ws/2/url":
        return "url_promises" if params.get("resource") == PROMISES_BANDCAMP else None
    if path == "/ws/2/release":
        query = params["query"]
        if "barcode:" in query:
            return "barcode_promises"
        if "tracks:" in query:  # the folder importer's search
            return (
                "search_release_promises_tracks"
                if "promises" in query.lower()
                else ("search_release_empty")
            )
        return "search_release_promises"
    return LOOKUPS.get(path)


class MockMusicBrainz(MusicBrainz):
    """Serves the fixtures; ``requests`` records what was asked."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        super().__init__(
            transport=httpx2.MockTransport(self._handle),
            limiter=RateLimiter(0),
            retry_backoff=0,
        )

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if (name := fixture_name(request)) is None:
            return httpx2.Response(404, content=json.dumps(NOT_FOUND))
        return httpx2.Response(200, content=fixture(name))
