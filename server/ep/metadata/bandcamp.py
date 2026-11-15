"""Bandcamp album and track pages: release metadata from the embedded JSON.

A Python port of the Bandcamp provider of Harmony by David Kellner
(https://github.com/kellnerd/harmony, ``providers/Bandcamp/``, MIT License,
Copyright (c) 2022-2024 David Kellner). Ported: the ``data-tralbum`` and ``data-band``
attributes and ``og:description`` as the source of the release, the artist from the
release's own artist field (the page owner, "band", is often a label), the label
heuristics (a unique physical package label; else the band, unless its name is the
artist's or part of it), the barcode of the download release (dropped when it is a
physical package's), the release date (the publish date for dates before Bandcamp
existed), track artists with the "Artist - " prefix removed from their titles, ISRCs of
track pages, cover art URLs and the count of hidden tracks. Not ported: link types,
pre-order track durations from the embedded player, licence links. Added: tags from the
page's JSON-LD (``keywords``).

The artist is never taken from the subdomain or the page owner: on
``knotwilg.bandcamp.com/album/leitmotiv-2`` the artist is Maxine Funke and Knotwilg is
the label.
"""

import html
import json
import re
import unicodedata
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Self
from urllib.parse import urljoin, urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ep.metadata.web import WebError, WebNotFound, client, get_bytes

# Bandcamp went online in September 2008: earlier release dates are not the digital
# release's, the publish date is used instead.
LAUNCH = (2008, 9)
COVER_URL = "https://f4.bcbits.com/img/a{art_id}_0.jpg"  # _0: the original image


class BandcampError(Exception):
    """The page could not be fetched or has no release data."""


class BandcampNotFound(BandcampError):
    """The page does not exist (any more)."""


class BandcampBlocked(BandcampError):
    """Bandcamp's CDN served a bot check (a JavaScript challenge page) instead of the
    page. Seen from the Docker image (Debian's OpenSSL), not from macOS, 2026-11."""


# Assets of the CDN's challenge page.
_CHALLENGE = "/_fs-ch-"


# What the page embeds (only the fields used; Harmony's json_types.ts documents more).


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class _TrackInfo(_Model):
    track_num: int | None = None
    title: str
    # Credited track artist; null if it is the release artist.
    artist: str | None = None
    # Seconds; 0.0 for unreleased tracks.
    duration: float = 0.0
    # Relative URL of the track page; null for unreleased tracks.
    title_link: str | None = None
    # Stream URLs; null if the track cannot be streamed.
    file: dict[str, str] | None = None
    unreleased_track: bool = False


class _Package(_Model):
    label: str | None = None
    upc: str | None = None
    type_name: str | None = None


class _Current(_Model):
    title: str
    type: Literal["album", "track"]
    # GMT date strings, "01 Nov 2026 00:00:00 GMT".
    release_date: str | None = None
    publish_date: str | None = None
    upc: str | None = None
    isrc: str | None = None


class _TrAlbum(_Model):
    current: _Current
    art_id: int | None = None
    packages: list[_Package] | None = None
    artist: str
    item_type: Literal["album", "track"]
    album_is_preorder: bool | None = None
    album_url: str | None = None
    trackinfo: list[_TrackInfo]
    url: str


class _Band(_Model):
    name: str


# The release, as EP uses it.


class BandcampTrack(_Model):
    number: int
    title: str
    # The credited track artist, if it differs from the release artist (compilations).
    artist: str | None = None
    length_ms: int | None = None
    # The track page; null for tracks not released yet.
    url: str | None = None
    isrc: str | None = None
    streamable: bool = True


class BandcampRelease(_Model):
    # The canonical page URL: https://<band>.bandcamp.com/album/<slug> (or /track/).
    url: str
    kind: Literal["album", "track"]
    title: str
    # The release's artist credit as entered on Bandcamp, e.g. "A, B & C".
    artist: str
    # The account the page belongs to: the artist, or a label.
    band: str
    label: str | None = None
    # YYYY-MM-DD.
    date: str | None = None
    barcode: str | None = None
    cover_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    tracks: list[BandcampTrack]
    # A track page that is part of an album: the album's page.
    album_url: str | None = None
    # Tracks listed in the track count but not on the page (only in the download).
    hidden_tracks: int = 0
    preorder: bool = False


# URLs

_PAGE_URL = re.compile(r"^/(album|track)/([^/]+)/?$")
_SUBDOMAIN = re.compile(r"^[a-z0-9-]+\.bandcamp\.com$")


