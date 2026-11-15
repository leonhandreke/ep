"""The shared import framework: state machine, review round trip, issues, apply.

A fake importer stands in for the real ones: it returns a fixed proposal, so these tests
exercise matching, review, downloads and applying independently of any input format.
"""

import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from sqlmodel import Session, SQLModel, col, func, select

from ep.importers.framework import (
    Applied,
    DownloadError,
    Importer,
    ImportFailed,
    JobContext,
    job_work_dir,
)
from ep.importers.proposal import (
    EmbeddedCover,
    ExistingTarget,
    IncomingArtist,
    IncomingCreditPart,
    IncomingMedium,
    IncomingRelease,
    IncomingTrack,
    Origin,
    Proposal,
    Target,
)
from ep.jobs.queue import create_import
from ep.jobs.tasks import recover_interrupted_jobs
from ep.models import (
    Artist,
    ImportJob,
    ImportKind,
    JobState,
    Recording,
    ReleaseGroup,
    Source,
    SourceKind,
)
from ep.settings import Settings
from tests.helpers import make_artist
from tests.imports import get_job, review, run_queued


def count(session: Session, model: type[SQLModel]) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


class FakeImporter(Importer):
    """Proposes "Pastel Blues" by Nina Simone: two tracks, one file each."""

    kind = ImportKind.youtube

    def __init__(self, audio_dir: Path) -> None:
        self.audio_dir = audio_dir
        self.fail: Exception | None = None
        self.download_failures = 0
        self.pending_download = False
        self.recording_target: Target | None = None
        self.builds = 0
        self.downloads = 0
        self.found_partial = False
        self.fail_apply = False
        self.applied: Applied | None = None

    def build(self, ctx: JobContext) -> Proposal:
        self.builds += 1
        if self.fail is not None:
            raise self.fail
        credit = [IncomingCreditPart(artist_key="nina", credited_name="Nina Simone")]
        media = [
            IncomingMedium(key="one", path=str(self.audio_dir / "album.flac"), sha256="0" * 64),
            IncomingMedium(key="two", path=str(self.audio_dir / "song.mp3")),
        ]
        if self.pending_download:
            media[1] = IncomingMedium(
                key="two",
                path=None,
                download=True,
                managed=True,
                origin=Origin(kind=SourceKind.youtube, uri="https://youtu.be/feeling-good"),
            )
        tracks = [
            IncomingTrack(key="t1", medium_key="one", position=1, title="Sinnerman", credit=credit),
            IncomingTrack(
                key="t2",
                medium_key="two",
                position=2,
                title="Feeling Good",
                credit=credit,
                isrcs=["US-PR3-65-00001"],
            ),
        ]
        if self.recording_target is not None:
            tracks[0].target = self.recording_target
        return Proposal(
            media=media,
            artists=[IncomingArtist(key="nina", name="Nina Simone")],
            releases=[
                IncomingRelease(
                    key="rg",
                    title="Pastel Blues",
                    credit=credit,
                    tracks=tracks,
                    cover=EmbeddedCover(medium_key="one"),
                )
            ],
        )

    def apply(self, ctx: JobContext, proposal: Proposal, applied: Applied) -> None:
        self.applied = applied
        if self.fail_apply:
            raise RuntimeError("radio tables are full")

    def download(self, ctx: JobContext, medium: IncomingMedium, directory: Path) -> Path:
        self.downloads += 1
        self.found_partial = (directory / "song.mp3.part").exists()
        (directory / "song.mp3.part").write_bytes(b"partial")
        if self.download_failures:
            self.download_failures -= 1
            raise DownloadError("HTTP 403")
        file = directory / "song.mp3"
        shutil.copyfile(self.audio_dir / "song.mp3", file)
        (directory / "song.mp3.part").unlink()
        return file


@pytest.fixture
def fake(audio_dir: Path) -> FakeImporter:
    return FakeImporter(audio_dir)


@pytest.fixture
def run(
    session: Session, job_queue: InMemoryConnector, settings: Settings, fake: FakeImporter
) -> Any:
    def run() -> dict[uuid.UUID, JobState | None]:
        return run_queued(session, job_queue, settings, importers={ImportKind.youtube: fake})

    return run


@pytest.fixture
def job(session: Session) -> ImportJob:
    job = create_import(session, ImportKind.youtube, "https://example.com/pastel-blues")
    session.commit()
    return job


