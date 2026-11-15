"""The shared import framework: state machine, review round trip, issues, apply.

A fake importer stands in for the real ones: it returns a fixed proposal, so these tests
exercise matching, review, downloads and applying independently of any input format.
"""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from sqlmodel import Session, SQLModel, col, func, select

from ep.importers.framework import DownloadError, Importer, ImportFailed, JobContext
from ep.importers.proposal import (
    IncomingArtist,
    IncomingCreditPart,
    IncomingMedium,
    IncomingRelease,
    IncomingTrack,
    Origin,
    Proposal,
    Target,
    TargetKind,
)
from ep.jobs.queue import create_import
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
                IncomingRelease(key="rg", title="Pastel Blues", credit=credit, tracks=tracks)
            ],
        )

    def download(self, ctx: JobContext, medium: IncomingMedium) -> None:
        if self.download_failures:
            self.download_failures -= 1
            raise DownloadError("HTTP 403")
        medium.path, medium.download = str(self.audio_dir / "song.mp3"), False


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
    assert all(t["playback"] for t in tracks)
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


def test_create_new_answer(client: TestClient, session: Session, job: ImportJob, run: Any) -> None:
    make_artist(client, disambiguation="a")
    make_artist(client, disambiguation="b")
    run()
    review(client, str(job.id), {"issue_id": "uncertain_artist:nina", "choice": "create_new"})
    assert run() == {job.id: JobState.done}
    assert count(session, Artist) == 3


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
    client: TestClient, session: Session, job: ImportJob, run: Any, fake: FakeImporter
) -> None:
    fake.pending_download, fake.download_failures = True, 1
    assert run() == {job.id: JobState.needs_review}
    [issue] = get_job(client, str(job.id))["proposal"]["issues"]
    assert issue["kind"] == "download_failed"
    assert issue["subject"]["type"] == "medium"
    assert issue["message"] == "download failed: HTTP 403"
    assert issue["choices"] == ["retry", "skip"]

    review(client, str(job.id), {"issue_id": issue["id"], "choice": "retry"})
    assert run() == {job.id: JobState.done}
    read = get_job(client, str(job.id))
    assert read["proposal"]["issues"] == []
    file = session.exec(select(Source).where(col(Source.uri).endswith("song.mp3"))).one()
    assert file.managed
    assert file.parent is not None
    assert (file.parent.kind, file.parent.uri) == ("youtube", "https://youtu.be/feeling-good")


def test_download_skipped(
    client: TestClient, session: Session, job: ImportJob, run: Any, fake: FakeImporter
) -> None:
    fake.pending_download, fake.download_failures = True, 5
    run()
    review(client, str(job.id), {"issue_id": "download_failed:two", "choice": "skip"})
    assert run() == {job.id: JobState.done}
    [issue] = get_job(client, str(job.id))["proposal"]["issues"]
    assert issue["answer"] == {"choice": "skip", "candidate_id": None}
    assert count(session, Recording) == 1  # "Feeling Good" was skipped
    assert count(session, Source) == 1


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
    fake.recording_target = Target(kind=TargetKind.existing, id=uuid.uuid4())
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
    assert client.post(f"/imports/{job.id}/dismiss").json()["state"] == "dismissed"
    assert run() == {job.id: None}
    assert fake.builds == 0


def test_list_import_jobs(client: TestClient, session: Session, job: ImportJob, run: Any) -> None:
    second = create_import(session, ImportKind.youtube, "https://example.com/second")
    session.commit()
    listed = client.get("/imports").json()
    assert [j["id"] for j in listed] == [str(second.id), str(job.id)]
    assert listed[0]["progress"] == {"stage": None, "done": 0, "total": None}
    assert client.get("/imports", params={"kind": "folder"}).json() == []
    assert client.get("/imports", params={"state": "done"}).json() == []
    run()
    assert len(client.get("/imports", params={"state": "done"}).json()) == 2
