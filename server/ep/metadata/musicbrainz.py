"""MusicBrainz web service client (JSON, ``/ws/2``) and Cover Art Archive covers.

Requests are spaced at least one second apart across the whole process (MusicBrainz
rate limit), sent with a descriptive User-Agent, and retried on ``503`` (which
MusicBrainz also uses for "rate limited"). Responses are parsed into Pydantic models
that keep only what EP uses.

Usage::

    with MusicBrainz() as mb:
        release = mb.get_release(mbid)
        releases = mb.lookup_url("https://artist.bandcamp.com/album/title")
"""

import re
import threading
import time
import uuid
from importlib.metadata import version
from types import TracebackType
from typing import Annotated, Literal, Self

import httpx2
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

API_URL = "https://musicbrainz.org/ws/2"
COVER_ART_URL = "https://coverartarchive.org"
# MusicBrainz asks for contact details: "App/version ( contact )".
USER_AGENT = f"EP/{version('ep')} ( leonh@ndreke.de )"
# Seconds between two MusicBrainz requests (https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting).
REQUEST_INTERVAL = 1.0
# Retries after a 503, waiting Retry-After seconds, or RETRY_BACKOFF * 2**attempt.
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0
TIMEOUT = 30.0

# Includes for a full release lookup: tracks with their recordings, credits and ISRCs.
RELEASE_INCLUDES = ("recordings", "artist-credits", "release-groups", "isrcs")


class MusicBrainzError(Exception):
    """MusicBrainz could not be reached or returned an unexpected response."""


class MusicBrainzNotFound(MusicBrainzError):
    """The entity does not exist in MusicBrainz."""


# Response models. Field names follow the JSON (kebab-case via the alias generator);
# unknown fields are ignored.


class MBModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda name: name.replace("_", "-"),
        populate_by_name=True,
        frozen=True,
    )


class MBArtist(MBModel):
    id: uuid.UUID
    name: str
    sort_name: str
    disambiguation: str = ""
    # Person, Group, Orchestra, Choir, Character, Other, or unknown.
    type: str | None = None
    # Search results only: relevance 0-100.
    score: int | None = None


class MBArtistCreditPart(MBModel):
    # The name as credited, e.g. "The London Symphony Orchestra".
    name: str
    joinphrase: str = ""
    artist: MBArtist


def render_artist_credit(parts: list[MBArtistCreditPart]) -> str:
    return "".join(part.name + part.joinphrase for part in parts)


# MusicBrainz sends "" for an unknown date or barcode.
EmptyIsNone = Annotated[str | None, BeforeValidator(lambda value: value or None)]


class MBReleaseGroup(MBModel):
    id: uuid.UUID
    title: str
    disambiguation: str = ""
    # Album, Single, EP, Broadcast, Other, or none.
    primary_type: str | None = None
    # Compilation, Live, Remix, Soundtrack, ...
    secondary_types: list[str] = Field(default_factory=list)
    # Partial date YYYY[-MM[-DD]].
    first_release_date: EmptyIsNone = None
    artist_credit: list[MBArtistCreditPart] = Field(default_factory=list)
    score: int | None = None


class MBRecording(MBModel):
    id: uuid.UUID
    title: str
    disambiguation: str = ""
    # Milliseconds.
    length: int | None = None
    artist_credit: list[MBArtistCreditPart] = Field(default_factory=list)
    isrcs: list[str] = Field(default_factory=list)
    first_release_date: EmptyIsNone = None
    score: int | None = None


class MBTrack(MBModel):
    id: uuid.UUID
    # 1-based position on the medium; ``number`` is the printed number ("A1", "3").
    position: int
    number: str = ""
    title: str
    length: int | None = None
    artist_credit: list[MBArtistCreditPart] = Field(default_factory=list)
    recording: MBRecording