def test_confident_job_applies_directly(
    client: TestClient, session: Session, job: ImportJob, run: Any, job_queue: InMemoryConnector
) -> None:
    assert [j["args"] for j in job_queue.jobs.values()] == [{"job_id": str(job.id)}]
    assert run() == {job.id: JobState.done}

    read = get_job(client, str(job.id))
    assert read["state"] == "done"
    assert read["actions"] == ["retry"]
    assert read["open_issues"] == 0
    assert read["progress"]["stage"] is None
    messages = [entry["message"] for entry in read["log"]]
    assert messages[0] == "started"
    assert messages[-1] == (
        "done, created: artist 1, release_group 1, recording 2, track 2, source 2, external_id 1"
    )
    proposal = read["proposal"]
    assert proposal["artists"][0]["target"]["kind"] == "new"
    assert proposal["releases"][0]["target"]["kind"] == "new"

    release_group = session.exec(select(ReleaseGroup)).one()
    tracks = client.get(f"/release-groups/{release_group.id}/tracks").json()
    assert [(t["position"], t["recording"]["title"]) for t in tracks] == [
        (1, "Sinnerman"),
        (2, "Feeling Good"),
    ]
    assert all(t["playability"]["state"] == "file" for t in tracks)
    feeling_good = tracks[1]["recording"]["id"]
    ids = client.get(f"/recordings/{feeling_good}/external-ids").json()
    assert [(i["scheme"], i["value"]) for i in ids] == [("isrc", "USPR36500001")]
    source = session.exec(select(Source).where(col(Source.uri).endswith("album.flac"))).one()
    assert (source.managed, source.sha256, source.meta) == (
        False,
        "0" * 64,
        {"import_job_id": str(job.id)},
    )


def test_uncertain_match_review_round_trip(
    client: TestClient, session: Session, job: ImportJob, run: Any, job_queue: InMemoryConnector
) -> None:
    make_artist(client, disambiguation="singer")
    other = make_artist(client, disambiguation="someone else")
    assert run() == {job.id: JobState.needs_review}

    read = get_job(client, str(job.id))
    assert (read["state"], read["open_issues"]) == ("needs_review", 1)
    assert read["actions"] == ["review", "retry", "dismiss"]
    [issue] = read["proposal"]["issues"]
    assert issue["id"] == "uncertain_artist:nina"
    assert issue["kind"] == "uncertain_artist"
    assert issue["subject"] == {"type": "artist", "key": "nina", "label": "Nina Simone"}
    assert issue["choices"] == ["pick_candidate", "create_new"]
    assert issue["answer"] is None
    assert {c["subtitle"] for c in issue["candidates"]} == {"singer", "someone else"}
    assert all(c["type"] == "artist" and c["score"] == 100 for c in issue["candidates"])
    assert count(session, ReleaseGroup) == 0  # nothing applied

    answer = {"issue_id": issue["id"], "choice": "pick_candidate", "candidate_id": other["id"]}
    queued = review(client, str(job.id), answer)
    assert queued["state"] == "queued"
    assert len(job_queue.jobs) == 1
    assert run() == {job.id: JobState.done}

    read = get_job(client, str(job.id))
    [issue] = read["proposal"]["issues"]
    assert issue["answer"] == {"choice": "pick_candidate", "candidate_id": other["id"]}
    assert read["proposal"]["artists"][0]["target"]["id"] == other["id"]
    release_group = session.exec(select(ReleaseGroup)).one()
    assert [n.artist_id for n in release_group.artist_credit.names] == [uuid.UUID(other["id"])]
    assert count(session, Artist) == 2


def test_review_answers_are_validated(
    client: TestClient, session: Session, job: ImportJob, run: Any
) -> None:
    make_artist(client, disambiguation="a")
    make_artist(client, disambiguation="b")
    run()
    path = f"/imports/{job.id}/review"
    cases = [
        ({"issue_id": "nope", "choice": "create_new"}, ["body", "answers", 0, "issue_id"]),
        ({"issue_id": "uncertain_artist:nina", "choice": "skip"}, ["body", "answers", 0]),
        (
            {
                "issue_id": "uncertain_artist:nina",
                "choice": "pick_candidate",
                "candidate_id": str(uuid.uuid4()),
            },
            ["body", "answers", 0],
        ),
        ({"issue_id": "uncertain_artist:nina", "choice": "pick_candidate"}, ["body", "answers", 0]),
    ]
    for answer, loc in cases:
        response = client.post(path, json={"answers": [answer]})
        assert response.status_code == 422, answer
        assert response.json()["detail"][0]["loc"] == loc
    assert client.post(path, json={"answers": []}).status_code == 422
    assert client.post(f"/imports/{uuid.uuid4()}/review", json={"answers": []}).status_code == 422
    missing = {"answers": [{"issue_id": "x", "choice": "skip"}]}
    assert client.post(f"/imports/{uuid.uuid4()}/review", json=missing).status_code == 404

    review(client, str(job.id), {"issue_id": "uncertain_artist:nina", "choice": "create_new"})
    response = client.post(path, json={"answers": [{"issue_id": "x", "choice": "skip"}]})
    assert response.status_code == 409  # queued, not reviewable
    assert response.json() == {"detail": "a queued job cannot be reviewed"}


