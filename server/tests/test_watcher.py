"""The watcher: baseline, settle period, "already imported or queued", explicit scans."""

import os
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from sqlmodel import Session, select

from ep.jobs.watcher import Watcher, album_folders, last_change
from ep.models import ImportJob, JobState
from ep.models.base import utcnow
from ep.settings import Settings
from tests.imports import run_queued, write_audio

SETTLE = timedelta(seconds=60)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """No settle period for the API's explicit scans (the watcher gets ``SETTLE``)."""
    return settings.model_copy(update={"watch_settle_seconds": 0.0})


@pytest.fixture
def library(settings: Settings) -> Path:
    """The watched directory, with one album in it from before it was registered."""
    root = settings.media_roots[2] / "Library"
    write_audio(root / "Old Artist" / "Old Album" / "1.flac", 1, {"title": "Old", "album": "Old"})
    return root


@pytest.fixture
def watch_dir(client: TestClient, library: Path) -> dict[str, Any]:
    response = client.post("/watch-dirs", json={"path": str(library)})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def watcher(settings: Settings) -> Watcher:
    return Watcher(settings.model_copy(update={"watch_settle_seconds": SETTLE.total_seconds()}))


def new_album(library: Path, name: str = "New Album") -> Path:
    album = library / "New Artist" / name
    write_audio(album / "1.flac", 1, {"title": "One", "album": name, "artist": "New Artist"})
    write_audio(album / "2.flac", 1, {"title": "Two", "album": name, "artist": "New Artist"})
    return album


def later() -> Any:
    """A time after the settle period of anything written so far."""
    return utcnow() + SETTLE + timedelta(seconds=1)


def queued_inputs(session: Session) -> list[str]:
    return sorted(job.input for job in session.exec(select(ImportJob)).all())


def test_registering_does_not_import_existing_folders(
    session: Session, watch_dir: dict[str, Any], watcher: Watcher, library: Path
) -> None:
    assert watcher.rescan(session, later()) == []
    album = new_album(library)
    assert watcher.rescan(session, utcnow()) == []  # not settled yet
    [job] = watcher.rescan(session, later())
    assert job.input == str(album)
    assert watcher.rescan(session, later()) == []  # queued already


def test_changed_folders_are_queued_once_settled(
    session: Session, watch_dir: dict[str, Any], watcher: Watcher, library: Path
) -> None:
    album = new_album(library)
    watcher.changed([library], [album / "1.flac", album / "2.flac", library / "New Artist"])
    assert set(watcher.dirty) == {album, library / "New Artist"}
    assert watcher.due(session, utcnow()) == []
    assert set(watcher.dirty) == {album, library / "New Artist"}  # still waiting
    jobs = watcher.due(session, later())
    assert [job.input for job in jobs] == [str(album)]
    assert watcher.dirty == {}
    # A change to an old folder (before the baseline it was not imported) counts.
    old = library / "Old Artist" / "Old Album"
    write_audio(old / "2.flac", 1, {"title": "Old 2", "album": "Old"})
    watcher.changed([library], [old / "2.flac"])
    assert [job.input for job in watcher.due(session, later())] == [str(old)]


def test_imported_and_dismissed_folders_are_left_alone(
    client: TestClient,
    session: Session,
    watch_dir: dict[str, Any],
    watcher: Watcher,
    library: Path,
    job_queue: InMemoryConnector,
    settings: Settings,
) -> None:
    album = new_album(library)
    [job] = watcher.rescan(session, later())
    assert run_queued(session, job_queue, settings) == {job.id: JobState.done}
    session.delete(job)  # even without the job: its files are sources
    session.commit()
    assert watcher.rescan(session, later()) == []

    other = new_album(library, "Other Album")
    [job] = watcher.rescan(session, later())
    assert client.post(f"/imports/{job.id}/dismiss").status_code == 200
    assert watcher.rescan(session, later()) == []
    # Until the folder changes again.
    write_audio(other / "3.flac", 1, {"title": "Three", "album": "Other Album"})
    assert [j.input for j in watcher.rescan(session, later())] == [str(other)]
    assert album.is_dir()


