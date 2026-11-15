"""Classifying URLs: what a pasted link is, before importing it or adding it as a source."""

from typing import Annotated

from fastapi import APIRouter, Query

from ep.api.schemas import LinkInfoRead
from ep.links import classify

router = APIRouter(tags=["links"])


@router.get("/links/classify")
def classify_link(url: Annotated[str, Query(min_length=1)]) -> LinkInfoRead:
    """What ``url`` is (no request is made to it): the kind of page (Bandcamp album or
    track, YouTube video or playlist, NTS episode, SoundCloud, Spotify), the source kind
    it would be stored as, the import that takes it (null: not importable) and its
    canonical form. All fields null for a URL EP does not know."""
    info = classify(url)
    if info is None:
        return LinkInfoRead(link_kind=None, source_kind=None, import_kind=None, url=None)
    return LinkInfoRead(
        link_kind=info.link_kind,
        source_kind=info.source_kind,
        import_kind=info.import_kind,
        url=info.url,
    )