def test_download_failed_then_retried(
    client: TestClient,
    session: Session,
    job: ImportJob,
    run: Any,
    fake: FakeImporter,
    library_dir: Path,
) -> None:
    fake.pending_download, fake.download_failures = True, 1
    assert run() == {job.id: JobState.needs_review}
    [issue] = get_job(client, str(job.id))["proposal"]["issues"]
    assert issue["kind"] == "download_failed"
    assert issue["subject"]["type"] == "medium"
    assert issue["message"] == "download failed: HTTP 403"
    assert issue["choices"] == ["retry", "skip"]
    work = job_work_dir(library_dir, job.id)
    assert [p.name for p in work.rglob("*") if p.is_file()] == ["song.mp3.part"]

    review(client, str(job.id), {"issue_id": issue["id"], "choice": "retry"})
    assert run() == {job.id: JobState.done}
    assert fake.found_partial  # the second attempt got the first one's directory
    read = get_job(client, str(job.id))
    assert read["proposal"]["issues"] == []
    # Moved to its place in the managed library; the work directory is gone.
    placed = library_dir / "Nina Simone" / "Pastel Blues" / "02 Feeling Good.mp3"
    assert placed.is_file()
    assert not work.exists()
    [medium] = [m for m in read["proposal"]["media"] if m["key"] == "two"]
    assert (medium["path"], medium["download"], medium["managed"]) == (str(placed), False, True)
    file = session.exec(select(Source).where(Source.uri == str(placed))).one()
    assert (file.managed, file.codec, file.size) == (True, "mp3", placed.stat().st_size)
    assert file.sha256 is not None and len(file.sha256) == 64
    assert file.parent is not None
    assert (file.parent.kind, file.parent.uri) == ("youtube", "https://youtu.be/feeling-good")


def test_download_is_reused_after_a_failed_apply(
    client: TestClient,
    session: Session,
    job: ImportJob,
    run: Any,
    fake: FakeImporter,
    library_dir: Path,
) -> None:
    fake.pending_download, fake.fail_apply = True, True
    assert run() == {job.id: JobState.failed}
    assert get_job(client, str(job.id))["error"] == "RuntimeError: radio tables are full"
    work = job_work_dir(library_dir, job.id)
    assert [p.name for p in work.rglob("*") if p.is_file()] == ["song.mp3"]
    # The file placed in the library while applying was removed again, and so was the cover.
    assert [p for p in library_dir.rglob("*") if p.is_file() and ".downloads" not in p.parts] == []
    assert count(session, Source) == 0

    fake.fail_apply = False
    client.post(f"/imports/{job.id}/retry")
    assert run() == {job.id: JobState.done}
    assert fake.downloads == 1
    assert (library_dir / "Nina Simone" / "Pastel Blues" / "02 Feeling Good.mp3").is_file()
    assert not work.exists()
    applied = fake.applied
    assert applied is not None
    assert (set(applied.sources), set(applied.files)) == ({"t1", "t2"}, {"one", "two"})
    assert applied.sources["t2"] is applied.files["two"]

    # Run again: the origin is a source already, so nothing is downloaded or created.
    sources = count(session, Source)
    client.post(f"/imports/{job.id}/retry")
    assert run() == {job.id: JobState.done}
    assert (fake.downloads, count(session, Source)) == (1, sources)
    assert session.exec(select(ReleaseGroup)).one().cover_path is not None


def test_dismiss_deletes_downloads(
    client: TestClient, job: ImportJob, run: Any, fake: FakeImporter, library_dir: Path
) -> None:
    fake.pending_download, fake.download_failures = True, 1
    assert run() == {job.id: JobState.needs_review}
    work = job_work_dir(library_dir, job.id)
    assert work.exists()
    assert client.post(f"/imports/{job.id}/dismiss").status_code == 200
    assert not work.exists()


def test_failed_job_stays_in_inbox_until_dismissed(
    client: TestClient, job: ImportJob, run: Any, fake: FakeImporter
) -> None:
    fake.fail = ImportFailed("the page is gone")
    assert run() == {job.id: JobState.failed}
    read = get_job(client, str(job.id))
    assert (read["error"], read["actions"]) == ("the page is gone", ["retry", "dismiss"])
    assert read["log"][-1]["level"] == "error"
    inbox = client.get("/imports", params={"state": ["needs_review", "failed"]}).json()
    assert [j["id"] for j in inbox] == [str(job.id)]

    fake.fail = None
    assert client.post(f"/imports/{job.id}/retry").json()["state"] == "queued"
    assert run() == {job.id: JobState.done}
    assert get_job(client, str(job.id))["error"] is None

    other = client.post("/imports", json={"kind": "folder", "input": "/nope"})
    assert other.status_code == 422
    fake.fail = ImportFailed("again")
    client.post(f"/imports/{job.id}/retry")
    run()
    assert client.post(f"/imports/{job.id}/dismiss").json()["state"] == "dismissed"
    assert client.post(f"/imports/{job.id}/dismiss").status_code == 409
    assert client.post(f"/imports/{job.id}/retry").status_code == 409
    assert client.get("/imports", params={"state": "failed"}).json() == []


