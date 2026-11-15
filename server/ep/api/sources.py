import mimetypes
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Never

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlmodel import Session

from ep.api.deps import SessionDep, SettingsDep, get_or_404, get_or_422
from ep.api.errors import CONFLICT, NOT_FOUND, ErrorRead, unprocessable
from ep.api.schemas import PlaybackRead, SourceCreate, SourceRead, SourceUpdate
from ep.host_paths import from_host, to_host
from ep.media_roots import is_within_media_roots
from ep.models import Recording, Source, SourceKind
from ep.playback import NotPlayable, Playback, resolve, try_resolve
from ep.probe import ProbeError, probe
from ep.settings import Settings

router = APIRouter(prefix="/sources", tags=["sources"])

NOT_PLAYABLE: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {"model": ErrorRead, "description": "Source has no file to play"}
}
OUTSIDE_MEDIA_ROOTS: dict[int | str, dict[str, Any]] = {
    status.HTTP_403_FORBIDDEN: {
        "model": ErrorRead,
        "description": "The file is outside the configured media roots",
    }
}

# Types AVPlayer recognises; Python's table has e.g. audio/mp4a-latm for .m4a.
_AUDIO_TYPES = {
    ".aac": "audio/aac",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}


def audio_media_type(path: Path) -> str:
    return (
        _AUDIO_TYPES.get(path.suffix.lower())
        or mimetypes.guess_file_type(path)[0]
        or "application/octet-stream"
    )


def source_read(source: Source, playback: Playback | None, settings: Settings) -> SourceRead:
    """A source with how to play it (``try_resolve``, or ``resolve_where`` for lists)."""
    host_path = to_host(source.uri, settings) if source.kind is SourceKind.file else None
    return SourceRead.model_validate(source).model_copy(
        update={
            "playback": playback and PlaybackRead.model_validate(playback),
            "host_path": host_path,
        }
    )


def _check_source(session: Session, source: Source, media_roots: Sequence[Path]) -> None:
    """Validate a new or edited source as a whole; 422 at the offending field."""

    def invalid(field: str, msg: str) -> Never:
        raise unprocessable((field,), msg, getattr(source, field), type="value_error")

    is_slice = source.kind == SourceKind.slice
    if is_slice:
        if source.uri is not None:
            invalid("uri", "a slice has no uri")
        if source.parent_id is None:
            invalid("parent_id", "a slice needs a parent")
        if source.start_ms is None:
            invalid("start_ms", "a slice needs start_ms")
        if source.end_ms is not None and source.end_ms <= (source.start_ms or 0):
            invalid("end_ms", "end_ms must be after start_ms")
    else:
        if source.uri is None:
            invalid("uri", f"a {source.kind} source needs a uri")
        for field in ("start_ms", "end_ms"):
            if getattr(source, field) is not None:
                invalid(field, "only a slice has a time range")
    if source.kind == SourceKind.file:
        path = Path(source.uri or "")
        if not path.is_absolute():
            invalid("uri", "a file source needs an absolute path")
        if not is_within_media_roots(path, media_roots):
            invalid("uri", "a file source must be under one of the media roots")

    if source.recording_id is not None:
        get_or_422(session, Recording, source.recording_id, ("recording_id",))
    ancestor = source.parent_id
    while ancestor is not None:
        if ancestor == source.id:
            invalid("parent_id", "a source cannot derive from itself")
        ancestor = get_or_422(session, Source, ancestor, ("parent_id",)).parent_id


def _fill_file_facts(source: Source) -> None:
    """Read codec, duration and size of a new file source from the file itself."""
    try:
        facts = probe(Path(source.uri or ""))
    except ProbeError as e:
        raise unprocessable(("uri",), str(e), source.uri, type="value_error") from None
    for name, value in facts.audio_facts().items():
        if getattr(source, name) is None:
            setattr(source, name, value)


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_source(body: SourceCreate, session: SessionDep, settings: SettingsDep) -> SourceRead:
    """Create a source of any kind: a file (must exist), a slice of a source, a remote link.

    A file must lie under one of the media roots (``EP_MEDIA_ROOTS``); a host path in a
    directory of ``EP_HOST_PATHS`` is taken as the server path it maps to. Its codec,
    duration, size and stream facts are read from the file.
    """
    source = Source.model_validate(body)
    if source.kind is SourceKind.file and source.uri:
        source.uri = from_host(source.uri, settings)
    _check_source(session, source, settings.media_roots)
    if source.kind == SourceKind.file:
        _fill_file_facts(source)
    session.add(source)
    session.commit()
    return source_read(source, try_resolve(source), settings)


@router.get("/{id}", responses=NOT_FOUND)
def get_source(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> SourceRead:
    source = get_or_404(session, Source, id)
    return source_read(source, try_resolve(source), settings)


@router.patch("/{id}", responses=NOT_FOUND | CONFLICT)
def update_source(
    id: uuid.UUID, body: SourceUpdate, session: SessionDep, settings: SettingsDep
) -> SourceRead:
    """Change a source; a file's new ``uri`` (to locate a moved file) may be a host path
    (``EP_HOST_PATHS``)."""
    source = get_or_404(session, Source, id)
    with session.no_autoflush:
        source.sqlmodel_update(body.model_dump())
        if source.kind is SourceKind.file and source.uri:
            source.uri = from_host(source.uri, settings)
        _check_source(session, source, settings.media_roots)
    session.commit()
    return source_read(source, try_resolve(source), settings)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND | CONFLICT)
def delete_source(id: uuid.UUID, session: SessionDep) -> None:
    """Delete a source with no derived sources. Never deletes the file on disk."""
    source = get_or_404(session, Source, id)
    if source.children:
        raise HTTPException(status.HTTP_409_CONFLICT, "source still has derived sources")
    session.delete(source)
    session.commit()


@router.get(
    "/{id}/stream",
    response_class=FileResponse,
    responses=NOT_FOUND
    | NOT_PLAYABLE
    | OUTSIDE_MEDIA_ROOTS
    | {
        status.HTTP_200_OK: {"content": {"audio/*": {}}, "description": "The whole audio file"},
        status.HTTP_206_PARTIAL_CONTENT: {
            "content": {"audio/*": {}},
            "description": "The requested byte range (Range header)",
        },
    },
)
def stream_source(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> FileResponse:
    """The audio file a source resolves to, with HTTP Range support.

    For a slice this is the whole underlying file: play from ``playback.start_ms`` to
    ``playback.end_ms`` (``GET /sources/{id}``). Remote-only sources are a 409, files
    outside the media roots (``EP_MEDIA_ROOTS``) a 403.
    """
    source = get_or_404(session, Source, id)
    try:
        playback = resolve(source)
    except NotPlayable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    # Checked again here: the roots may have changed, or a symlink been swapped in.
    if not is_within_media_roots(playback.path, settings.media_roots):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "file is outside the media roots")
    if not playback.path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"file not found: {playback.path}")
    return FileResponse(playback.path, media_type=audio_media_type(playback.path))
