"""Watched directories: new album folders in them are imported by the watcher.

Registering a directory records a baseline time; only folders that change after it are
imported automatically. ``POST /watch-dirs/{id}/scan`` queues imports for the folders
already there that are not imported or queued yet.
"""

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, status
from sqlalchemy import ColumnElement
from sqlmodel import Session, col, func, select

from ep.api.deps import SessionDep, SettingsDep, get_or_404
from ep.api.errors import CONFLICT, NOT_FOUND, unprocessable
from ep.api.imports import check_folder_input, job_summaries
from ep.api.schemas import (
    ImportJobSummary,
    WatchDirCreate,
    WatchDirRead,
    WatchDirScan,
    WatchDirStatus,
    WatchDirUpdate,
)
from ep.host_paths import from_host, to_host
from ep.jobs.watcher import folders_to_import, queue_folders
from ep.models import ImportJob, JobState, WatchDir
from ep.models.base import utcnow
from ep.settings import Settings

router = APIRouter(prefix="/watch-dirs", tags=["watch dirs"])


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_watch_dir(
    body: WatchDirCreate, session: SessionDep, settings: SettingsDep
) -> WatchDirRead:
    """Watch a directory (under the media roots). What is in it already is not imported:
    see ``scan_watch_dir``."""
    path = check_folder_input(from_host(body.path, settings), settings, ("path",))
    watch_dir = WatchDir(path=str(path), enabled=body.enabled, baseline_at=utcnow())
    session.add(watch_dir)
    session.commit()
    return _read(session, settings, col(WatchDir.id) == watch_dir.id)[0]


def _read(session: Session, settings: Settings, *where: ColumnElement[bool]) -> list[WatchDirRead]:
    """Watch dirs by path, each with its status and when its watcher last queued an
    import (one query)."""
    jobs = select(ImportJob).where(col(ImportJob.watch_dir_id) == col(WatchDir.id))
    last = select(func.max(ImportJob.created_at)).where(
        col(ImportJob.watch_dir_id) == col(WatchDir.id)
    )
    active = jobs.where(
        col(ImportJob.state).in_((JobState.queued, JobState.running, JobState.applying))
    ).exists()
    rows = session.exec(
        select(WatchDir, last.scalar_subquery(), active).where(*where).order_by(col(WatchDir.path))
    ).all()
    reads = []
    for watch_dir, last_import_at, importing in rows:
        if not watch_dir.enabled:
            status_ = WatchDirStatus.paused
        elif importing:
            status_ = WatchDirStatus.importing
        else:
            status_ = WatchDirStatus.idle
        reads.append(
            WatchDirRead.model_validate(
                {
                    **watch_dir.model_dump(),
                    "host_path": to_host(watch_dir.path, settings),
                    "status": status_,
                    "last_import_at": last_import_at,
                }
            )
        )
    return reads


@router.get("")
def list_watch_dirs(session: SessionDep, settings: SettingsDep) -> list[WatchDirRead]:
    """Watched directories by path, with their status (paused, importing, idle) and
    when they last queued an import."""
    return _read(session, settings)


@router.get("/{id}", responses=NOT_FOUND)
def get_watch_dir(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> WatchDirRead:
    get_or_404(session, WatchDir, id)
    return _read(session, settings, col(WatchDir.id) == id)[0]


@router.patch("/{id}", responses=NOT_FOUND)
def update_watch_dir(
    id: uuid.UUID, body: WatchDirUpdate, session: SessionDep, settings: SettingsDep
) -> WatchDirRead:
    """Enable or disable watching. The baseline stays: folders that changed while the
    directory was disabled are imported once it is enabled again."""
    watch_dir = get_or_404(session, WatchDir, id)
    watch_dir.sqlmodel_update(body.model_dump())
    session.commit()
    return _read(session, settings, col(WatchDir.id) == id)[0]


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
def delete_watch_dir(id: uuid.UUID, session: SessionDep) -> None:
    """Stop watching. Imported files and import jobs stay."""
    session.delete(get_or_404(session, WatchDir, id))
    session.commit()


# Without a body: everything.
_SCAN_ALL = WatchDirScan()


@router.post("/{id}/scan", responses=NOT_FOUND)
def scan_watch_dir(
    id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    body: Annotated[WatchDirScan, Body()] = _SCAN_ALL,
) -> list[ImportJobSummary]:
    """Queue folder imports for the album folders in the watch dir (or in ``path``, a
    directory inside it) that are not imported or queued yet, however old: the explicit
    way to import what was there before the directory was registered. Folders changed
    within the settle period are left to the watcher. Returns the queued jobs."""
    watch_dir = get_or_404(session, WatchDir, id)
    under = None
    path = body.path
    if path is not None:
        under = check_folder_input(from_host(path, settings), settings, ("path",))
        if not under.resolve().is_relative_to(Path(watch_dir.path).resolve()):
            raise unprocessable(("path",), "not inside the watch dir", path, "value_error")
    settle = timedelta(seconds=settings.watch_settle_seconds)
    folders = folders_to_import(
        session, watch_dir, utcnow(), settle, under=under, use_baseline=False
    )
    jobs = queue_folders(session, folders, watch_dir)
    session.commit()
    return job_summaries(session, settings, jobs)
