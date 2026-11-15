"""Watched directories: new album folders in them are imported by the watcher.

Registering a directory records a baseline time; only folders that change after it are
imported automatically. ``POST /watch-dirs/{id}/scan`` queues imports for the folders
already there that are not imported or queued yet.
"""

import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, status
from sqlmodel import col, select

from ep.api.deps import SessionDep, SettingsDep, get_or_404
from ep.api.errors import CONFLICT, NOT_FOUND, unprocessable
from ep.api.imports import check_folder_input, job_summary
from ep.api.schemas import (
    ImportJobSummary,
    WatchDirCreate,
    WatchDirRead,
    WatchDirScan,
    WatchDirUpdate,
)
from ep.jobs.watcher import folders_to_import, queue_folders
from ep.models import WatchDir
from ep.models.base import utcnow

router = APIRouter(prefix="/watch-dirs", tags=["watch dirs"])


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_watch_dir(
    body: WatchDirCreate, session: SessionDep, settings: SettingsDep
) -> WatchDirRead:
    """Watch a directory (under the media roots). What is in it already is not imported:
    see ``scan_watch_dir``."""
    path = check_folder_input(body.path, settings, ("path",))
    watch_dir = WatchDir(path=str(path), enabled=body.enabled, baseline_at=utcnow())
    session.add(watch_dir)
    session.commit()
    return WatchDirRead.model_validate(watch_dir)


@router.get("")
def list_watch_dirs(session: SessionDep) -> list[WatchDirRead]:
    watch_dirs = session.exec(select(WatchDir).order_by(col(WatchDir.path))).all()
    return [WatchDirRead.model_validate(watch_dir) for watch_dir in watch_dirs]


@router.get("/{id}", responses=NOT_FOUND)
def get_watch_dir(id: uuid.UUID, session: SessionDep) -> WatchDirRead:
    return WatchDirRead.model_validate(get_or_404(session, WatchDir, id))


@router.patch("/{id}", responses=NOT_FOUND)
def update_watch_dir(id: uuid.UUID, body: WatchDirUpdate, session: SessionDep) -> WatchDirRead:
    """Enable or disable watching. The baseline stays: folders that changed while the
    directory was disabled are imported once it is enabled again."""
    watch_dir = get_or_404(session, WatchDir, id)
    watch_dir.sqlmodel_update(body.model_dump())
    session.commit()
    return WatchDirRead.model_validate(watch_dir)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
def delete_watch_dir(id: uuid.UUID, session: SessionDep) -> None:
    """Stop watching. Imported files and import jobs stay."""
    session.delete(get_or_404(session, WatchDir, id))
    session.commit()


@router.post("/{id}/scan", responses=NOT_FOUND)
def scan_watch_dir(
    id: uuid.UUID, body: WatchDirScan, session: SessionDep, settings: SettingsDep
) -> list[ImportJobSummary]:
    """Queue folder imports for the album folders in the watch dir (or in ``path``, a
    directory inside it) that are not imported or queued yet, however old: the explicit
    way to import what was there before the directory was registered. Folders changed
    within the settle period are left to the watcher. Returns the queued jobs."""
    watch_dir = get_or_404(session, WatchDir, id)
    under = None
    if body.path is not None:
        under = check_folder_input(body.path, settings, ("path",))
        if not under.resolve().is_relative_to(Path(watch_dir.path).resolve()):
            raise unprocessable(("path",), "not inside the watch dir", body.path, "value_error")
    settle = timedelta(seconds=settings.watch_settle_seconds)
    folders = folders_to_import(
        session, watch_dir, utcnow(), settle, under=under, use_baseline=False
    )
    jobs = queue_folders(session, folders)
    session.commit()
    return [job_summary(job) for job in jobs]
