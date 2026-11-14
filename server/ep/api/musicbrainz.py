"""MusicBrainz search and release import (metadata only), for trying imports by hand."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ep.api.deps import SessionDep
from ep.api.errors import BAD_GATEWAY, CONFLICT, NOT_FOUND
from ep.api.schemas import (
    MusicBrainzHitRead,
    MusicBrainzImport,
    MusicBrainzSearchType,
    ReleaseGroupRead,
)
from ep.importers.musicbrainz import ReleaseConflict, import_release
from ep.metadata.musicbrainz import (
    MusicBrainz,
    MusicBrainzError,
    MusicBrainzNotFound,
    escape_lucene,
    render_artist_credit,
)

router = APIRouter(prefix="/musicbrainz", tags=["musicbrainz"])


def get_musicbrainz() -> Iterator[MusicBrainz]:
    """FastAPI dependency: a MusicBrainz client for the request."""
    with MusicBrainz() as client:
        yield client


MusicBrainzDep = Annotated[MusicBrainz, Depends(get_musicbrainz)]


@router.get("/search", responses=BAD_GATEWAY)
def search_musicbrainz(
    musicbrainz: MusicBrainzDep,
    type: MusicBrainzSearchType,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> list[MusicBrainzHitRead]:
    """Search MusicBrainz for artists, releases or recordings (``q`` is plain text)."""
    query = escape_lucene(q)
    try:
        match type:
            case MusicBrainzSearchType.artist:
                return [
                    MusicBrainzHitRead(
                        type=type,
                        mbid=artist.id,
                        title=artist.name,
                        artist_credit=None,
                        disambiguation=artist.disambiguation,
                        date=None,
                        score=artist.score,
                    )
                    for artist in musicbrainz.search_artists(query, limit)
                ]
            case MusicBrainzSearchType.release:
                return [
                    MusicBrainzHitRead(
                        type=type,
                        mbid=release.id,
                        title=release.title,
                        artist_credit=render_artist_credit(release.artist_credit),
                        disambiguation=release.disambiguation,
                        date=release.date,
                        score=release.score,
                    )
                    for release in musicbrainz.search_releases(query, limit)
                ]
            case MusicBrainzSearchType.recording:
                return [
                    MusicBrainzHitRead(
                        type=type,
                        mbid=recording.id,
                        title=recording.title,
                        artist_credit=render_artist_credit(recording.artist_credit),
                        disambiguation=recording.disambiguation,
                        date=recording.first_release_date,
                        score=recording.score,
                    )
                    for recording in musicbrainz.search_recordings(query, limit)
                ]
    except MusicBrainzError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e


@router.post("/import", responses=NOT_FOUND | CONFLICT | BAD_GATEWAY)
def import_musicbrainz_release(
    body: MusicBrainzImport, session: SessionDep, musicbrainz: MusicBrainzDep
) -> ReleaseGroupRead:
    """Import a release into the catalogue: artists, release group, recordings, tracks, ISRCs.

    Metadata only (no files, no cover). Rows with known MBIDs are reused; importing the
    same release again adds only what is missing. 409 if the release group was already
    imported from a different release.
    """
    try:
        release = musicbrainz.get_release(body.release_mbid)
    except MusicBrainzNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except MusicBrainzError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    try:
        release_group = import_release(session, release)
    except ReleaseConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    session.commit()
    return ReleaseGroupRead.model_validate(release_group)
