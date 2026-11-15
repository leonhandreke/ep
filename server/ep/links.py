"""What a URL is: the sites EP knows, their kinds of page, and the import that takes
them (``GET /links/classify``, the kind of an import job's input)."""

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from ep.metadata import bandcamp, nts
from ep.metadata.ytdlp import YouTubeKind, youtube_url
from ep.models import ImportKind, SourceKind


class LinkKind(StrEnum):
    bandcamp_album = "bandcamp_album"
    bandcamp_track = "bandcamp_track"
    youtube_video = "youtube_video"
    youtube_playlist = "youtube_playlist"
    nts_episode = "nts_episode"
    soundcloud = "soundcloud"
    spotify = "spotify"


@dataclass(frozen=True)
class LinkInfo:
    link_kind: LinkKind
    source_kind: SourceKind
    import_kind: ImportKind | None
    # Canonical: what imports and sources store.
    url: str


def classify(url: str) -> LinkInfo | None:
    """What ``url`` is, or None for a URL EP does not know."""
    if canonical := bandcamp.canonical_url(url):
        kind = LinkKind.bandcamp_track if "/track/" in canonical else LinkKind.bandcamp_album
        return LinkInfo(kind, SourceKind.bandcamp, ImportKind.bandcamp, canonical)
    if parsed := youtube_url(url):
        kind = (
            LinkKind.youtube_video
            if parsed.kind is YouTubeKind.video
            else LinkKind.youtube_playlist
        )
        return LinkInfo(kind, SourceKind.youtube, ImportKind.youtube, parsed.url)
    if ref := nts.episode_ref(url):
        return LinkInfo(LinkKind.nts_episode, SourceKind.nts, ImportKind.nts, ref.url)
    if canonical := nts.soundcloud_url(url):
        return LinkInfo(LinkKind.soundcloud, SourceKind.soundcloud, None, canonical)
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme == "https" and (parts.hostname or "").lower() == "open.spotify.com":
        path = parts.path.rstrip("/")
        return LinkInfo(
            LinkKind.spotify, SourceKind.spotify, None, f"https://open.spotify.com{path}"
        )
    return None