def canonical_url(url: str) -> str | None:
    """``https://<band>.bandcamp.com/<album|track>/<slug>`` for a Bandcamp album or track
    page URL (query, fragment and trailing slash dropped), else None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not _SUBDOMAIN.match(host):
        return None
    if (m := _PAGE_URL.match(parts.path)) is None:
        return None
    return f"https://{host}/{m.group(1)}/{m.group(2)}"


# Parsing


def _data_attribute(page: str, key: str) -> str | None:
    """The value of the first ``data-<key>`` attribute, entities decoded (Harmony's
    ``extractDataAttribute``)."""
    m = re.search(rf"data-{key}=[\"'](.+?)[\"']", page, re.IGNORECASE | re.DOTALL)
    return html.unescape(m.group(1)) if m else None


def _meta(page: str, name: str) -> str | None:
    m = re.search(
        rf"<meta(?=[^>]+?(?:name|property)=[\"']{re.escape(name)}[\"'])[^>]+?content=[\"'](.+?)[\"']",
        page,
        re.IGNORECASE,
    )
    return html.unescape(m.group(1)) if m else None


def _json_ld(page: str) -> dict[str, Any]:
    m = re.search(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.+?)</script>", page, re.DOTALL
    )
    if m is None:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def simplify_name(name: str) -> str:
    """Letters and digits only, lowercase, without accents (Harmony's ``simplifyName``)."""
    decomposed = unicodedata.normalize("NFKD", name)
    kept = (c for c in decomposed if unicodedata.category(c).startswith("L") or c.isdigit())
    return "".join(kept).lower()


def similar_names(a: str, b: str) -> bool:
    return simplify_name(a) == simplify_name(b)


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None


def _release_date(current: _Current) -> str | None:
    date = _date(current.release_date)
    if date is None or (date.year, date.month) < LAUNCH:
        date = _date(current.publish_date)
    return date.strftime("%Y-%m-%d") if date else None


def _label(tralbum: _TrAlbum, band: str) -> str | None:
    """Harmony's label heuristics: a unique physical package label is the label; the
    band (page owner) is a label too unless it is the artist or part of the credit."""
    label: str | None = None
    band_is_label: bool | None = None
    physical = {package.label for package in tralbum.packages or [] if package.label}
    if len(physical) == 1:
        label = next(iter(physical))
        if similar_names(label, band):
            band_is_label = True
    if band_is_label is not True:
        if similar_names(tralbum.artist, band):
            band_is_label = False
        elif simplify_name(band) in simplify_name(tralbum.artist):
            band_is_label = False  # the credit includes the band: not a label
        else:
            label = band
    return label


def _barcode(tralbum: _TrAlbum) -> str | None:
    """The download release's UPC/EAN, unless it is a physical package's."""
    upc = tralbum.current.upc or None
    if upc and any(package.upc == upc for package in tralbum.packages or []):
        return None
    return upc


def _track(info: _TrackInfo, index: int, base_url: str) -> BandcampTrack:
    title = info.title
    if info.artist:
        # The title is prefixed by the track artist, if filled ("Artist - Title").
        title = title.replace(f"{info.artist} - ", "", 1)
    return BandcampTrack(
        number=info.track_num or index + 1,
        title=title,
        artist=info.artist or None,
        length_ms=round(info.duration * 1000) or None,
        url=canonical_url(urljoin(base_url, info.title_link)) if info.title_link else None,
        streamable=bool(info.file) and not info.unreleased_track,
    )


def _tags(json_ld: dict[str, Any]) -> list[str]:
    keywords = json_ld.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",")]
    seen: dict[str, str] = {}
    for keyword in keywords:
        if isinstance(keyword, str) and keyword.strip():
            seen.setdefault(keyword.strip().casefold(), keyword.strip())
    return list(seen.values())


def parse_release_page(page: str, url: str) -> BandcampRelease:
    """The release on an album or track page. ``url`` is the page's URL (canonical)."""
    tralbum_json, band_json = _data_attribute(page, "tralbum"), _data_attribute(page, "band")
    if tralbum_json is None or band_json is None:
        if _CHALLENGE in page:
            raise BandcampBlocked(
                f"{url}: Bandcamp answered with a bot check instead of the page (its CDN "
                "rejects this client)"
            )
        raise BandcampError(f"{url}: no embedded release data (not an album or track page?)")
    try:
        tralbum = _TrAlbum.model_validate_json(tralbum_json)
        band = _Band.model_validate_json(band_json).name
    except ValidationError as e:
        raise BandcampError(f"{url}: unexpected embedded release data: {e}") from e
    # The release URL may be on a custom domain: links are resolved against the page.
    base = tralbum.url if urlsplit(tralbum.url).hostname == urlsplit(url).hostname else url
    tracks = [_track(info, i, base) for i, info in enumerate(tralbum.trackinfo)]
    current = tralbum.current
    if current.type == "track" and current.isrc and tracks:
        tracks[0] = tracks[0].model_copy(update={"isrc": current.isrc})
    hidden = 0
    description = _meta(page, "og:description") or ""
    if m := re.search(r"(\d+) track", description, re.IGNORECASE):
        hidden = max(int(m.group(1)) - len(tracks), 0)
    album_url = None
    if tralbum.item_type == "track" and tralbum.album_url:
        album_url = canonical_url(urljoin(base, tralbum.album_url))
    return BandcampRelease(
        url=url,
        kind=tralbum.item_type,
        title=current.title,
        artist=tralbum.artist,
        band=band,
        label=_label(tralbum, band),
        date=_release_date(current),
        barcode=_barcode(tralbum),
        cover_url=COVER_URL.format(art_id=tralbum.art_id) if tralbum.art_id else None,
        tags=_tags(_json_ld(page)),
        tracks=tracks,
        album_url=album_url,
        hidden_tracks=hidden,
        preorder=bool(tralbum.album_is_preorder),
    )


class Bandcamp:
    """Fetches Bandcamp pages. Use as a context manager, or call ``close``."""

    def __init__(self, *, transport: httpx2.BaseTransport | None = None) -> None:
        self._http = client(transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get_release(self, url: str) -> BandcampRelease:
        """The release on an album or track page (``canonical_url`` first)."""
        canonical = canonical_url(url)
        if canonical is None:
            raise BandcampError(f"not a Bandcamp album or track page: {url}")
        try:
            content = get_bytes(self._http, canonical)
        except WebNotFound as e:
            raise BandcampNotFound(str(e)) from e
        except WebError as e:
            raise BandcampError(str(e)) from e
        return parse_release_page(content.decode("utf-8", errors="replace"), canonical)
