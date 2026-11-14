"""Fuzzy search (``[[`` autocomplete) and resolving link targets."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import col, or_, select

from ep.api.deps import SessionDep
from ep.api.errors import NOT_FOUND
from ep.api.schemas import ResolvedRead, SearchHitRead
from ep.matching.search import SEARCH_TYPES, SearchHit, search
from ep.models import Artist, EntityType, Recording, ReleaseGroup, Track

router = APIRouter(tags=["search"])


def _cover_url(release_group_id: UUID) -> str:
    return f"/covers/release-group/{release_group_id}"


@router.get("/search")
def search_library(
    session: SessionDep,
    q: Annotated[str, Query(min_length=1)],
    types: Annotated[list[EntityType], Query(default_factory=list)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[SearchHitRead]:
    """Artists, release groups and recordings matching ``q``, best first.

    Accent- and case-insensitive; finds prefixes ("bjo" finds "Björk"), tolerates small
    typos, and matches artist and title together ("nina simone sinnerman"). ``types``
    (repeatable) limits the entity types; default all.
    """
    hits = search(session, q, types or SEARCH_TYPES, limit)
    recording_ids = [hit.entity.id for hit in hits if hit.type is EntityType.recording]
    covers: dict[UUID, UUID] = {}
    if recording_ids:
        # One release group with a cover per recording, for its cover URL.
        rows = session.exec(
            select(Track.recording_id, ReleaseGroup.id)
            .join(ReleaseGroup, col(Track.release_group_id) == ReleaseGroup.id)
            .where(
                col(Track.recording_id).in_(recording_ids),
                col(ReleaseGroup.cover_path).is_not(None),
            )
            .order_by(col(ReleaseGroup.date).asc().nulls_last(), col(ReleaseGroup.id))
        )
        for recording_id, release_group_id in rows:
            covers.setdefault(recording_id, release_group_id)
    return [_hit_read(hit, covers) for hit in hits]


def _hit_read(hit: SearchHit, covers: dict[UUID, UUID]) -> SearchHitRead:
    entity = hit.entity
    match entity:
        case Artist():
            title, subtitle, cover = entity.name, entity.disambiguation or None, None
        case ReleaseGroup():
            title, subtitle = entity.title, entity.artist_credit.name
            cover = _cover_url(entity.id) if entity.cover_path else None
        case Recording():
            title, subtitle = entity.title, entity.artist_credit.name
            cover = _cover_url(covers[entity.id]) if entity.id in covers else None
        case _:
            raise TypeError(f"unexpected search hit {entity!r}")
    return SearchHitRead(
        type=hit.type,
        id=entity.id,
        mbid=entity.mbid,
        title=title,
        subtitle=subtitle,
        cover_url=cover,
        score=hit.score,
    )


@router.get("/resolve/{type}/{uuid}", responses=NOT_FOUND)
def resolve(type: EntityType, uuid: UUID, session: SessionDep) -> ResolvedRead:
    """Find an entity by MBID or internal ID (as used in note links).

    A release group is also found by the MBID of its release.
    """
    match type:
        case EntityType.artist:
            query = select(Artist.id).where(or_(Artist.id == uuid, Artist.mbid == uuid))
        case EntityType.release_group:
            query = select(ReleaseGroup.id).where(
                or_(
                    ReleaseGroup.id == uuid,
                    ReleaseGroup.mbid == uuid,
                    ReleaseGroup.release_mbid == uuid,
                )
            )
        case EntityType.recording:
            query = select(Recording.id).where(or_(Recording.id == uuid, Recording.mbid == uuid))
    if (id := session.exec(query).first()) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{type} {uuid} not found")
    return ResolvedRead(type=type, id=id)
