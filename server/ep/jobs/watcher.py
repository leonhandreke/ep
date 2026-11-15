"""The watcher: folder import jobs for new album folders in the watched directories.

Runs in the worker process (``ep.jobs.worker``) next to the Procrastinate worker: it only
reads the file system and queues jobs, and that way the three compose services of the
plan stay as they are and the watcher stops and starts with the jobs it feeds.

- ``watchfiles`` in polling mode (Docker bind mounts on macOS drop file system events)
  reports changed paths; their album folders are checked once they have been quiet for
  ``watch_settle_seconds``.
- Every ``watch_rescan_seconds`` all watched directories are rescanned, which catches
  anything the polling missed (and the time the worker was down).

An *album folder* is a directory with audio files in it (a disc folder such as "CD1"
counts as part of its parent). It is imported automatically when

- it last changed (newest mtime/ctime of the folder and everything in it) after the watch
  dir's ``baseline_at``: registering a directory does not import what is already there
  (``POST /watch-dirs/{id}/scan`` does that, on request);
- it has been quiet for the settle period;
- some of its audio files are not sources yet, and no folder import job for it (or a
  folder above it) was created since it last changed. A dismissed or finished job counts:
  skipped files are not offered again until the folder changes.

"Already imported / already queued" is a query on ``source`` and ``import_job``.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import watchfiles
from sqlalchemy import Engine, exists, func, literal, or_
from sqlmodel import Session, col, select

from ep.importers.folder import AUDIO_EXTENSIONS, audio_files
from ep.jobs.queue import create_import
from ep.models import ImportJob, ImportKind, Source, SourceKind, WatchDir
from ep.models.base import utcnow
from ep.settings import Settings

logger = logging.getLogger(__name__)

_DISC_FOLDER = re.compile(r"^(cd|disc|disk)\s*\d+$", re.IGNORECASE)
# How often the watcher checks settled folders and the rescan timer (seconds).
TICK_SECONDS = 5.0


def album_folder(path: Path, root: Path) -> Path:
    """The album folder of an audio file under ``root``: its directory, or the parent of
    a disc folder ("CD1", "Disc 2")."""
    folder = path.parent
    if folder != root and _DISC_FOLDER.match(folder.name):
        folder = folder.parent
    return folder


def album_folders(directory: Path, root: Path) -> list[Path]:
    """The album folders at or below ``directory`` (excluding the watch root itself)."""
    return sorted({album_folder(f, root) for f in audio_files(directory)} - {root})


def last_change(folder: Path) -> datetime:
    """When the folder or anything in it last changed: the newest mtime or ctime (ctime
    also moves when a file is copied in with its old mtime preserved)."""
    newest = 0.0
    for path in [folder, *folder.rglob("*")]:
        stat = path.stat()
        newest = max(newest, stat.st_mtime, stat.st_ctime)
    return datetime.fromtimestamp(newest, UTC)


def needs_import(session: Session, folder: Path, changed_at: datetime) -> bool:
    paths = [str(path) for path in audio_files(folder)]
    if not paths:
        return False
    imported = session.exec(
        select(func.count())
        .select_from(Source)
        .where(Source.kind == SourceKind.file, col(Source.uri).in_(paths))
    ).one()
    if imported == len(paths):
        return False
    text = str(folder)
    covering = or_(
        col(ImportJob.input) == text,
        func.starts_with(literal(text), col(ImportJob.input) + "/"),
    )
    queued_since = exists().where(
        col(ImportJob.kind) == ImportKind.folder,
        covering,
        col(ImportJob.created_at) >= changed_at,
    )
    return not session.scalar(select(queued_since))


def folders_to_import(
    session: Session,
    watch_dir: WatchDir,
    now: datetime,
    settle: timedelta,
    *,
    under: Path | None = None,
    use_baseline: bool = True,
) -> list[Path]:
    """Album folders of the watch dir (or of ``under``, a path in it) to import now: see
    the module docstring. ``use_baseline=False`` includes folders older than the
    baseline (the explicit scan)."""
    root = Path(watch_dir.path)
    folders = []
    for folder in album_folders(under or root, root):
        try:
            changed = last_change(folder)
        except OSError:  # removed while scanning
            continue
        if now - changed < settle:
            continue
        if use_baseline and changed <= watch_dir.baseline_at:
            continue
        if needs_import(session, folder, changed):
            folders.append(folder)
    return folders


def queue_folders(session: Session, folders: Iterable[Path]) -> list[ImportJob]:
    return [create_import(session, ImportKind.folder, str(folder)) for folder in folders]


class Watcher:
    """Tracks changed folders between polls; ``due`` and ``rescan`` queue the imports."""

    def __init__(self, settings: Settings) -> None:
        self.settle = timedelta(seconds=settings.watch_settle_seconds)
        self.poll_seconds = settings.watch_poll_seconds
        self.rescan_every = timedelta(seconds=settings.watch_rescan_seconds)
        # Changed folder -> the watch root it is in.
        self.dirty: dict[Path, Path] = {}

    @staticmethod
    def watch_dirs(session: Session) -> list[WatchDir]:
        return list(session.exec(select(WatchDir).where(col(WatchDir.enabled))).all())

    def changed(self, roots: Iterable[Path], paths: Iterable[Path]) -> None:
        """Note changed (added, modified, deleted) paths under the watch roots."""
        roots = list(roots)
        for path in paths:
            root = next((r for r in roots if path.is_relative_to(r) and path != r), None)
            if root is None:
                continue
            if path.suffix.lower() in AUDIO_EXTENSIONS or not path.is_dir():
                folder = album_folder(path, root)
            else:
                folder = path
            if folder != root:
                self.dirty[folder] = root

    def due(self, session: Session, now: datetime) -> list[ImportJob]:
        """Queue imports for changed folders that have settled; commits."""
        dirs = {Path(watch_dir.path): watch_dir for watch_dir in self.watch_dirs(session)}
        jobs = []
        for folder, root in list(self.dirty.items()):
            watch_dir = dirs.get(root)
            try:
                if watch_dir is None or now - last_change(folder) < self.settle:
                    if watch_dir is None:
                        del self.dirty[folder]
                    continue
            except OSError:  # gone again
                del self.dirty[folder]
                continue
            del self.dirty[folder]
            folders = folders_to_import(session, watch_dir, now, self.settle, under=folder)
            jobs += queue_folders(session, folders)
        session.commit()
        return jobs

    def rescan(self, session: Session, now: datetime) -> list[ImportJob]:
        """Queue imports for every folder of every enabled watch dir that needs one."""
        jobs = []
        for watch_dir in self.watch_dirs(session):
            if Path(watch_dir.path).is_dir():
                jobs += queue_folders(
                    session, folders_to_import(session, watch_dir, now, self.settle)
                )
            else:
                logger.warning("watched directory %s is missing", watch_dir.path)
        session.commit()
        return jobs

    # The loop

    async def run(self, engine: Engine) -> None:
        """Watch until cancelled; restarts after errors."""
        while True:
            try:
                await self._watch(engine)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("watcher failed; restarting in %s s", self.poll_seconds)
                await asyncio.sleep(self.poll_seconds)

    async def _watch(self, engine: Engine) -> None:
        next_rescan = utcnow()

        def tick(now: datetime, rescan: bool) -> list[tuple[uuid.UUID, str]]:
            with Session(engine) as session:
                jobs = self.due(session, now)
                if rescan:
                    jobs += self.rescan(session, now)
                return [(job.id, job.input) for job in jobs]

        def roots() -> list[Path]:
            with Session(engine) as session:
                return sorted(
                    Path(watch_dir.path)
                    for watch_dir in self.watch_dirs(session)
                    if Path(watch_dir.path).is_dir()
                )

        while True:
            watched = await asyncio.to_thread(roots)
            stop = asyncio.Event()
            changes_iter = (
                watchfiles.awatch(
                    *watched,
                    force_polling=True,
                    poll_delay_ms=int(self.poll_seconds * 1000),
                    rust_timeout=int(TICK_SECONDS * 1000),
                    yield_on_timeout=True,
                    stop_event=stop,
                )
                if watched
                else _idle()
            )
            async for changes in changes_iter:
                self.changed(watched, (Path(path) for _, path in changes))
                now = utcnow()
                rescan = now >= next_rescan
                if rescan:
                    next_rescan = now + self.rescan_every
                for job_id, folder in await asyncio.to_thread(tick, now, rescan):
                    logger.info("queued folder import %s for %s", job_id, folder)
                if await asyncio.to_thread(roots) != watched:
                    stop.set()
                    break


async def _idle() -> AsyncIterator[set[tuple[watchfiles.Change, str]]]:
    """No directory to watch: wake up every tick to check for new watch dirs."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        yield set()
