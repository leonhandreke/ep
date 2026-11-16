import mimetypes
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlmodel import Session

from ep import covers
from ep.api.deps import SessionDep, SettingsDep, get_or_404
from ep.api.errors import NOT_FOUND, ErrorRead
from ep.api.schemas import ReleaseGroupRead
from ep.models import RadioEpisode, RadioShow, ReleaseGroup
from ep.settings import Settings

router = APIRouter(prefix="/covers", tags=["covers"])


IMAGE = NOT_FOUND | {
    status.HTTP_200_OK: {"content": {"image/*": {}}},
    status.HTTP_304_NOT_MODIFIED: {"description": "Not modified (``If-None-Match``)"},
}
# Cache an image for good when asked for by its current URL (the version changes with the
# image); otherwise revalidate with the ETag (the version as well).
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"

Version = Annotated[
    str | None,
    Query(description="The image's version, as in the URL the API gives (``cover_url``)."),
]


def _image(
    what: str, id: uuid.UUID, path_text: str | None, v: str | None, request: Request
) -> Response:
    if not path_text or not Path(path_text).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} {id} has no image")
    version = covers.image_version(path_text)
    headers = {
        "ETag": f'"{version}"',
        "Cache-Control": IMMUTABLE if v == version else REVALIDATE,
    }
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    path = Path(path_text)
    media_type = mimetypes.guess_file_type(path)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers=headers)


@router.get("/release-group/{id}", response_class=FileResponse, responses=IMAGE)
def get_release_group_cover(
    id: uuid.UUID, request: Request, session: SessionDep, v: Version = None
) -> Response:
    """A release group's cover. Cached for good when ``v`` is the current version
    (the URL in ``cover_url``), otherwise revalidated by ETag."""
    release_group = get_or_404(session, ReleaseGroup, id)
    return _image("release_group", id, release_group.cover_path, v, request)


@router.get("/radio-show/{id}", response_class=FileResponse, responses=IMAGE)
def get_radio_show_image(
    id: uuid.UUID, request: Request, session: SessionDep, v: Version = None
) -> Response:
    """A radio show's image (from the station); cached as covers are."""
    show = get_or_404(session, RadioShow, id)
    return _image("radio_show", id, show.image_path, v, request)


@router.get("/radio-episode/{id}", response_class=FileResponse, responses=IMAGE)
def get_radio_episode_image(
    id: uuid.UUID, request: Request, session: SessionDep, v: Version = None
) -> Response:
    """A radio episode's image (from the station); cached as covers are."""
    episode = get_or_404(session, RadioEpisode, id)
    return _image("radio_episode", id, episode.image_path, v, request)


_IMAGE_BODY = {"schema": {"type": "string", "format": "binary"}}


@router.put(
    "/release-group/{id}",
    responses=NOT_FOUND
    | {
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
            "model": ErrorRead,
            "description": "Not a JPEG, PNG or WebP image",
        }
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The image file itself.",
            "content": {
                media_type: _IMAGE_BODY for media_type in ("image/jpeg", "image/png", "image/webp")
            },
        }
    },
)
async def set_release_group_cover(
    id: uuid.UUID, request: Request, session: SessionDep, settings: SettingsDep
) -> ReleaseGroupRead:
    """Upload a cover image (the request body is the JPEG, PNG or WebP file), replacing
    any existing one. The type is read from the image itself.

    Stored under the managed library (``EP_LIBRARY_DIR/_covers``).
    """
    data = await request.body()
    return await run_in_threadpool(_set_cover, id, data, session, settings)


def _set_cover(
    id: uuid.UUID, data: bytes, session: Session, settings: Settings
) -> ReleaseGroupRead:
    release_group = get_or_404(session, ReleaseGroup, id)
    try:
        covers.set_release_group_cover(release_group, data, settings.library_dir)
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
