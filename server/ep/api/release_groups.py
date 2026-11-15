import uuid
from collections import Counter

from fastapi import APIRouter, HTTPException, status
from pydantic import MISSING
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from ep.api.deps import PageDep, SessionDep, SettingsDep, artist_credit_from_input, get_or_404
from ep.api.errors import CONFLICT, NOT_FOUND, unprocessable
from ep.api.external_ids import add_external_id_routes
from ep.api.schemas import (
    Page,
    ReleaseGroupCreate,
    ReleaseGroupDetail,
    ReleaseGroupRead,
    ReleaseGroupUpdate,
    TrackOrderWrite,
    TrackRead,
)
from ep.api.tracks import track_reads, with_recording
from ep.covers import remove_cover_file
from ep.credits import delete_artist_credit_if_unused, with_credit
from ep.external_ids import delete_external_ids
from ep.models import EntityType, ReleaseGroup, Track, rel
from ep.notes.store import delete_annotations
from ep.playback import PlayState, playabilities

router = APIRouter(prefix="/release-groups", tags=["release groups"])

# Oldest first; undated release groups last. Partial dates sort correctly as text.
release_group_order = (
    col(ReleaseGroup.date).asc().nulls_last(),
    col(ReleaseGroup.title),
    col(ReleaseGroup.id),
)


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_release_group(body: ReleaseGroupCreate, session: SessionDep) -> ReleaseGroupRead:
    credit = artist_credit_from_input(session, body.artist_credit)
    release_group = ReleaseGroup.model_validate(
        body.model_dump(exclude={"artist_credit"}), update={"artist_credit_id": credit.id}
    )
    session.add(release_group)
    session.commit()
    return ReleaseGroupRead.model_validate(release_group)


@router.get("/{id}", responses=NOT_FOUND)
def get_release_group(id: uuid.UUID, session: SessionDep) -> ReleaseGroupDetail:
    """A release group with what its tracks add up to: their number, discs, total
    length, how many are in each play state, and the kinds of link the link-only ones
    have."""
    release_group = session.exec(
        select(ReleaseGroup)
        .where(ReleaseGroup.id == id)
        .options(
            with_credit(ReleaseGroup.artist_credit),
            selectinload(rel(ReleaseGroup.tracks)).joinedload(rel(Track.recording)),
        )
    ).one_or_none()
    if release_group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"release_group {id} not found")
    tracks = release_group.tracks
    found = playabilities(session, {t.recording.id: t.recording for t in tracks}.values())
    states = Counter(found[t.recording_id].state for t in tracks)
    link_kinds = {
        link.kind
        for t in tracks
        if (p := found[t.recording_id]).state is PlayState.link_only and (link := p.link)
    }
    return ReleaseGroupDetail.model_validate(
        {
            **ReleaseGroupRead.model_validate(release_group).model_dump(),
            "cover_path": release_group.cover_path,
            "track_count": len(tracks),
            "disc_count": len({t.disc for t in tracks}),
            "length_ms": sum(t.recording.length_ms or 0 for t in tracks),
            "play_states": {state.value: states[state] for state in PlayState},
            "link_kinds": sorted(link_kinds),
        }
    )


@router.patch("/{id}", responses=NOT_FOUND | CONFLICT)
def update_release_group(
    id: uuid.UUID, body: ReleaseGroupUpdate, session: SessionDep
) -> ReleaseGroupRead:
    release_group = get_or_404(session, ReleaseGroup, id)
    release_group.sqlmodel_update(body.model_dump(exclude={"artist_credit"}))
    if body.artist_credit is not MISSING:
        old_credit = release_group.artist_credit
        release_group.artist_credit = artist_credit_from_input(session, body.artist_credit)
        delete_artist_credit_if_unused(session, old_credit)
    session.commit()
    return ReleaseGroupRead.model_validate(release_group)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND | CONFLICT)
def delete_release_group(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> None:
    """Delete a release group, its tracks and its cover. The recordings stay."""
    release_group = get_or_404(session, ReleaseGroup, id)
    credit, cover_path = release_group.artist_credit, release_group.cover_path
    delete_external_ids(session, EntityType.release_group, id)
    delete_annotations(session, EntityType.release_group, id)
    session.delete(release_group)
    delete_artist_credit_if_unused(session, credit)
    session.commit()
    if cover_path:
        remove_cover_file(cover_path, settings.library_dir)


@router.get("/{id}/tracks", responses=NOT_FOUND)
def list_release_group_tracks(id: uuid.UUID, session: SessionDep, page: PageDep) -> list[TrackRead]:
    """Tracks in disc and position order, each with its recording and how to play it."""
    get_or_404(session, ReleaseGroup, id)
    tracks = session.exec(
        select(Track)
        .where(Track.release_group_id == id)
        .order_by(col(Track.disc), col(Track.position))
        .limit(page.limit)
        .offset(page.offset)
        .options(with_recording)
    ).all()
    return track_reads(session, tracks)


@router.put("/{id}/track-order", responses=NOT_FOUND | CONFLICT)
def set_release_group_track_order(
    id: uuid.UUID, body: TrackOrderWrite, session: SessionDep
) -> list[TrackRead]:
    """Move tracks to new discs and positions in one step (e.g. after dragging rows).
    Lists every track of the release group once; two tracks at one place are a 422.
    Returns the tracks in their new order."""
    release_group = get_or_404(session, ReleaseGroup, id)
    tracks = {track.id: track for track in release_group.tracks}
    given = [place.id for place in body.tracks]
    if sorted(given, key=str) != sorted(tracks, key=str):
        raise unprocessable(
            ("tracks",), "must list every track of the release group once", None, "value_error"
        )
    if len({(place.disc, place.position) for place in body.tracks}) != len(body.tracks):
        raise unprocessable(("tracks",), "two tracks at one place", None, "value_error")
    # Out of the way first: the unique (disc, position) is checked per statement.
    for offset, track in enumerate(tracks.values(), start=1):
        track.position = -offset
    session.flush()
    for place in body.tracks:
        tracks[place.id].disc, tracks[place.id].position = place.disc, place.position
    session.commit()
    return list_release_group_tracks(id, session, Page(limit=1000))


add_external_id_routes(router, ReleaseGroup, EntityType.release_group, "")
