import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import col, select

from ep.api.deps import PageDep, SessionDep, get_or_404
from ep.api.errors import CONFLICT, NOT_FOUND
from ep.api.external_ids import add_external_id_routes
from ep.api.release_groups import release_group_order
from ep.api.schemas import (
    ArtistCreate,
    ArtistRead,
    ArtistUpdate,
    RecordingRead,
    ReleaseGroupRead,
)
from ep.credits import with_credit
from ep.external_ids import delete_external_ids
from ep.models import Artist, ArtistCreditName, EntityType, Recording, ReleaseGroup, Track
from ep.notes.store import delete_annotations
from ep.radio import visible_artists, visible_recordings

router = APIRouter(prefix="/artists", tags=["artists"])


def _credit_ids(artist_id: uuid.UUID):
    """Subquery: the artist credits that include this artist."""
    return select(ArtistCreditName.artist_credit_id).where(ArtistCreditName.artist_id == artist_id)


@router.get("")
def list_artists(
    session: SessionDep, page: PageDep, include_radio_only: bool = False
) -> list[ArtistRead]:
    """Artists by sort name. Artists only known from radio tracklists (no release group,
    only radio-only recordings, see ``ep.radio``) are left out unless
    ``include_radio_only``."""
    query = select(Artist)
    if (visible := visible_artists(include_radio_only)) is not None:
        query = query.where(visible)
    artists = session.exec(
        query.order_by(col(Artist.sort_name), col(Artist.name), col(Artist.id))
        .limit(page.limit)
        .offset(page.offset)
    )
    return [ArtistRead.model_validate(artist) for artist in artists]


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_artist(body: ArtistCreate, session: SessionDep) -> ArtistRead:
    artist = Artist.model_validate(body, update={"sort_name": body.sort_name or body.name})
    session.add(artist)
    session.commit()
    return ArtistRead.model_validate(artist)


@router.get("/{id}", responses=NOT_FOUND)
def get_artist(id: uuid.UUID, session: SessionDep) -> ArtistRead:
    return ArtistRead.model_validate(get_or_404(session, Artist, id))


@router.patch("/{id}", responses=NOT_FOUND | CONFLICT)
def update_artist(id: uuid.UUID, body: ArtistUpdate, session: SessionDep) -> ArtistRead:
    artist = get_or_404(session, Artist, id)
    artist.sqlmodel_update(body.model_dump())
    session.commit()
    return ArtistRead.model_validate(artist)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND | CONFLICT)
def delete_artist(id: uuid.UUID, session: SessionDep) -> None:
    """Delete an artist that is not credited on anything."""
    artist = get_or_404(session, Artist, id)
    if session.exec(select(ArtistCreditName.id).where(ArtistCreditName.artist_id == id)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "artist is still credited")
    delete_external_ids(session, EntityType.artist, id)
    delete_annotations(session, EntityType.artist, id)
    session.delete(artist)
    session.commit()


@router.get("/{id}/release-groups", responses=NOT_FOUND)
def list_artist_release_groups(
    id: uuid.UUID, session: SessionDep, page: PageDep
) -> list[ReleaseGroupRead]:
    """Release groups whose artist credit includes this artist, oldest first."""
    get_or_404(session, Artist, id)
    release_groups = session.exec(
        select(ReleaseGroup)
        .where(col(ReleaseGroup.artist_credit_id).in_(_credit_ids(id)))
        .order_by(*release_group_order)
        .limit(page.limit)
        .offset(page.offset)
        .options(with_credit(ReleaseGroup.artist_credit))
    )
    return [ReleaseGroupRead.model_validate(rg) for rg in release_groups]


@router.get("/{id}/recordings", responses=NOT_FOUND)
def list_artist_recordings(
    id: uuid.UUID,
    session: SessionDep,
    page: PageDep,
    standalone: bool | None = None,
    include_radio_only: bool = False,
) -> list[RecordingRead]:
    """Recordings whose artist credit includes this artist, by title.

    ``standalone=true``: only recordings that are not a track of any release group
    (loose tracks); ``false``: only those that are; omitted: all. Recordings only known
    from radio tracklists (``ep.radio``) are left out unless ``include_radio_only``.
    """
    get_or_404(session, Artist, id)
    query = select(Recording).where(col(Recording.artist_credit_id).in_(_credit_ids(id)))
    if (visible := visible_recordings(include_radio_only)) is not None:
        query = query.where(visible)
    if standalone is not None:
        has_track = select(Track.id).where(Track.recording_id == Recording.id).exists()
        query = query.where(~has_track if standalone else has_track)
    recordings = session.exec(
        query.order_by(col(Recording.title), col(Recording.id))
        .limit(page.limit)
        .offset(page.offset)
        .options(with_credit(Recording.artist_credit))
    )
    return [RecordingRead.model_validate(recording) for recording in recordings]


add_external_id_routes(router, Artist, EntityType.artist, "")
