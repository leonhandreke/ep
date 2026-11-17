import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy import ColumnElement, exists, literal, tuple_
from sqlalchemy import select as sa_select
from sqlmodel import col, func, select

from ep.api.deps import PAGED, PageDep, SessionDep, get_or_404
from ep.api.errors import CONFLICT, NOT_FOUND
from ep.api.external_ids import add_external_id_routes
from ep.api.release_groups import release_group_order
from ep.api.schemas import (
    ArtistCountsRead,
    ArtistCreate,
    ArtistDetail,
    ArtistPositionRead,
    ArtistRead,
    ArtistUpdate,
    PlayableRecordingRead,
    RecordingRead,
    ReleaseGroupRead,
)
from ep.api.tracks import playability_read
from ep.credits import with_credit
from ep.external_ids import delete_external_ids
from ep.models import (
    Artist,
    ArtistCreditName,
    EntityType,
    RadioEpisodeTrack,
    Recording,
    ReleaseGroup,
    Track,
)
from ep.notes.store import delete_annotations
from ep.playback import playabilities
from ep.radio import (
    radio_only_artist,
    radio_only_recording,
    visible_artists,
    visible_recordings,
)

router = APIRouter(prefix="/artists", tags=["artists"])


def _credit_ids(artist_id: uuid.UUID):
    """Subquery: the artist credits that include this artist."""
    return select(ArtistCreditName.artist_credit_id).where(ArtistCreditName.artist_id == artist_id)


def _appearances(artist_id: uuid.UUID) -> list[ColumnElement[bool]]:
    """Conditions on ``ReleaseGroup``: not credited to this artist, but with a track
    whose recording is (a featured appearance)."""
    credits = _credit_ids(artist_id)
    credited_track = (
        select(Track.id)
        .join(Recording, col(Track.recording_id) == col(Recording.id))
        .where(
            col(Track.release_group_id) == col(ReleaseGroup.id),
            col(Recording.artist_credit_id).in_(credits),
        )
        .exists()
    )
    return [~col(ReleaseGroup.artist_credit_id).in_(credits), credited_track]


# The order of ``GET /artists``.
_ARTIST_ORDER = (col(Artist.sort_name), col(Artist.name), col(Artist.id))

ArtistFilter = Annotated[str | None, Query(min_length=1)]


def _listed_artists(include_radio_only: bool, q: str | None) -> list[ColumnElement[bool]]:
    """The conditions of ``GET /artists`` with these parameters."""
    where = []
    if (visible := visible_artists(include_radio_only)) is not None:
        where.append(visible)
    if q is not None:
        needle = func.ep_normalize(q)
        where.append(
            col(Artist.name_normalized).contains(needle)
            | col(Artist.sort_name_normalized).contains(needle)
        )
    return where


@router.get("", responses=PAGED)
def list_artists(
    session: SessionDep,
    page: PageDep,
    include_radio_only: bool = False,
    q: ArtistFilter = None,
) -> list[ArtistRead]:
    """Artists by sort name. Artists only known from radio tracklists (no release group,
    only radio-only recordings, see ``ep.radio``) are left out unless
    ``include_radio_only``. ``q`` filters (still by sort name): artists whose name or
    sort name contains it, ignoring case, accents and punctuation (index-backed; for
    ranked fuzzy matching use ``/search``). Where an artist or a typed name is in this
    list: ``GET /artists/position``."""
    query = select(Artist).where(*_listed_artists(include_radio_only, q))
    artists = session.exec(page.apply(session, query).order_by(*_ARTIST_ORDER))
    return [ArtistRead.model_validate(artist) for artist in artists]


@router.get("/position", responses=NOT_FOUND)
def get_artist_position(
    session: SessionDep,
    include_radio_only: bool = False,
    q: ArtistFilter = None,
    id: uuid.UUID | None = None,
    sort_name: Annotated[str | None, Query(min_length=1)] = None,
) -> ArtistPositionRead:
    """Where an artist is in ``GET /artists`` with the same ``include_radio_only`` and
    ``q``, as the ``offset`` of its row (to load the page that has it). Give exactly one
    of ``id`` (that artist; 404 if the list leaves it out) or ``sort_name`` (the first
    artist that sorts at or after it, as type-to-select does: "bjo" finds "Björk";
    past the end of the list: ``offset`` is the number of artists and ``id`` null)."""
    if (id is None) == (sort_name is None):
        error = {"type": "value_error", "loc": ("query", "id"), "input": None}
        raise RequestValidationError([{**error, "msg": "give exactly one of id and sort_name"}])
    where = _listed_artists(include_radio_only, q)
    if id is not None:
        found = session.exec(
            select(Artist.sort_name, Artist.name).where(Artist.id == id, *where)
        ).first()
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"artist {id} is not in this list")
        before = tuple_(*_ARTIST_ORDER) < tuple_(literal(found[0]), literal(found[1]), literal(id))
        offset = session.exec(select(func.count()).where(before, *where)).one()
        return ArtistPositionRead(offset=offset, id=id)
    before = col(Artist.sort_name) < sort_name
    offset, first = session.exec(
        select(
            select(func.count()).where(before, *where).scalar_subquery(),
            select(Artist.id)
            .where(~before, *where)
            .order_by(*_ARTIST_ORDER)
            .limit(1)
            .scalar_subquery(),
        )
    ).one()
    return ArtistPositionRead(offset=offset, id=first)


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_artist(body: ArtistCreate, session: SessionDep) -> ArtistRead:
    artist = Artist.model_validate(body, update={"sort_name": body.sort_name or body.name})
    session.add(artist)
    session.commit()
    return ArtistRead.model_validate(artist)


