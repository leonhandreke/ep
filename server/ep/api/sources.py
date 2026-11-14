import mimetypes
import uuid
from pathlib import Path
from typing import Any, Never

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlmodel import Session

from ep.api.deps import SessionDep, get_or_404, get_or_422
from ep.api.errors import CONFLICT, NOT_FOUND, ErrorRead, unprocessable
from ep.api.schemas import PlaybackRead, SourceCreate, SourceRead, SourceUpdate
from ep.models import Recording, Source, SourceKind
from ep.playback import NotPlayable, resolve
from ep.probe import ProbeError, probe

router = APIRouter(prefix="/sources", tags=["sources"])

NOT_PLAYABLE: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {"model": ErrorRead, "description": "Source has no file to play"}
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


def source_read(source: Source) -> SourceRead:
    try:
        playback = PlaybackRead.model_validate(resolve(source))
    except NotPlayable:
        playback = None
    return SourceRead.model_validate(source).model_copy(update={"playback": playback})


def _check_source(session: Session, source: Source) -> None:
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
    if source.kind == SourceKind.file and not Path(source.uri or "").is_absolute():
        invalid("uri", "a file source needs an absolute path")

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
    source.codec = source.codec or facts.codec
    source.duration_ms = source.duration_ms if source.duration_ms is not None else facts.duration_ms
    source.size = source.size if source.size is not None else facts.size


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_source(body: SourceCreate, session: SessionDep) -> SourceRead:
    """Create a source of any kind: a file (must exist), a slice of a source, a remote link."""
    source = Source.model_validate(body)
    _check_source(session, source)
    if source.kind == SourceKind.file:
        _fill_file_facts(source)
    session.add(source)
    session.commit()
    return source_read(source)


@router.get("/{id}", responses=NOT_FOUND)
def get_source(id: uuid.UUID, session: SessionDep) -> SourceRead:
    return source_read(get_or_404(session, Source, id))


@router.patch("/{id}", responses=NOT_FOUND | CONFLICT)
def update_source(id: uuid.UUID, body: SourceUpdate, session: SessionDep) -> SourceRead:
    source = get_or_404(session, Source, id)
    with session.no_autoflush:
        source.sqlmodel_update(body.model_dump())
        _check_source(session, source)
    session.commit()
    return source_read(source)


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
    | {
        status.HTTP_200_OK: {"content": {"audio/*": {}}, "description": "The whole audio file"},
        status.HTTP_206_PARTIAL_CONTENT: {
            "content": {"audio/*": {}},
            "description": "The requested byte range (Range header)",
        },
    },
)
def stream_source(id: uuid.UUID, session: SessionDep) -> FileResponse:
    """The audio file a source resolves to, with HTTP Range support.

    For a slice this is the whole underlying file: play from ``playback.start_ms`` to
    ``playback.end_ms`` (``GET /sources/{id}``). Remote-only sources are a 409.
    """
    source = get_or_404(session, Source, id)
    try:
        playback = resolve(source)
    except NotPlayable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    if not playback.path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"file not found: {playback.path}")
    return FileResponse(playback.path, media_type=audio_media_type(playback.path))
