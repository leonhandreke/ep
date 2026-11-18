"""MusicBrainz search, and adding artists, releases and recordings from it (metadata
only): entities created by hand while writing a note, or imports tried by hand."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from ep.api.deps import MusicBrainzDep, SessionDep
from ep.api.entities import entity_reads
from ep.api.errors import BAD_GATEWAY, CONFLICT, NOT_FOUND
from ep.api.schemas import (
    EntityRead,
    MusicBrainzHitRead,
    MusicBrainzImport,
    MusicBrainzSearchType,
)
from ep.importers.musicbrainz import (
    ReleaseConflict,
    import_artist,
    import_recording,
    import_release,
)
from ep.metadata.musicbrainz import (
    MusicBrainzError,
    MusicBrainzNotFound,
    escape_lucene,
    render_artist_credit,
)
from ep.models import Artist, Recording, ReleaseGroup

router = APIRouter(prefix="/musicbrainz", tags=["musicbrainz"])


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
def import_from_musicbrainz(
    body: MusicBrainzImport, session: SessionDep, musicbrainz: MusicBrainzDep
) -> EntityRead:
    """Add an artist, a release or a recording from MusicBrainz to the catalogue, as
    metadata only (no files, no cover), and return it (with the link to write for it).

    An artist or recording with the MBID in the library is returned as it is. A
    release becomes a release group with its artists, recordings, tracks and ISRCs; rows
    with known MBIDs are reused, and importing the same release again adds only what is
    missing (409 if the release group was imported from a different release). A
    recording becomes a standalone recording (no track) with its artists and ISRCs.
    """
    entity: Artist | ReleaseGroup | Recording
    try:
        match body.type:
            case MusicBrainzSearchType.artist:
                entity = import_artist(session, musicbrainz.get_artist(body.mbid))
            case MusicBrainzSearchType.recording:
                entity = import_recording(session, musicbrainz.get_recording(body.mbid))
            case MusicBrainzSearchType.release:
                release = musicbrainz.get_release(body.mbid)
                try:
                    entity = import_release(session, release)
                except ReleaseConflict as e:
                    raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except MusicBrainzNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except MusicBrainzError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    session.commit()
    return entity_reads(session, [entity])[entity.id]
