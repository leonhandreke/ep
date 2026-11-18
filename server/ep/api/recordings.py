import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import MISSING
from sqlalchemy import select as sa_select
from sqlalchemy.orm import contains_eager, joinedload
from sqlmodel import Session, col, select

from ep.api.deps import SessionDep, SettingsDep, artist_credit_from_input, get_or_404, get_or_422
from ep.api.errors import CONFLICT, NOT_FOUND, unprocessable
from ep.api.external_ids import add_external_id_routes
from ep.api.release_groups import release_group_order
from ep.api.schemas import (
    FileRead,
    PreferredSourceWrite,
    RadioEpisodeRead,
    RadioShowRead,
    RadioSliceRead,
    RecordingCreate,
    RecordingDetail,
    RecordingRead,
    RecordingSourceCreate,
    RecordingSourceRead,
    RecordingUpdate,
    SourceImportRead,
    SourceRole,
    SourceUpdate,
)
from ep.api.sources import add_source, change_source, source_read
from ep.api.tracks import playability_read
from ep.credits import delete_artist_credit_if_unused, with_credit
from ep.external_ids import delete_external_ids
from ep.host_paths import to_host
from ep.models import (
    ArtistCredit,
    EntityType,
    ImportJob,
    ImportKind,
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
    """Change a recording (its preferred source: ``PUT /recordings/{id}/preferred-source``)."""
    recording = get_or_404(session, Recording, id)
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
    entry of an excerpt, whether it can be chosen to play, whether the user chose it and
    which one will play.

    The changes made from this list (``POST /recordings/{id}/sources``,
    ``PATCH /recordings/{id}/sources/{source_id}``,
    ``PUT /recordings/{id}/preferred-source``) answer with it."""
    return _recording_source_reads(session, get_or_404(session, Recording, id), settings)


@router.post("/{id}/sources", status_code=status.HTTP_201_CREATED, responses=NOT_FOUND | CONFLICT)
def add_recording_source(
    id: uuid.UUID, body: RecordingSourceCreate, session: SessionDep, settings: SettingsDep
) -> list[RecordingSourceRead]:
    """Add a source to this recording (as ``POST /sources``: a file, a slice, a link).
    Returns the recording's sources."""
    recording = get_or_404(session, Recording, id)
    add_source(session, body, settings, recording.id)
    session.commit()
    return _recording_source_reads(session, recording, settings)


@router.patch("/{id}/sources/{source_id}", responses=NOT_FOUND | CONFLICT)
def update_recording_source(
    id: uuid.UUID,
    source_id: uuid.UUID,
    body: SourceUpdate,
    session: SessionDep,
    settings: SettingsDep,
) -> list[RecordingSourceRead]:
    """Change a source the recording plays from, as ``PATCH /sources/{id}`` does: one of
    its sources, or a source one derives from (locating a moved album or episode file
    shared with other recordings). Returns the recording's sources."""
    recording = get_or_404(session, Recording, id)
    resolved = resolve_where_flagged(session, col(Source.recording_id) == id)
    if source_id not in {s.id for r in resolved for s in (r.source, *r.ancestors)}:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"recording {id} plays from no source {source_id}"
        )
    change_source(session, get_or_404(session, Source, source_id), body, settings)
    session.commit()
    return _recording_source_reads(session, recording, settings)


@router.put("/{id}/preferred-source", responses=NOT_FOUND)
def set_preferred_source(
    id: uuid.UUID, body: PreferredSourceWrite, session: SessionDep, settings: SettingsDep
) -> list[RecordingSourceRead]:
    """Choose which source plays (one of the recording's ``playable`` sources), or the
    default choice (``source_id`` null): the oldest library source whose file is there,
    else the oldest radio excerpt. Returns the recording's sources."""
    recording = get_or_404(session, Recording, id)
    if body.source_id is not None:
        source = get_or_422(session, Source, body.source_id, ("source_id",))
        if source.recording_id != id or try_resolve(source) is None:
            raise unprocessable(
                ("source_id",),
                "not a playable source of this recording",
                str(body.source_id),
                "value_error",
            )
    recording.preferred_source_id = body.source_id
    session.commit()
    return _recording_source_reads(session, recording, settings)


def _recording_source_reads(
    session: Session, recording: Recording, settings: Settings
) -> list[RecordingSourceRead]:
    resolved = resolve_where_flagged(session, col(Source.recording_id) == recording.id)
    playing = playabilities(session, [recording])[recording.id].playback
    entries = _radio_entries(session, [r.radio.track_id for r in resolved if r.radio])
    job_ids = {r.source.id: _import_job_id(r) for r in resolved}
    jobs = _import_jobs(session, {id for id in job_ids.values() if id is not None}, settings)
    return [
        _recording_source_read(
            r, recording, playing, entries, jobs.get(job_ids[r.source.id]), settings
        )
        for r in resolved
    ]


def _import_job_id(resolved: Resolved) -> uuid.UUID | None:
    """The import that added the source: recorded on the file it plays from
    (``Source.meta``), else on the nearest source of its chain that has one."""
    chain = [resolved.source, *resolved.ancestors]
    if (playback := resolved.playback) is not None:
        chain.sort(key=lambda s: s.id != playback.file_source_id)  # the file first
    for source in chain:
        try:
            return uuid.UUID(source.meta["import_job_id"])
        except KeyError, TypeError, ValueError:
            continue
    return None


def _import_jobs(
    session: Session, ids: set[uuid.UUID], settings: Settings
) -> dict[uuid.UUID, SourceImportRead]:
    if not ids:
        return {}
    rows = session.connection().execute(  # not the proposal and log
        sa_select(
            col(ImportJob.id),
            col(ImportJob.created_at),
            col(ImportJob.updated_at),
            col(ImportJob.kind),
            col(ImportJob.input),
            col(ImportJob.watch_dir_id),
            col(ImportJob.state),
        ).where(col(ImportJob.id).in_(ids))
    )
    return {
        id: SourceImportRead(
            id=id,
            created_at=created_at,
            updated_at=updated_at,
            kind=kind,
            input=input,
            input_host_path=to_host(input, settings) if kind is ImportKind.folder else None,
            watch_dir_id=watch_dir_id,
            state=state,
        )
        for id, created_at, updated_at, kind, input, watch_dir_id, state in rows
    }


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
    imported_by: SourceImportRead | None,
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
                channels=node.channels,
                duration_ms=node.duration_ms,
                size=node.size,
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
        imported_by=imported_by,
        radio=radio,
        playable=playback is not None,
        preferred=recording.preferred_source_id == source.id,
        will_play=playing is not None and playing.source_id == source.id,
    )


add_external_id_routes(router, Recording, EntityType.recording, "")
