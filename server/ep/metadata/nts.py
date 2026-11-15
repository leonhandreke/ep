"""NTS Radio: episodes with their tracklists, from NTS's public (undocumented) API.

- ``GET https://www.nts.live/api/v2/shows/{show}/episodes/{episode}``: the episode
  (name, description, broadcast time, location, genres, moods, images, audio sources
  such as a SoundCloud upload) with the tracklist embedded
  (``embeds.tracklist.results``);
- ``GET https://www.nts.live/api/v2/shows/{show}``: the show.

A tracklist entry has the artist and title as NTS shows them, NTS's own ``uid``, and,
where NTS's recognition found the track, ``offset`` and ``duration`` (seconds into the
episode) plus ``isrc_id``, ``deezer_track_id``, ``acr_id`` (ACRCloud) and
``musicbrainz_track_id``. Entries typed in by hand have ``offset_estimate`` and
``duration_estimate`` instead (``NtsTrack.estimate``), some have no time at all.

``musicbrainz_track_id`` is a MusicBrainz *recording* MBID despite its name (checked
2026-11 against MusicBrainz for the fixture episode: every one resolves as a recording
with that entry's title and artist, none as a track MBID).

The API is undocumented: responses are validated against the models here and anything
unexpected (a missing field, another type, a truncated tracklist) raises
``NtsUnexpectedResponse`` with the details, rather than importing half an episode.
Requests send the generic ``ep.metadata.web`` User-Agent, nothing personal.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Self
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from ep.metadata.web import WebError, WebNotFound, client, get_bytes

API = "https://www.nts.live/api/v2"
_ALIAS = r"[a-z0-9][a-z0-9-]*"
_EPISODE_PATH = re.compile(rf"^/shows/({_ALIAS})/episodes/({_ALIAS})/?$")
_SOUNDCLOUD_PATH = re.compile(r"^/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)/?$")
# SoundCloud paths that are not a user's track.
_SOUNDCLOUD_RESERVED = {"discover", "search", "stream", "upload", "you", "charts", "pages"}


class NtsError(Exception):
    """An episode or show could not be read from NTS."""


class NtsNotFound(NtsError):
    """The episode or show does not exist (any more)."""


class NtsUnreachable(NtsError):
    """NTS could not be reached or answered with an error (network, server error, a bot
    check): worth a retry."""


class NtsUnexpectedResponse(NtsError):
    """NTS answered with something EP does not understand: the undocumented API changed."""


# URLs


@dataclass(frozen=True)
class EpisodeRef:
    show: str
    episode: str

    @property
    def url(self) -> str:
        """The canonical episode page URL."""
        return f"https://www.nts.live/shows/{self.show}/episodes/{self.episode}"

    @property
    def api_url(self) -> str:
        return f"{API}/shows/{self.show}/episodes/{self.episode}"


def episode_ref(url: str) -> EpisodeRef | None:
    """Show and episode alias of an NTS episode page URL
    (``https://www.nts.live/shows/<show>/episodes/<episode>``, with or without ``www.``,
    query, fragment or trailing slash), else None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in (
        "nts.live",
        "www.nts.live",
    ):
        return None
    if (m := _EPISODE_PATH.match(parts.path)) is None:
        return None
    return EpisodeRef(m.group(1), m.group(2))


