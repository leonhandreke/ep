"""Fuzzy search (``[[`` autocomplete, the search palette) and resolving link targets."""

import uuid
from collections import Counter
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy import exists
from sqlalchemy.orm import joinedload
from sqlmodel import Session, col, func, select

from ep.api.deps import PAGED, TOTAL_COUNT, SessionDep
from ep.api.entities import entity_read, recording_albums
from ep.api.errors import NOT_FOUND
from ep.api.schemas import RadioMatchRead, ResolvedRead, SearchHitRead
from ep.credits import with_credit
from ep.entities import resolve_ref
from ep.matching.search import SEARCH_TYPES, SearchHit, search, tagged
from ep.models import (
    ArtistCreditName,
    EntityTag,
    EntityType,
    RadioEpisode,
    RadioEpisodeTrack,
    Recording,
    ReleaseGroup,
    Track,
    rel,
)
from ep.radio import visible_recordings

router = APIRouter(tags=["search"])

# A radio episode found through what it played ranks a little below that hit.
RADIO_MATCH_FACTOR = 0.9


@router.get("/search", responses=PAGED)
def search_library(
    session: SessionDep,
    response: Response,
    *,
    q: Annotated[str | None, Query(min_length=1)] = None,
    types: Annotated[list[EntityType], Query(default_factory=list)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    per_type: Annotated[int | None, Query(ge=1, le=100)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    tag_id: uuid.UUID | None = None,
    include_radio_only: bool = False,
) -> list[SearchHitRead]:
    """Artists, release groups, recordings, radio shows and radio episodes matching
    ``q``, best first.

    Accent- and case-insensitive; finds prefixes ("bjo" finds "Björk"), tolerates small
    typos, and matches artist and title together ("nina simone sinnerman"). ``types``
    (repeatable) limits the entity types; default all. ``per_type`` caps the hits of
    each type (for results grouped by type), ``limit`` all of them. ``tag_id``: only
    entities with that tag. Give ``q``, ``tag_id`` or both: without ``q``, everything
    with the tag, by type (artists, release groups, recordings, radio shows, radio
    episodes) and then by name, with ``score`` null, in pages (``limit``, ``offset``;
    ``X-Total-Count`` is the length of the whole list). A search with ``q`` is ranked and
    not paged: ``offset`` must be 0 and ``X-Total-Count`` is the number of hits.
    Artists and recordings only known from radio tracklists are left out unless
    ``include_radio_only``.

    Radio episodes are also found by what they played: an episode that played a
    matching recording, or a track of a matching release group, is a hit with
    ``radio_match`` (the entry and its time). Release group hits carry their track
    count, artist hits their release group and loose recording counts. Each hit
    carries the link to write for it (``link``: ``[[type:uuid|Title]]``, the UUID being
    the MBID if there is one, else the id).
    """
    wanted = types or list(SEARCH_TYPES)
    radio: dict[uuid.UUID, tuple[SearchHit, RadioEpisodeTrack]] = {}
    if q is None:
        if tag_id is None:
            error = {"type": "missing", "loc": ("query", "q"), "input": None}
            raise RequestValidationError([{**error, "msg": "give q, tag_id or both"}])
        hits, total = tagged(session, tag_id, wanted, limit, include_radio_only, per_type, offset)
    else:
        if offset:
            error = {"type": "value_error", "loc": ("query", "offset"), "input": offset}
            raise RequestValidationError([{**error, "msg": "a search with q is not paged"}])
        hits = search(session, q, wanted, limit, include_radio_only, per_type, tag_id)
        total = len(hits)
    if q is not None and EntityType.radio_episode in wanted:
        radio = _radio_matches(session, hits, tag_id)
        direct = {hit.entity.id for hit in hits if hit.type is EntityType.radio_episode}
        radio = {id: match for id, match in radio.items() if id not in direct}
        cap = per_type or limit
        for hit, _ in sorted(radio.values(), key=lambda m: m[0].score, reverse=True)[:cap]:
            hits.append(hit)
        hits = sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]
        total = len(hits)
    response.headers[TOTAL_COUNT] = str(total)
    by_type = _ids_by_type(hits)
    albums = recording_albums(
        session,
        {
            *by_type[EntityType.recording],
            *(entry.recording_id for _, entry in radio.values() if entry.recording_id),
        },
    )
    track_counts = _track_counts(session, by_type[EntityType.release_group])
    release_groups, loose = _artist_counts(session, by_type[EntityType.artist])
    reads = []
    for hit in hits:
        read = entity_read(hit.entity, albums)
        match: RadioMatchRead | None = None
        if hit.type is EntityType.radio_episode and hit.entity.id in radio:
            _, entry = radio[hit.entity.id]
            if entry.recording is not None:
                match = RadioMatchRead(
                    track_id=entry.id,
                    recording=entity_read(entry.recording, albums),
                    start_ms=entry.source.start_ms if entry.source is not None else None,
                    offset_is_estimate=entry.offset_is_estimate,
                )
        is_artist = hit.type is EntityType.artist
        reads.append(
            SearchHitRead(
                **read.model_dump(),
                score=hit.score if q is not None else None,
                track_count=(
                    track_counts.get(hit.entity.id, 0)
                    if hit.type is EntityType.release_group
                    else None
                ),
                release_group_count=release_groups.get(hit.entity.id, 0) if is_artist else None,
                loose_recording_count=loose.get(hit.entity.id, 0) if is_artist else None,
                radio_match=match,
            )
        )
    return reads


def _ids_by_type(hits: Sequence[SearchHit]) -> dict[EntityType, list[uuid.UUID]]:
    ids: dict[EntityType, list[uuid.UUID]] = {type: [] for type in EntityType}
    for hit in hits:
        ids[hit.type].append(hit.entity.id)
    return ids


def _radio_matches(
    session: Session, hits: Sequence[SearchHit], tag_id: uuid.UUID | None
) -> dict[uuid.UUID, tuple[SearchHit, RadioEpisodeTrack]]:
    """Episodes that played a recording hit or a track of a release group hit: for each,
    a hit scored from the best such hit, and the entry (one query)."""
    scores = {
        hit.entity.id: hit.score
        for hit in hits
        if hit.type in (EntityType.recording, EntityType.release_group)
    }
    if not scores:
        return {}
    on_release_group = select(Track.recording_id, Track.release_group_id).where(
        col(Track.release_group_id).in_(scores)
    )
    via = {
        recording_id: release_group_id
        for recording_id, release_group_id in session.exec(on_release_group)
    }
    recording_ids = set(scores) | set(via)
    query = (
        select(RadioEpisodeTrack)
        .where(col(RadioEpisodeTrack.recording_id).in_(recording_ids))
        .order_by(col(RadioEpisodeTrack.episode_id), col(RadioEpisodeTrack.position))
        .options(
            joinedload(rel(RadioEpisodeTrack.episode)).joinedload(rel(RadioEpisode.show)),
            with_credit(RadioEpisodeTrack.recording, Recording.artist_credit),
            joinedload(rel(RadioEpisodeTrack.source)),
        )
    )
    if tag_id is not None:
        query = query.where(
            exists().where(
                col(EntityTag.tag_id) == tag_id,
                col(EntityTag.entity_type) == EntityType.radio_episode,
                col(EntityTag.entity_id) == col(RadioEpisodeTrack.episode_id),
            )
        )
    found: dict[uuid.UUID, tuple[SearchHit, RadioEpisodeTrack]] = {}
    for entry in session.exec(query).unique():
        assert entry.recording_id is not None
        score = scores.get(entry.recording_id) or scores[via[entry.recording_id]]
        score *= RADIO_MATCH_FACTOR
        current = found.get(entry.episode_id)
        if current is None or score > current[0].score:
            hit = SearchHit(EntityType.radio_episode, entry.episode, score)
            found[entry.episode_id] = (hit, entry)
    return found


def _track_counts(session: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = session.exec(
        select(Track.release_group_id, func.count(col(Track.id)))
        .where(col(Track.release_group_id).in_(ids))
        .group_by(col(Track.release_group_id))
    )
    return dict(rows.all())


def _artist_counts(
    session: Session, ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """Release groups and loose (not radio-only) recordings credited to each artist."""
    if not ids:
        return {}, {}
    names = select(ArtistCreditName.artist_id, ArtistCreditName.artist_credit_id).where(
        col(ArtistCreditName.artist_id).in_(ids)
    )
    credits = names.subquery()
    release_groups = Counter(
        dict(
            session.exec(
                select(credits.c.artist_id, func.count(func.distinct(col(ReleaseGroup.id))))
                .join(
                    ReleaseGroup, col(ReleaseGroup.artist_credit_id) == credits.c.artist_credit_id
                )
                .group_by(credits.c.artist_id)
            ).all()
        )
    )
    loose_query = (
        select(credits.c.artist_id, func.count(func.distinct(col(Recording.id))))
        .join(Recording, col(Recording.artist_credit_id) == credits.c.artist_credit_id)
        .where(~exists().where(col(Track.recording_id) == col(Recording.id)))
        .group_by(credits.c.artist_id)
    )
    if (visible := visible_recordings(False)) is not None:
        loose_query = loose_query.where(visible)
    loose = Counter(dict(session.exec(loose_query).all()))
    return dict(release_groups), dict(loose)


@router.get("/resolve/{type}/{id}", responses=NOT_FOUND)
def resolve_entity(type: EntityType, id: uuid.UUID, session: SessionDep) -> ResolvedRead:
    """Find an entity by MBID or internal ID (as used in note links).

    A release group is also found by the MBID of its release. Radio shows and episodes
    have internal IDs only.
    """
    if (entity := resolve_ref(session, type, id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{type} {id} not found")
    return ResolvedRead(type=type, id=entity.id)
