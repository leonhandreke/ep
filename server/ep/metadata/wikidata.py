"""Wikidata: an item's Wikipedia articles (sitelinks), for an entity's links.

Requests follow Wikimedia's etiquette: one at a time across the process, spaced at
least ``REQUEST_INTERVAL`` apart, with a descriptive User-Agent (no contact details:
those are for MusicBrainz only), through the ``wbgetentities`` API asking only for
sitelinks. Callers cache the answers (``ep.service_links``).

Usage::

    with Wikidata() as wikidata:
        sitelinks = wikidata.sitelinks("Q22277802")
"""

import re
from importlib.metadata import version
from typing import Self

import httpx2
from pydantic import BaseModel, ValidationError

from ep.metadata.musicbrainz import RateLimiter
from ep.metadata.web import WebError, client, get_bytes

API_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = f"EP/{version('ep')} (self-hosted music library) httpx2/{version('httpx2')}"
REQUEST_INTERVAL = 1.0
QID = re.compile(r"^Q[1-9][0-9]*$")

# One limiter per process.
LIMITER = RateLimiter(REQUEST_INTERVAL)


class WikidataError(Exception):
    """Wikidata could not be reached or returned an unexpected response."""


class _Sitelink(BaseModel):
    site: str
    url: str


class _Entity(BaseModel):
    # Absent for a missing item (``"missing": ""``).
    sitelinks: dict[str, _Sitelink] = {}


class _Entities(BaseModel):
    entities: dict[str, _Entity] = {}


class Wikidata:
    """A Wikidata client. Use as a context manager, or call ``close``."""

    def __init__(
        self, *, transport: httpx2.BaseTransport | None = None, limiter: RateLimiter = LIMITER
    ) -> None:
        self._http = client(transport)
        self._http.headers["User-Agent"] = USER_AGENT
        self._limiter = limiter

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def sitelinks(self, qid: str) -> dict[str, str]:
        """The item's sitelinks as site id (``enwiki``, ``dewiki``, ``commonswiki``, ...)
        -> URL; empty for an unknown item. A redirected (merged) item answers with the
        item it redirects to."""
        if not QID.match(qid):
            raise ValueError(f"not a Wikidata item id: {qid!r}")
        params = {
            "action": "wbgetentities",
            "ids": qid,
            "props": "sitelinks/urls",
            "format": "json",
        }
        self._limiter.wait()
        try:
            content = get_bytes(self._http, str(httpx2.URL(API_URL, params=params)))
        except WebError as e:
            raise WikidataError(f"Wikidata request failed: {e}") from e
        try:
            entities = _Entities.model_validate_json(content).entities
        except ValidationError as e:
            raise WikidataError(f"unexpected Wikidata response for {qid}") from e
        # An unknown id is an "error" (no-such-entity) without entities.
        links = [link for entity in entities.values() for link in entity.sitelinks.values()]
        return {link.site: link.url for link in links}


def wikipedia_article(sitelinks: dict[str, str]) -> tuple[str, str] | None:
    """The English Wikipedia article, else the one of the first language by site id, as
    (URL, language code); None if the item has none (Commons, Wikiquote, ... are not
    Wikipedia)."""
    articles = {}
    for site, url in sitelinks.items():
        host = httpx2.URL(url).host
        if site.endswith("wiki") and host.endswith(".wikipedia.org"):
            articles[site] = (url, host.removesuffix(".wikipedia.org"))
    if not articles:
        return None
    return articles.get("enwiki") or articles[min(articles)]
