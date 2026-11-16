import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import MISSING
from sqlalchemy.orm import contains_eager, joinedload
from sqlmodel import Session, col, select

from ep.api.deps import SessionDep, SettingsDep, artist_credit_from_input, get_or_404, get_or_422
from ep.api.errors import CONFLICT, NOT_FOUND, unprocessable
from ep.api.external_ids import add_external_id_routes
from ep.api.release_groups import release_group_order
from ep.api.schemas import (
    FileRead,
    RadioEpisodeRead,
    RadioShowRead,
    RadioSliceRead,
    RecordingCreate,
    RecordingDetail,
    RecordingRead,
    RecordingSourceRead,
    RecordingUpdate,
    SourceRole,
)
from ep.api.sources import source_read
from ep.api.tracks import playability_read
from ep.credits import delete_artist_credit_if_unused, with_credit
from ep.external_ids import delete_external_ids
from ep.host_paths import to_host
from ep.models import (
    ArtistCredit,
    EntityType,
    RadioEpisode,
    RadioEpisodeTrack,
    Recording,
    ReleaseGroup,
    Source,
    SourceKind,
    Track,
    rel,
)
from ep.notes.store import delete_annotations
from ep.playback import (
    LINK_KINDS,
    Playback,
    Resolved,
    playabilities,
    resolve_where_flagged,
    try_resolve,
)
from ep.settings import Settings

router = APIRouter(prefix="/recordings", tags=["recordings"])


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_recording(body: RecordingCreate, session: SessionDep) -> RecordingRead:
    credit = artist_credit_from_input(session, body.artist_credit)
    recording = Recording.model_validate(
        body.model_dump(exclude={"artist_credit"}), update={"artist_credit_id": credit.id}
    )
    session.add(recording)
    session.commit()
    return RecordingRead.model_validate(recording)


@router.get("/{id}", responses=NOT_FOUND)
def get_recording(id: uuid.UUID, session: SessionDep) -> RecordingDetail:
    """A recording with the tracks (and their release groups) it appears as.

    Tracks by release group (oldest first, as on an artist), then disc and position.
    """
    recording = (
        session.exec(
            select(Recording)
            .where(Recording.id == id)
            .outerjoin(rel(Recording.tracks))
            .outerjoin(rel(Track.release_group))
            .order_by(*release_group_order, col(Track.disc), col(Track.position))
            .options(
                with_credit(Recording.artist_credit),
                contains_eager(rel(Recording.tracks))
                .contains_eager(rel(Track.release_group))
                .joinedload(rel(ReleaseGroup.artist_credit))
                .selectinload(rel(ArtistCredit.names)),
            )
        )
        .unique()
        .one_or_none()
    )
    if recording is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"recording {id} not found")
    playability = playabilities(session, [recording])[recording.id]
    return RecordingDetail.model_validate(
        {
            **RecordingRead.model_validate(recording).model_dump(),
            "tracks": recording.tracks,
            "playability": playability_read(playability),
        },
        from_attributes=True,
    )


@router.patch("/{id}", responses=NOT_FOUND | CONFLICT)
def update_recording(id: uuid.UUID, body: RecordingUpdate, session: SessionDep) -> RecordingRead:
    """Change a recording. ``preferred_source_id`` must be one of its sources that plays
    (a file, or a slice of one)."""
    recording = get_or_404(session, Recording, id)
    preferred = body.preferred_source_id
    if preferred is not MISSING and preferred is not None:
        source = get_or_422(session, Source, preferred, ("preferred_source_id",))
        if source.recording_id != id or try_resolve(source) is None:
            raise unprocessable(
                ("preferred_source_id",),
                "not a playable source of this recording",
                str(preferred),
                "value_error",
            )
    recording.sqlmodel_update(body.model_dump(exclude={"artist_credit"}))
    if body.artist_credit is not MISSING:
        old_credit = recording.artist_credit
        recording.artist_credit = artist_credit_from_input(session, body.artist_credit)
        delete_artist_credit_if_unused(session, old_credit)
    session.commit()
    return RecordingRead.model_validate(recording)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND | CONFLICT)
