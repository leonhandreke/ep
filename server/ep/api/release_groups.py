import uuid

from fastapi import APIRouter, status
from pydantic import MISSING
from sqlmodel import col, select

from ep.api.deps import PageDep, SessionDep, SettingsDep, artist_credit_from_input, get_or_404
from ep.api.errors import CONFLICT, NOT_FOUND
from ep.api.external_ids import add_external_id_routes
from ep.api.schemas import (
    ReleaseGroupCreate,
    ReleaseGroupRead,
    ReleaseGroupUpdate,
    TrackRead,
)
from ep.api.tracks import track_reads, with_recording
from ep.covers import remove_cover_file
from ep.credits import delete_artist_credit_if_unused
from ep.external_ids import delete_external_ids
from ep.models import EntityType, ReleaseGroup, Track

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
def get_release_group(id: uuid.UUID, session: SessionDep) -> ReleaseGroupRead:
    return ReleaseGroupRead.model_validate(get_or_404(session, ReleaseGroup, id))


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


add_external_id_routes(router, ReleaseGroup, EntityType.release_group, "")