@router.get("/{id}", responses=NOT_FOUND)
def get_artist(id: uuid.UUID, session: SessionDep) -> ArtistDetail:
    """An artist with counts: release groups, loose recordings (not radio-only), release
    groups they appear on (``/appearances``), radio episodes that played them; and
    whether the artist is known only from radio."""
    credits = _credit_ids(id)
    loose = select(func.count(col(Recording.id))).where(
        col(Recording.artist_credit_id).in_(credits),
        ~exists().where(col(Track.recording_id) == col(Recording.id)),
        ~radio_only_recording(col(Recording.id)),
    )
    episodes = (
        select(func.count(func.distinct(col(RadioEpisodeTrack.episode_id))))
        .join(Recording, col(RadioEpisodeTrack.recording_id) == col(Recording.id))
        .where(col(Recording.artist_credit_id).in_(credits))
    )
    release_groups = select(func.count(col(ReleaseGroup.id))).where(
        col(ReleaseGroup.artist_credit_id).in_(credits)
    )
    appearances = select(func.count(col(ReleaseGroup.id))).where(*_appearances(id))
    artist = get_or_404(session, Artist, id)
    # sqlmodel's ``select`` (and ``session.exec``) are typed for at most four columns.
    row = (
        session.connection()
        .execute(
            sa_select(
                release_groups.scalar_subquery(),
                loose.scalar_subquery(),
                appearances.scalar_subquery(),
                episodes.scalar_subquery(),
                radio_only_artist(literal(id)),
            )
        )
        .one()
    )
    n_release_groups, n_loose, n_appearances, n_episodes, radio_only = row
    return ArtistDetail.model_validate(
        {
            **ArtistRead.model_validate(artist).model_dump(),
            "counts": ArtistCountsRead(
                release_groups=n_release_groups,
                loose_recordings=n_loose,
                appearances=n_appearances,
                radio_episodes=n_episodes,
            ),
            "radio_only": radio_only,
        }
    )


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


@router.get("/{id}/release-groups", responses=NOT_FOUND | PAGED)
def list_artist_release_groups(
    id: uuid.UUID, session: SessionDep, page: PageDep
) -> list[ReleaseGroupRead]:
    """Release groups whose artist credit includes this artist, oldest first."""
    get_or_404(session, Artist, id)
    query = select(ReleaseGroup).where(col(ReleaseGroup.artist_credit_id).in_(_credit_ids(id)))
    release_groups = session.exec(
        page.apply(session, query)
        .order_by(*release_group_order)
        .options(with_credit(ReleaseGroup.artist_credit))
    )
    return [ReleaseGroupRead.model_validate(rg) for rg in release_groups]


@router.get("/{id}/appearances", responses=NOT_FOUND | PAGED)
def list_artist_appearances(
    id: uuid.UUID, session: SessionDep, page: PageDep
) -> list[ReleaseGroupRead]:
    """Release groups the artist appears on without being credited on them: a track's
    recording credits the artist ("Bonobo feat. Rhye" on Bonobo's album), oldest first."""
    get_or_404(session, Artist, id)
    query = select(ReleaseGroup).where(*_appearances(id))
    release_groups = session.exec(
        page.apply(session, query)
        .order_by(*release_group_order)
        .options(with_credit(ReleaseGroup.artist_credit))
    )
    return [ReleaseGroupRead.model_validate(rg) for rg in release_groups]


@router.get("/{id}/recordings", responses=NOT_FOUND | PAGED)
def list_artist_recordings(
    id: uuid.UUID,
    session: SessionDep,
    page: PageDep,
    standalone: bool | None = None,
    include_radio_only: bool = False,
) -> list[PlayableRecordingRead]:
    """Recordings whose artist credit includes this artist, by title.

    ``standalone=true``: only recordings that are not a track of any release group
    (loose tracks); ``false``: only those that are; omitted: all. Recordings only known
    from radio tracklists (``ep.radio``) are left out unless ``include_radio_only``.
    Each with how it plays.
    """
    get_or_404(session, Artist, id)
    query = select(Recording).where(col(Recording.artist_credit_id).in_(_credit_ids(id)))
    if (visible := visible_recordings(include_radio_only)) is not None:
        query = query.where(visible)
    if standalone is not None:
        has_track = select(Track.id).where(Track.recording_id == Recording.id).exists()
        query = query.where(~has_track if standalone else has_track)
    recordings = session.exec(
        page.apply(session, query)
        .order_by(col(Recording.title), col(Recording.id))
        .options(with_credit(Recording.artist_credit))
    ).all()
    found = playabilities(session, recordings)
    return [
        PlayableRecordingRead.model_validate(
            {
                **RecordingRead.model_validate(recording).model_dump(),
                "playability": playability_read(found[recording.id]),
            }
        )
        for recording in recordings
    ]


add_external_id_routes(router, Artist, EntityType.artist, "")
