"""Fuzzy search (``[[`` autocomplete) and resolving link targets."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from ep.api.deps import SessionDep
from ep.api.entities import entity_read, recording_covers
from ep.api.errors import NOT_FOUND
from ep.api.schemas import ResolvedRead, SearchHitRead
from ep.entities import resolve_ref
from ep.matching.search import SEARCH_TYPES, search
from ep.models import EntityType

router = APIRouter(tags=["search"])


@router.get("/search")
def search_library(
    session: SessionDep,
    q: Annotated[str, Query(min_length=1)],
    types: Annotated[list[EntityType], Query(default_factory=list)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    include_radio_only: bool = False,
) -> list[SearchHitRead]:
    """Artists, release groups, recordings, radio shows and radio episodes matching
    ``q``, best first.

    Accent- and case-insensitive; finds prefixes ("bjo" finds "Björk"), tolerates small
    typos, and matches artist and title together ("nina simone sinnerman"). ``types``
    (repeatable) limits the entity types; default all. Artists and recordings only known
    from radio tracklists are left out unless ``include_radio_only``. Each hit carries
    the link to write for it (``link``: ``[[type:uuid|Title]]``, the UUID being the MBID
    if there is one, else the id).
    """
    hits = search(session, q, types or SEARCH_TYPES, limit, include_radio_only)
    covers = recording_covers(
        session, (hit.entity.id for hit in hits if hit.type is EntityType.recording)
    )
    return [
        SearchHitRead(**entity_read(hit.entity, covers).model_dump(), score=hit.score)
        for hit in hits
    ]


@router.get("/resolve/{type}/{uuid}", responses=NOT_FOUND)
def resolve_entity(type: EntityType, uuid: UUID, session: SessionDep) -> ResolvedRead:
    """Find an entity by MBID or internal ID (as used in note links).

    A release group is also found by the MBID of its release. Radio shows and episodes
    have internal IDs only.
    """
    if (entity := resolve_ref(session, type, uuid)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{type} {uuid} not found")
    return ResolvedRead(type=type, id=entity.id)
