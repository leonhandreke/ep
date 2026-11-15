import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from ep import covers
from ep.api.deps import SessionDep, SettingsDep, get_or_404
from ep.api.errors import NOT_FOUND, ErrorRead
from ep.api.schemas import ReleaseGroupRead
from ep.models import RadioEpisode, RadioShow, ReleaseGroup

router = APIRouter(prefix="/covers", tags=["covers"])


IMAGE = NOT_FOUND | {status.HTTP_200_OK: {"content": {"image/*": {}}}}


def _image(what: str, id: uuid.UUID, path_text: str | None) -> FileResponse:
    if not path_text or not Path(path_text).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} {id} has no image")
    path = Path(path_text)
    media_type = mimetypes.guess_file_type(path)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@router.get("/release-group/{id}", response_class=FileResponse, responses=IMAGE)
def get_release_group_cover(id: uuid.UUID, session: SessionDep) -> FileResponse:
    release_group = get_or_404(session, ReleaseGroup, id)
    return _image("release_group", id, release_group.cover_path)


@router.get("/radio-show/{id}", response_class=FileResponse, responses=IMAGE)
def get_radio_show_image(id: uuid.UUID, session: SessionDep) -> FileResponse:
    """A radio show's image (from the station)."""
    return _image("radio_show", id, get_or_404(session, RadioShow, id).image_path)


@router.get("/radio-episode/{id}", response_class=FileResponse, responses=IMAGE)
def get_radio_episode_image(id: uuid.UUID, session: SessionDep) -> FileResponse:
    """A radio episode's image (from the station)."""
    return _image("radio_episode", id, get_or_404(session, RadioEpisode, id).image_path)


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