def delete_recording(id: uuid.UUID, session: SessionDep) -> None:
    """Delete a recording that is not a track of any release group and has no sources."""
    recording = get_or_404(session, Recording, id)
    if recording.tracks:
        raise HTTPException(status.HTTP_409_CONFLICT, "recording is still a track")
    if session.exec(select(Source.id).where(Source.recording_id == id)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "recording still has sources")
    credit = recording.artist_credit
    delete_external_ids(session, EntityType.recording, id)
    delete_annotations(session, EntityType.recording, id)
    session.delete(recording)
    delete_artist_credit_if_unused(session, credit)
    session.commit()


@router.get("/{id}/sources", responses=NOT_FOUND)
def list_recording_sources(
    id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> list[RecordingSourceRead]:
    """The sources of this recording, oldest first: what each is (its own file, a range
    of a shared file, a radio excerpt, a link), where it came from (its parent sources),
    the file it plays from (with its host path and whether it is there), the radio
    entry of an excerpt, and which one the user chose and which one plays."""
    recording = get_or_404(session, Recording, id)
    resolved = resolve_where_flagged(session, col(Source.recording_id) == id)
    playing = playabilities(session, [recording])[id].playback
    entries = _radio_entries(session, [r.radio.track_id for r in resolved if r.radio])
    return [_recording_source_read(r, recording, playing, entries, settings) for r in resolved]


def _radio_entries(
    session: Session, track_ids: list[uuid.UUID]
) -> dict[uuid.UUID, RadioEpisodeTrack]:
    if not track_ids:
        return {}
    tracks = session.exec(
        select(RadioEpisodeTrack)
        .where(col(RadioEpisodeTrack.id).in_(track_ids))
        .options(joinedload(rel(RadioEpisodeTrack.episode)).joinedload(rel(RadioEpisode.show)))
    ).all()
    return {track.id: track for track in tracks}


def _recording_source_read(
    resolved: Resolved,
    recording: Recording,
    playing: Playback | None,
    entries: dict[uuid.UUID, RadioEpisodeTrack],
    settings: Settings,
) -> RecordingSourceRead:
    source, playback = resolved.source, resolved.playback
    file: FileRead | None = None
    if playback is not None:
        node = next(
            (s for s in (source, *resolved.ancestors) if s.id == playback.file_source_id), None
        )
        if node is not None:
            file = FileRead(
                source_id=node.id,
                path=str(playback.path),
                host_path=to_host(playback.path, settings),
                exists=playback.path.is_file(),
                media_type=playback.media_type,
                codec=node.codec,
                sample_rate=node.sample_rate,
                bit_depth=node.bit_depth,
                bit_rate=node.bit_rate,
                duration_ms=node.duration_ms,
            )
    radio = None
    if resolved.radio is not None and (entry := entries.get(resolved.radio.track_id)):
        radio = RadioSliceRead(
            track_id=entry.id,
            episode=RadioEpisodeRead.model_validate(entry.episode),
            show=RadioShowRead.model_validate(entry.episode.show),
            offset_is_estimate=entry.offset_is_estimate,
        )
    if source.kind in LINK_KINDS:
        role = SourceRole.link
    elif radio is not None or resolved.radio is not None:
        role = SourceRole.radio_excerpt
    elif source.kind is SourceKind.slice:
        role = SourceRole.slice
    else:
        role = SourceRole.file
    return RecordingSourceRead(
        source=source_read(source, playback, settings),
        role=role,
        ancestors=[source_read(a, try_resolve(a), settings) for a in resolved.ancestors],
        file=file,
        radio=radio,
        preferred=recording.preferred_source_id == source.id,
        plays=playing is not None and playing.source_id == source.id,
    )


add_external_id_routes(router, Recording, EntityType.recording, "")
