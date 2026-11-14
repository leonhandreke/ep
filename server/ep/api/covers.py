import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from ep import covers
from ep.api.deps import SessionDep, SettingsDep, get_or_404
from ep.api.errors import NOT_FOUND, ErrorRead
from ep.api.schemas import ReleaseGroupRead
from ep.models import ReleaseGroup

router = APIRouter(prefix="/covers", tags=["covers"])


@router.get(
    "/release-group/{id}",
    response_class=FileResponse,
    responses=NOT_FOUND | {status.HTTP_200_OK: {"content": {"image/*": {}}}},
)
def get_release_group_cover(id: uuid.UUID, session: SessionDep) -> FileResponse:
    release_group = get_or_404(session, ReleaseGroup, id)
    if not release_group.cover_path or not Path(release_group.cover_path).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"release_group {id} has no cover")
    path = Path(release_group.cover_path)
    media_type = mimetypes.guess_file_type(path)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@router.put(
    "/release-group/{id}",
    responses=NOT_FOUND
    | {
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
            "model": ErrorRead,
            "description": "Not a JPEG, PNG or WebP image",
        }
    },
)
def set_release_group_cover(
    id: uuid.UUID, file: UploadFile, session: SessionDep, settings: SettingsDep
) -> ReleaseGroupRead:
    """Upload a cover image (JPEG, PNG or WebP), replacing any existing one.

    Stored under the managed library (``EP_LIBRARY_DIR/_covers``).
    """
    release_group = get_or_404(session, ReleaseGroup, id)
    try:
        covers.set_release_group_cover(release_group, file.file.read(), settings.library_dir)
    except ValueError as e:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e)) from None
    session.commit()
    return ReleaseGroupRead.model_validate(release_group)


@router.delete("/release-group/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
def delete_release_group_cover(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> None:
    release_group = get_or_404(session, ReleaseGroup, id)
    if release_group.cover_path:
        covers.remove_cover_file(release_group.cover_path, settings.library_dir)
        release_group.cover_path = None
        session.commit()
