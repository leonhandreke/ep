"""Links of an artist or release group to other services, from MusicBrainz (asked when
they are wanted, not stored): a curated set, one link per service, in a fixed order.

A relationship is classified by its URL's host (MusicBrainz's ``free streaming``,
``streaming`` and ``purchase for download`` each cover many services). Wikipedia comes
from a ``wikipedia`` relationship or else the Wikidata item's sitelinks (English, else
another language). Ended relationships are left out. A release group's links include
those of the release it was imported from (MusicBrainz keeps Bandcamp and Spotify links
on releases). The MusicBrainz link is built from the MBID.

Answers are cached in the process (``TTL``): MusicBrainz relationships per entity,
Wikidata sitelinks per item. Failures are not cached.
"""

import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import httpx2

from ep.metadata.musicbrainz import (
    MBUrlRelation,
    MusicBrainz,
    MusicBrainzNotFound,
)
from ep.metadata.wikidata import Wikidata, WikidataError, wikipedia_article

log = logging.getLogger(__name__)

TTL = 24 * 60 * 60.0
MAX_ENTRIES = 4096
MUSICBRAINZ_URL = "https://musicbrainz.org"


class LinkService(StrEnum):
    """Services, in the order links are listed."""

    wikipedia = "wikipedia"
    bandcamp = "bandcamp"
    spotify = "spotify"
    musicbrainz = "musicbrainz"


@dataclass(frozen=True)
class ServiceLink:
    service: LinkService
    url: str
    # Wikipedia: the article's language code (``en``, ``de``, ...); else None.
    label: str | None = None


def _host(url: str) -> str:
    try:
        host = httpx2.URL(url).host.lower()
    except httpx2.InvalidURL:
        return ""
    return host.removeprefix("www.")


def classify(relation: MBUrlRelation) -> LinkService | None:
    """The service of a URL relationship, by its host; None for other services (and
    Wikidata)."""
    host = _host(relation.url.resource)
    if host.endswith(".wikipedia.org"):
        return LinkService.wikipedia
    if host == "bandcamp.com" or host.endswith(".bandcamp.com"):
        return LinkService.bandcamp
    if host == "open.spotify.com":
        return LinkService.spotify
    return None


def wikidata_item(relations: Iterable[MBUrlRelation]) -> str | None:
    for relation in relations:
        if relation.ended or _host(relation.url.resource) != "wikidata.org":
            continue
        qid = relation.url.resource.rstrip("/").rsplit("/", 1)[-1]
        if qid.startswith("Q"):
            return qid
    return None


class TTLCache[K, V]:
    """A thread-safe map whose entries expire ``ttl`` seconds after they were set; the
    oldest entries make room beyond ``max_entries``."""

    def __init__(self, ttl: float = TTL, max_entries: int = MAX_ENTRIES) -> None:
        self.ttl, self.max_entries = ttl, max_entries
        self._entries: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_set(self, key: K, compute: Callable[[], V]) -> V:
        """The cached value, or ``compute()`` (outside the lock; an exception is not
        cached)."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > time.monotonic():
                return entry[1]
        value = compute()
        with self._lock:
            self._entries[key] = (time.monotonic() + self.ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


type MBEntity = Literal["artist", "release-group", "release"]

RELATIONS: TTLCache[tuple[MBEntity, uuid.UUID], tuple[MBUrlRelation, ...]] = TTLCache()
SITELINKS: TTLCache[str, dict[str, str]] = TTLCache()


def clear_caches() -> None:
    RELATIONS.clear()
    SITELINKS.clear()


def _relations(
    musicbrainz: MusicBrainz, entity: MBEntity, mbid: uuid.UUID
) -> tuple[MBUrlRelation, ...]:
    """The entity's URL relationships (none if MusicBrainz does not know it); raises
    MusicBrainzError if MusicBrainz cannot be asked."""

    def fetch() -> tuple[MBUrlRelation, ...]:
        try:
            return tuple(musicbrainz.get_url_relations(entity, mbid))
        except MusicBrainzNotFound:
            return ()

    return RELATIONS.get_or_set((entity, mbid), fetch)


def _wikipedia(wikidata: Wikidata, qid: str) -> ServiceLink | None:
    """The item's Wikipedia article; None if it has none or Wikidata cannot be asked
    (logged, not cached: asked again next time)."""
    try:
        sitelinks = SITELINKS.get_or_set(qid, lambda: wikidata.sitelinks(qid))
    except WikidataError as e:
        log.warning("no Wikipedia link for %s: %s", qid, e)
        return None
    article = wikipedia_article(sitelinks)
    return ServiceLink(LinkService.wikipedia, *article) if article else None


def _links(
    relations: Iterable[MBUrlRelation], wikidata: Wikidata, musicbrainz_url: str
) -> list[ServiceLink]:
    relations = [r for r in relations if not r.ended]
    found: dict[LinkService, ServiceLink] = {}
    for relation in relations:
        service = classify(relation)
        if service is not None and service not in found:
            label = None
            if service is LinkService.wikipedia:
                label = _host(relation.url.resource).removesuffix(".wikipedia.org")
            found[service] = ServiceLink(service, relation.url.resource, label)
    qid = wikidata_item(relations) if LinkService.wikipedia not in found else None
    if qid and (article := _wikipedia(wikidata, qid)) is not None:
        found[LinkService.wikipedia] = article
    found[LinkService.musicbrainz] = ServiceLink(LinkService.musicbrainz, musicbrainz_url)
    return [found[service] for service in LinkService if service in found]


def artist_links(
    musicbrainz: MusicBrainz, wikidata: Wikidata, mbid: uuid.UUID
) -> list[ServiceLink]:
    """An artist's links; raises MusicBrainzError if MusicBrainz cannot be asked."""
    return _links(
        _relations(musicbrainz, "artist", mbid), wikidata, f"{MUSICBRAINZ_URL}/artist/{mbid}"
    )


def release_group_links(
    musicbrainz: MusicBrainz,
    wikidata: Wikidata,
    mbid: uuid.UUID,
    release_mbid: uuid.UUID | None,
) -> list[ServiceLink]:
    """A release group's links, with those of its release (the release group's own
    first); raises MusicBrainzError if MusicBrainz cannot be asked."""
    relations = _relations(musicbrainz, "release-group", mbid)
    if release_mbid is not None:
        relations += _relations(musicbrainz, "release", release_mbid)
    return _links(relations, wikidata, f"{MUSICBRAINZ_URL}/release-group/{mbid}")