def test_disc_folders_belong_to_their_album(library: Path) -> None:
    album = library / "Box"
    write_audio(album / "CD1" / "1.flac", 1)
    write_audio(album / "Disc 2" / "1.flac", 1)
    assert album_folders(library, library) == [album, library / "Old Artist" / "Old Album"]
    assert last_change(album) >= last_change(album / "CD1")


def test_explicit_scan_queues_existing_folders(
    client: TestClient, session: Session, watch_dir: dict[str, Any], library: Path
) -> None:
    old = library / "Old Artist" / "Old Album"
    second = new_album(library)
    response = client.post(
        f"/watch-dirs/{watch_dir['id']}/scan", json={"path": str(library / "Old Artist")}
    )
    assert response.status_code == 200
    jobs = response.json()
    assert [(j["input"], j["state"], j["kind"]) for j in jobs] == [(str(old), "queued", "folder")]
    jobs = client.post(f"/watch-dirs/{watch_dir['id']}/scan", json={}).json()
    assert [j["input"] for j in jobs] == [str(second)]
    assert client.post(f"/watch-dirs/{watch_dir['id']}/scan", json={}).json() == []
    assert queued_inputs(session) == sorted([str(old), str(second)])

    outside = client.post(f"/watch-dirs/{watch_dir['id']}/scan", json={"path": str(library.parent)})
    assert outside.status_code == 422
    assert outside.json()["detail"][0]["msg"] == "not inside the watch dir"


def test_watch_dir_crud(client: TestClient, library: Path, watch_dir: dict[str, Any]) -> None:
    assert watch_dir["path"] == str(library)
    assert watch_dir["enabled"] is True
    assert client.post("/watch-dirs", json={"path": str(library)}).status_code == 409
    assert client.post("/watch-dirs", json={"path": "/etc"}).status_code == 422
    assert [w["id"] for w in client.get("/watch-dirs").json()] == [watch_dir["id"]]
    updated = client.patch(f"/watch-dirs/{watch_dir['id']}", json={"enabled": False}).json()
    assert updated["enabled"] is False
    assert updated["baseline_at"] == watch_dir["baseline_at"]
    assert client.get(f"/watch-dirs/{watch_dir['id']}").json()["enabled"] is False
    assert client.delete(f"/watch-dirs/{watch_dir['id']}").status_code == 204
    assert client.get(f"/watch-dirs/{watch_dir['id']}").status_code == 404


def test_disabled_watch_dirs_are_not_scanned(
    client: TestClient, session: Session, watch_dir: dict[str, Any], watcher: Watcher, library: Path
) -> None:
    client.patch(f"/watch-dirs/{watch_dir['id']}", json={"enabled": False})
    new_album(library)
    assert watcher.rescan(session, later()) == []
    client.patch(f"/watch-dirs/{watch_dir['id']}", json={"enabled": True})
    assert len(watcher.rescan(session, later())) == 1


def test_folder_moved_in_with_old_times_counts_as_new(
    session: Session, watch_dir: dict[str, Any], watcher: Watcher, library: Path, tmp_path: Path
) -> None:
    """``mv``/``cp -p`` keep the files' mtimes; the ctime moves (as through the macOS
    bind mount), so the folder is still newer than the baseline."""
    staging = tmp_path / "Downloads" / "Moved Album"
    write_audio(staging / "1.flac", 1, {"title": "One", "album": "Moved"})
    for path in (staging / "1.flac", staging):
        os.utime(path, (946684800, 946684800))  # 2000-01-01
    moved = library / "Moved Album"
    staging.rename(moved)
    assert [job.input for job in watcher.rescan(session, later())] == [str(moved)]


def test_missing_watch_dir_is_skipped(
    session: Session, watch_dir: dict[str, Any], watcher: Watcher, library: Path
) -> None:
    shutil.rmtree(library)
    assert watcher.rescan(session, later()) == []
    assert watcher.due(session, later()) == []