class MBMedium(MBModel):
    # 1-based disc number (absent in search results, which list only format and count).
    position: int | None = None
    format: str | None = None
    title: str = ""
    track_count: int = 0
    # Lookups only: the index of the first track in ``tracks`` (0: all of them).
    track_offset: int = 0
    tracks: list[MBTrack] = Field(default_factory=list)


class MBRelease(MBModel):
    id: uuid.UUID
    title: str
    disambiguation: str = ""
    status: str | None = None
    date: EmptyIsNone = None
    country: str | None = None
    barcode: EmptyIsNone = None
    artist_credit: list[MBArtistCreditPart] = Field(default_factory=list)
    release_group: MBReleaseGroup | None = None
    media: list[MBMedium] = Field(default_factory=list)
    score: int | None = None


class MBRelation(MBModel):
    type: str
    target_type: str
    release: MBRelease | None = None


class MBUrl(MBModel):
    id: uuid.UUID
    resource: str
    relations: list[MBRelation] = Field(default_factory=list)


class _ArtistSearch(MBModel):
    artists: list[MBArtist]


class _ReleaseSearch(MBModel):
    releases: list[MBRelease]


class _RecordingSearch(MBModel):
    recordings: list[MBRecording]


class _IsrcLookup(MBModel):
    recordings: list[MBRecording]


# Search queries

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def escape_lucene(text: str) -> str:
    """Escape Lucene query syntax, so that text is searched for as plain words."""
    return _LUCENE_SPECIAL.sub(r"\\\1", text)


def lucene_query(text: str | None = None, **fields: str | None) -> str:
    """A search query: free ``text`` plus ``field:"value"`` terms, all required.

    ``lucene_query(recording="Sinnerman", artist="Nina Simone")`` ->
    ``recording:"Sinnerman" AND artist:"Nina Simone"``. None values are left out.
    """
    terms = [escape_lucene(text)] if text else []
    for field, value in fields.items():
        if value:
            quoted = value.replace("\\", "\\\\").replace('"', '\\"')
            # Python names for MusicBrainz fields: release_group -> releasegroup.
            terms.append(f'{field.replace("_", "")}:"{quoted}"')
    if not terms:
        raise ValueError("empty search query")
    return " AND ".join(terms)


# Rate limiting


