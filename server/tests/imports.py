"""Helpers for import tests: generated album folders and running queued jobs."""

import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from sqlmodel import Session

from ep.importers.framework import Importer, run_job
from ep.jobs.queue import RUN_IMPORT
from ep.jobs.tasks import IMPORTERS
from ep.models import ImportKind, JobState
from ep.settings import Settings
from tests.mock_musicbrainz import MockMusicBrainz


def write_audio(
    path: Path,
    seconds: float,
    tags: Mapping[str, str | int] | None = None,
    cover: Path | None = None,
) -> Path:
    """A silent FLAC (or another format, by extension) of ``seconds`` with these tags."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args: list[str | Path] = ["-f", "lavfi", "-i", f"anullsrc=r=8000:cl=mono:d={seconds}"]
    if cover is not None:
        args += ["-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "copy"]
        args += ["-disposition:v", "attached_pic"]
    for key, value in (tags or {}).items():
        args += ["-metadata", f"{key}={value}"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args), str(path)], check=True)
    return path


def run_queued(
    session: Session,
    job_queue: InMemoryConnector,
    settings: Settings,
    musicbrainz: MockMusicBrainz | None = None,
    importers: dict[ImportKind, Importer] = IMPORTERS,
) -> dict[uuid.UUID, JobState | None]:
    """Run every queued "run import" job, as the worker would, until none is left."""
    musicbrainz = musicbrainz or MockMusicBrainz()
    states: dict[uuid.UUID, JobState | None] = {}
    while todo := [job for job in job_queue.jobs.values() if job["status"] == "todo"]:
        for row in todo:
            assert row["task_name"] == RUN_IMPORT
            del job_queue.jobs[row["id"]]
            job_id = uuid.UUID(row["args"]["job_id"])
            states[job_id] = run_job(session, job_id, importers, musicbrainz, settings)
    return states


def post_import(client: TestClient, path: Path) -> dict[str, Any]:
    response = client.post("/imports", json={"kind": "folder", "input": str(path)})
    assert response.status_code == 201, response.text
    return response.json()


def get_job(client: TestClient, job_id: str) -> dict[str, Any]:
    response = client.get(f"/imports/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def review(client: TestClient, job_id: str, *answers: dict[str, Any]) -> dict[str, Any]:
    response = client.post(f"/imports/{job_id}/review", json={"answers": list(answers)})
    assert response.status_code == 200, response.text
    return response.json()