def soundcloud_url(url: str) -> str | None:
    """``https://soundcloud.com/<user>/<track>`` for a SoundCloud track URL (query such
    as NTS's ``utm_*`` parameters, fragment and trailing slash dropped), else None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in (
        "soundcloud.com",
        "www.soundcloud.com",
        "m.soundcloud.com",
    ):
        return None
    m = _SOUNDCLOUD_PATH.match(parts.path)
    if m is None or m.group(1) in _SOUNDCLOUD_RESERVED or m.group(2) in ("sets", "tracks"):
        return None
    return f"https://soundcloud.com/{m.group(1)}/{m.group(2)}"


# Models. Unknown fields are ignored; the fields used are validated.


class NtsModel(BaseModel):
    model_config = ConfigDict(frozen=True)


def _blank_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]


class NtsTrack(NtsModel):
    artist: str
    title: str
    uid: OptionalText = None
    # Seconds into the episode, as detected; null for entries typed in by hand.
    offset: float | None
    duration: float | None
    # The presenter's (or NTS's) estimate, for entries without a detected position.
    offset_estimate: float | None = None
    duration_estimate: float | None = None
    isrc_id: OptionalText = None
    deezer_track_id: int | None = None
    acr_id: OptionalText = None
    # A MusicBrainz recording MBID (see the module docstring).
    musicbrainz_track_id: Annotated[uuid.UUID | None, BeforeValidator(_blank_to_none)] = None

    @property
    def estimate(self) -> bool:
        """The time range is an estimate (no detected offset)."""
        return self.offset is None and self.offset_estimate is not None

    @property
    def start_ms(self) -> int | None:
        seconds = self.offset if self.offset is not None else self.offset_estimate
        return round(seconds * 1000) if seconds is not None else None

    @property
    def duration_ms(self) -> int | None:
        seconds = self.duration if self.offset is not None else self.duration_estimate
        return round(seconds * 1000) if seconds else None

    @property
    def detected_duration_ms(self) -> int | None:
        """How long the track was detected playing (not an estimate): the recording is
        at least this long."""
        return round(self.duration * 1000) if self.offset is not None and self.duration else None


class NtsResultSet(NtsModel):
    count: int


class NtsListMetadata(NtsModel):
    resultset: NtsResultSet


class NtsTracklist(NtsModel):
    metadata: NtsListMetadata
    results: list[NtsTrack]


class NtsEpisodeEmbeds(NtsModel):
    tracklist: NtsTracklist


class NtsLabelled(NtsModel):
    """A genre or mood."""

    id: str
    value: str


class NtsAudioSource(NtsModel):
    url: str
    # e.g. "soundcloud", "mixcloud".
    source: str


class NtsMedia(NtsModel):
    picture_large: str | None = None
    background_large: str | None = None

    @property
    def image_url(self) -> str | None:
        return self.picture_large or self.background_large


class NtsEpisode(NtsModel):
    name: str
    description: str | None = None
    show_alias: str
    episode_alias: str
    broadcast: datetime | None = None
    location_long: OptionalText = None
    genres: list[NtsLabelled] = Field(default_factory=list)
    moods: list[NtsLabelled] = Field(default_factory=list)
    audio_sources: list[NtsAudioSource] = Field(default_factory=list)
    media: NtsMedia = Field(default_factory=NtsMedia)
    embeds: NtsEpisodeEmbeds

    @property
    def tracklist(self) -> list[NtsTrack]:
        return self.embeds.tracklist.results

    @property
    def soundcloud_url(self) -> str | None:
        """The canonical URL of the episode's SoundCloud upload, if there is one."""
        for audio in self.audio_sources:
            if audio.source == "soundcloud" and (url := soundcloud_url(audio.url)):
                return url
        return None


class NtsShow(NtsModel):
    name: str
    description: str | None = None
    show_alias: str
    location_long: OptionalText = None
    media: NtsMedia = Field(default_factory=NtsMedia)


def parse_episode(data: bytes) -> NtsEpisode:
    """An episode from the API's JSON; ``NtsUnexpectedResponse`` if it does not have the
    expected shape, or the embedded tracklist is not complete."""
    episode = _parse(NtsEpisode, data, "episode")
    tracklist = episode.embeds.tracklist
    if tracklist.metadata.resultset.count != len(tracklist.results):
        raise NtsUnexpectedResponse(
            f"the embedded tracklist has {len(tracklist.results)} of "
            f"{tracklist.metadata.resultset.count} entries"
        )
    return episode


def parse_show(data: bytes) -> NtsShow:
    return _parse(NtsShow, data, "show")


def _parse[T: BaseModel](model: type[T], data: bytes, what: str) -> T:
    try:
        return model.model_validate_json(data)
    except ValidationError as e:
        details = "; ".join(
            f"{'.'.join(map(str, error['loc'])) or '(response)'}: {error['msg']}"
            for error in e.errors()[:5]
        )
        raise NtsUnexpectedResponse(f"unexpected NTS {what} response: {details}") from e


class Nts:
    """Reads NTS's API. Use as a context manager, or call ``close``."""

    def __init__(self, *, transport: httpx2.BaseTransport | None = None) -> None:
        self._http = client(transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _get(self, url: str) -> bytes:
        try:
            return get_bytes(self._http, url)
        except WebNotFound as e:
            raise NtsNotFound(str(e)) from e
        except WebError as e:
            raise NtsUnreachable(str(e)) from e

    def get_episode(self, ref: EpisodeRef) -> NtsEpisode:
        episode = parse_episode(self._get(ref.api_url))
        if (episode.show_alias, episode.episode_alias) != (ref.show, ref.episode):
            raise NtsUnexpectedResponse(
                f"asked for {ref.show}/{ref.episode}, NTS answered with "
                f"{episode.show_alias}/{episode.episode_alias}"
            )
        return episode

    def get_show(self, show: str) -> NtsShow:
        return parse_show(self._get(f"{API}/shows/{show}"))