class RateLimiter:
    """Spaces calls to ``wait`` at least ``interval`` seconds apart, across threads."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._next > now:
                time.sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


# One limiter per process: the MusicBrainz limit is per client IP, not per connection.
LIMITER = RateLimiter(REQUEST_INTERVAL)


class MusicBrainz:
    """A MusicBrainz client. Use as a context manager, or call ``close``."""

    def __init__(
        self,
        *,
        transport: httpx2.BaseTransport | None = None,
        limiter: RateLimiter = LIMITER,
        retry_backoff: float = RETRY_BACKOFF,
    ) -> None:
        self._http = httpx2.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
            follow_redirects=True,
            transport=transport,
        )
        self._limiter = limiter
        self._retry_backoff = retry_backoff

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **params: str | int) -> bytes:
        """GET ``/ws/2/{path}`` as JSON; raises MusicBrainzNotFound on 404."""
        url = f"{API_URL}/{path}"
        for attempt in range(MAX_RETRIES + 1):
            self._limiter.wait()
            try:
                response = self._http.get(url, params={**params, "fmt": "json"})
            except httpx2.HTTPError as e:
                raise MusicBrainzError(f"MusicBrainz request failed: {e}") from e
            if response.status_code == 503 and attempt < MAX_RETRIES:
                time.sleep(self._retry_delay(response, attempt))
                continue
            if response.status_code == 404:
                raise MusicBrainzNotFound(f"not found in MusicBrainz: {path}")
            if response.status_code != 200:
                raise MusicBrainzError(
                    f"MusicBrainz returned {response.status_code} for {path}: {response.text[:200]}"
                )
            return response.content
        raise AssertionError("unreachable")

    def _retry_delay(self, response: httpx2.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return min(float(retry_after), 60.0)
        return self._retry_backoff * 2**attempt

    # Lookups by MBID

    def get_artist(self, mbid: uuid.UUID) -> MBArtist:
        return MBArtist.model_validate_json(self._get(f"artist/{mbid}"))

    def get_release(self, mbid: uuid.UUID) -> MBRelease:
        """A release with its release group, media, tracks, recordings, credits and ISRCs.

        A lookup returns every track (checked with a 2551-track release, 2026-11); should
        MusicBrainz ever page them, this fails rather than import a partial release.
        """
        content = self._get(f"release/{mbid}", inc="+".join(RELEASE_INCLUDES))
        release = MBRelease.model_validate_json(content)
        for medium in release.media:
            if medium.track_offset or len(medium.tracks) != medium.track_count:
                raise MusicBrainzError(
                    f"release {mbid}: medium {medium.position} has {medium.track_count} tracks, "
                    f"got {len(medium.tracks)} from offset {medium.track_offset}"
                )
        return release

    def get_release_group(self, mbid: uuid.UUID) -> MBReleaseGroup:
        content = self._get(f"release-group/{mbid}", inc="artist-credits")
        return MBReleaseGroup.model_validate_json(content)

    def get_recording(self, mbid: uuid.UUID) -> MBRecording:
        content = self._get(f"recording/{mbid}", inc="artist-credits+isrcs")
        return MBRecording.model_validate_json(content)

    # Lookups by other identifiers

    def lookup_url(self, url: str) -> list[MBRelease]:
        """Releases linked to a URL (e.g. a Bandcamp album page); empty if MB does not know it.

        The releases carry only basic fields: use ``get_release`` for tracks.
        """
        try:
            content = self._get("url", resource=url, inc="release-rels")
        except MusicBrainzNotFound:
            return []
        relations = MBUrl.model_validate_json(content).relations
        releases = {rel.release.id: rel.release for rel in relations if rel.release}
        return list(releases.values())

    def releases_by_barcode(self, barcode: str) -> list[MBRelease]:
        return self.search_releases(lucene_query(barcode=barcode))

    def recordings_by_isrc(self, isrc: str) -> list[MBRecording]:
        try:
            content = self._get(f"isrc/{normalize_isrc(isrc)}", inc="artist-credits")
        except MusicBrainzNotFound:
            return []
        return _IsrcLookup.model_validate_json(content).recordings

    # Search (Lucene query syntax; see ``lucene_query``)

    def search_artists(self, query: str, limit: int = 25) -> list[MBArtist]:
        content = self._get("artist", query=query, limit=limit)
        return _ArtistSearch.model_validate_json(content).artists

    def search_releases(self, query: str, limit: int = 25) -> list[MBRelease]:
        content = self._get("release", query=query, limit=limit)
        return _ReleaseSearch.model_validate_json(content).releases

    def search_recordings(self, query: str, limit: int = 25) -> list[MBRecording]:
        content = self._get("recording", query=query, limit=limit)
        return _RecordingSearch.model_validate_json(content).recordings

    # Cover Art Archive (not rate limited like MusicBrainz)

    def front_cover(
        self,
        entity: Literal["release", "release-group"],
        mbid: uuid.UUID,
        size: Literal[250, 500, 1200] | None = 500,
    ) -> bytes | None:
        """The front cover image (JPEG or PNG) of a release or release group, or None.

        ``size`` is the thumbnail width; None fetches the original image.
        """
        url = f"{COVER_ART_URL}/{entity}/{mbid}/front" + (f"-{size}" if size else "")
        try:
            response = self._http.get(url)
        except httpx2.HTTPError as e:
            raise MusicBrainzError(f"Cover Art Archive request failed: {e}") from e
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise MusicBrainzError(f"Cover Art Archive returned {response.status_code} for {url}")
        return response.content


def normalize_isrc(isrc: str) -> str:
    """ISRCs are 12 characters; tags often add hyphens or spaces (``US-PR3-65-00123``)."""
    return re.sub(r"[\s-]", "", isrc).upper()