def test_error_while_applying_rolls_back(
    client: TestClient, session: Session, job: ImportJob, run: Any, fake: FakeImporter
) -> None:
    # A recording that does not exist: applying fails after creating the artist.
    fake.recording_target = ExistingTarget(id=uuid.uuid4(), label="gone")
    assert run() == {job.id: JobState.failed}
    read = get_job(client, str(job.id))
    assert read["error"].startswith("NoResultFound")
    assert any("Traceback" in entry["message"] for entry in read["log"])
    assert read["proposal"] is not None  # what it tried to apply
    assert count(session, Artist) == count(session, ReleaseGroup) == count(session, Source) == 0


def test_retrying_a_done_job_creates_nothing_new(
    client: TestClient, session: Session, job: ImportJob, run: Any
) -> None:
    run()
    counts = [count(session, m) for m in (Artist, ReleaseGroup, Recording, Source)]
    client.post(f"/imports/{job.id}/retry")
    assert run() == {job.id: JobState.done}
    assert [count(session, m) for m in (Artist, ReleaseGroup, Recording, Source)] == counts
    # The second run matched everything it had created the first time.
    proposal = get_job(client, str(job.id))["proposal"]
    assert proposal["releases"][0]["target"]["kind"] == "existing"


def test_only_queued_jobs_run(
    client: TestClient, job: ImportJob, run: Any, fake: FakeImporter
) -> None:
    assert client.post(f"/imports/{job.id}/dismiss").status_code == 409  # cancel it instead
    assert client.post(f"/imports/{job.id}/cancel").json()["state"] == "dismissed"
    assert run() == {job.id: None}
    assert fake.builds == 0


def test_list_import_jobs(client: TestClient, session: Session, job: ImportJob, run: Any) -> None:
    second = create_import(session, ImportKind.youtube, "https://example.com/second")
    session.commit()
    listed = client.get("/imports").json()
    assert [j["id"] for j in listed] == [str(second.id), str(job.id)]
    assert listed[0]["progress"] == {
        "stage": None,
        "step": None,
        "done": 0,
        "total": None,
        "fraction": None,
    }
    # Waiting behind the first one; actions: cancel.
    assert (listed[0]["waiting_for"], listed[1]["waiting_for"]) == (str(job.id), None)
    assert listed[0]["actions"] == ["cancel"]
    assert client.get("/imports", params={"kind": "folder"}).json() == []
    assert client.get("/imports", params={"state": "done"}).json() == []
    run()
    assert len(client.get("/imports", params={"state": "done"}).json()) == 2


def test_stale_answer_is_ignored_and_asked_again(
    client: TestClient, session: Session, job: ImportJob, run: Any
) -> None:
    """An answer picking a candidate that a later run no longer offers (the library or
    MusicBrainz changed) does not apply: the issue is open again."""
    make_artist(client, disambiguation="a")
    gone = make_artist(client, disambiguation="b")
    run()
    answer = {"issue_id": "uncertain_artist:nina", "choice": "pick_candidate"}
    review(client, str(job.id), {**answer, "candidate_id": gone["id"]})
    assert client.delete(f"/artists/{gone['id']}").status_code == 204
    make_artist(client, disambiguation="c")
    assert run() == {job.id: JobState.needs_review}
    read = get_job(client, str(job.id))
    [issue] = read["proposal"]["issues"]
    assert issue["answer"] is None
    assert gone["id"] not in {c["id"] for c in issue["candidates"]}
    assert any("no longer fits" in entry["message"] for entry in read["log"])
    assert count(session, ReleaseGroup) == 0


def test_worker_start_requeues_interrupted_and_lost_jobs(
    session: Session, job: ImportJob, job_queue: InMemoryConnector
) -> None:
    lost = create_import(session, ImportKind.youtube, "https://example.com/lost")
    job.state = JobState.applying
    session.commit()
    job_queue.jobs.clear()  # both queue entries are gone (a crash, a failed task)
    assert set(recover_interrupted_jobs(session)) == {job.id, lost.id}
    assert job.state is JobState.queued
    assert sorted(j["args"]["job_id"] for j in job_queue.jobs.values()) == sorted(
        [str(job.id), str(lost.id)]
    )
    assert recover_interrupted_jobs(session) == []  # queued once only
