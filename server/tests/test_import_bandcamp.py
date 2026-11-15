"""Bandcamp import end to end: saved pages, MusicBrainz from the mock, fake downloads."""

import html
import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from sqlmodel import Session, SQLModel, func, select

from ep.importers.bandcamp import BandcampImporter
from ep.importers.folder import FolderImporter
from ep.importers.framework import Importer
from ep.importers.youtube import YouTubeImporter
from ep.metadata.ytdlp import VideoInfo
from ep.models import (
    Artist,
    ImportKind,
    JobState,
    Recording,
    ReleaseGroup,
    Source,
    SourceKind,
    Track,
)
from ep.settings import Settings
from tests.fakes import BANDCAMP, KNOTWILG, PROMISES, FakeYtDlp, web_transport
from tests.imports import get_job, review, run_queued
from tests.mock_musicbrainz import PROMISES_RELEASE

KNOTWILG_TRACKS = [
    "Whole Country",
    "I Know",
    "Trash",
    "They Wanna Live",
    "Stormbird And The Burning Star",
]


def count(session: Session, model: type[SQLModel]) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


@pytest.fixture
def ytdlp() -> FakeYtDlp:
    return FakeYtDlp()


@pytest.fixture
def importers(ytdlp: FakeYtDlp) -> dict[ImportKind, Importer]:
    transport = web_transport()
    return {
        ImportKind.folder: FolderImporter(),
        ImportKind.bandcamp: BandcampImporter(ytdlp, transport),
        ImportKind.youtube: YouTubeImporter(ytdlp, transport),
    }


@pytest.fixture
def run(
    session: Session,
    job_queue: InMemoryConnector,
    settings: Settings,
    importers: dict[ImportKind, Importer],
) -> Any:
    def run() -> dict[uuid.UUID, JobState | None]:
        return run_queued(session, job_queue, settings, importers=importers)

    return run


def post_url(client: TestClient, kind: str, url: str) -> dict[str, Any]:
    response = client.post("/imports", json={"kind": kind, "input": url})
    assert response.status_code == 201, response.text
    return response.json()


def test_label_page_album_not_in_musicbrainz(
    client: TestClient, session: Session, run: Any, settings: Settings, ytdlp: FakeYtDlp
) -> None:
    job = post_url(client, "bandcamp", KNOTWILG + "?from=discover_page")
    assert job["input"] == KNOTWILG
    assert run() == {uuid.UUID(job["id"]): JobState.done}

    proposal = get_job(client, job["id"])["proposal"]
    [release] = proposal["releases"]
    assert release["label"] == "Knotwilg"
    assert "experimental folk" in release["tags"]
    assert [a["name"] for a in proposal["artists"]] == ["Maxine Funke"]

    # The artist is Maxine Funke; Knotwilg (the page owner) is no artist.
    assert session.exec(select(Artist.name)).all() == ["Maxine Funke"]
    release_group = session.exec(select(ReleaseGroup)).one()
    assert (release_group.title, release_group.artist_credit.name) == ("Leitmotiv", "Maxine Funke")
    assert (release_group.mbid, release_group.date) == (None, "2026-11-01")
    assert release_group.cover_path is not None and Path(release_group.cover_path).is_file()
    tracks = sorted(release_group.tracks, key=lambda t: t.position)
    assert [t.recording.title for t in tracks] == KNOTWILG_TRACKS
    assert tracks[2].recording.length_ms == 270103

    # One download per track page, each a managed file under its bandcamp link.
    slugs = [title.lower().replace(" ", "-") for title in KNOTWILG_TRACKS]
    assert ytdlp.downloads == [f"https://knotwilg.bandcamp.com/track/{slug}" for slug in slugs]
    files = session.exec(select(Source).where(Source.kind == SourceKind.file)).all()
    assert len(files) == 5
    for file in files:
        assert file.managed and file.sha256 and file.parent is not None
        assert file.parent.kind is SourceKind.bandcamp
    assert {file.parent.uri for file in files if file.parent} == set(ytdlp.downloads)
    paths = sorted(Path(f.uri or "").relative_to(settings.library_dir).as_posix() for f in files)
    assert paths[0] == "Maxine Funke/Leitmotiv/01 Whole Country.mp3"
    # The work directory is gone.
    assert not (settings.library_dir / ".downloads" / job["id"]).exists()

    # Bandcamp's tags are tags of the new release group.
    tags_path = f"/entity-tags/release_group/{release_group.id}"
    tags = client.get(tags_path).json()
    assert {tag["name"] for tag in tags} == set(release["tags"])
    folk = next(tag for tag in tags if tag["name"] == "experimental folk")
    assert client.delete(f"{tags_path}/{folk['id']}").status_code == 204

    # Again: nothing new, nothing downloaded again, the removed tag stays removed.
    assert client.post(f"/imports/{job['id']}/retry").status_code == 200
    run()
    assert len(ytdlp.downloads) == 5
    assert count(session, Source) == 10
    assert len(client.get(tags_path).json()) == len(tags) - 1


def loose_video(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp, title: str, seconds: float
) -> Recording:
    """Import a YouTube video as a loose recording."""
    video = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    ytdlp.videos[video] = VideoInfo(
        id="aaaaaaaaaaa", title=title, webpage_url=video, channel="someone", duration=seconds
    )
    post_url(client, "youtube", video)
    run()
    recording = session.exec(select(Recording)).one()
    assert recording.tracks == []
    return recording


def file_origins(session: Session, recording: Recording) -> list[SourceKind]:
    files = session.exec(
        select(Source).where(Source.kind == SourceKind.file, Source.recording_id == recording.id)
    ).all()
    return sorted(f.parent.kind for f in files if f.parent)


def test_album_track_attaches_to_a_loose_recording(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp
) -> None:
    """A loose YouTube recording exists; the Bandcamp album containing it is imported:
    the recording gains the track and a second file, no duplicate."""
    loose = loose_video(client, session, run, ytdlp, "Maxine Funke - Trash (Official Audio)", 270)
    assert (loose.title, loose.artist_credit.name) == ("Trash", "Maxine Funke")

    job = post_url(client, "bandcamp", KNOTWILG)
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    assert count(session, Recording) == 5
    assert count(session, Artist) == 1
    [track] = session.exec(select(Track).where(Track.recording_id == loose.id)).all()
    assert track.position == 3
    assert file_origins(session, loose) == [SourceKind.bandcamp, SourceKind.youtube]


def test_album_found_by_its_url_with_a_loose_recording_to_review(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp
) -> None:
    """Promises is in MusicBrainz (linked from its Bandcamp page). The loose "Movement
    1" by Floating Points alone is not certainly MusicBrainz's "Promises: Movement 1" by
    all three: the inbox asks; picking it attaches it."""
    loose = loose_video(client, session, run, ytdlp, "Floating Points - Movement 1", 384)
    job = post_url(client, "bandcamp", PROMISES)
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    log = [entry["message"] for entry in get_job(client, job["id"])["log"]]
    assert any(m.startswith("found in MusicBrainz by its Bandcamp URL") for m in log), log
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "uncertain_recording"
    assert issue["subject"]["key"] == "https://floatingpoints.bandcamp.com/track/movement-1"
    assert [c["id"] for c in issue["candidates"]] == [str(loose.id)]

    review(
        client,
        job["id"],
        {"issue_id": issue["id"], "choice": "pick_candidate", "candidate_id": str(loose.id)},
    )
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    release_group = session.exec(select(ReleaseGroup)).one()
    assert release_group.release_mbid == uuid.UUID(PROMISES_RELEASE)
    assert count(session, Recording) == 9
    # Tags: MusicBrainz's genres and Bandcamp's tags.
    tags = {t["name"] for t in client.get(f"/entity-tags/release_group/{release_group.id}").json()}
    bandcamp_tags = get_job(client, job["id"])["proposal"]["releases"][0]["tags"]
    assert bandcamp_tags
    assert {"jazz", "third stream"} <= tags
    assert {t.casefold() for t in bandcamp_tags} <= {t.casefold() for t in tags}
    session.refresh(loose)
    assert loose.mbid is not None
    [track] = session.exec(select(Track).where(Track.recording_id == loose.id)).all()
    assert track.position == 1 and track.release_group_id == release_group.id
    assert file_origins(session, loose) == [SourceKind.bandcamp, SourceKind.youtube]


def test_failed_track_download_does_not_lose_the_rest(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp
) -> None:
    trash = "https://knotwilg.bandcamp.com/track/trash"
    ytdlp.failing_downloads.add(trash)
    job = post_url(client, "bandcamp", KNOTWILG)
    # The other four are imported; the job stays in the inbox for the failed one.
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    read = get_job(client, job["id"])
    [issue] = read["proposal"]["issues"]
    assert issue["kind"] == "download_failed"
    assert issue["subject"]["key"] == trash
    assert "not made this video available in your country" in issue["message"]
    assert issue["choices"] == ["retry", "skip"]
    assert issue["blocking"] is False
    assert (read["counts"]["failed"], read["created"]["recordings"]) == (1, 4)
    assert read["actions"] == ["review", "retry", "dismiss"]
    titles = session.exec(select(Recording.title)).all()
    assert sorted(titles) == sorted(t for t in KNOTWILG_TRACKS if t != "Trash")

    # Retried: the failed one is downloaded and added to the same release group.
    ytdlp.failing_downloads.clear()
    assert client.post(f"/imports/{job['id']}/retry").status_code == 200
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    assert len(ytdlp.downloads) == 6  # 5, then the retried one
    assert count(session, ReleaseGroup) == 1
    assert sorted(session.exec(select(Recording.title)).all()) == sorted(KNOTWILG_TRACKS)
    assert get_job(client, job["id"])["counts"]["failed"] == 0


def test_page_gone_is_an_issue(client: TestClient, run: Any) -> None:
    job = post_url(client, "bandcamp", "https://knotwilg.bandcamp.com/album/gone")
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "metadata_not_found"
    assert issue["choices"] == ["retry"]


@pytest.mark.network
def test_network_knotwilg_metadata(session: Session, settings: Settings) -> None:
    """The real page and MusicBrainz: metadata only (no download)."""
    from ep.importers.framework import JobContext, match_proposal
    from ep.jobs.queue import create_import
    from ep.metadata.musicbrainz import MusicBrainz

    job = create_import(session, ImportKind.bandcamp, KNOTWILG)
    with MusicBrainz() as musicbrainz:
        ctx = JobContext(session, job, musicbrainz, settings)
        proposal = BandcampImporter().build(ctx)
        match_proposal(ctx, proposal)
    [release] = proposal.releases
    assert release.credit[0].credited_name == "Maxine Funke"
    assert release.release_mbid is None  # not in MusicBrainz (2026-11)
    assert len(release.tracks) == 5
    assert [a.name for a in proposal.artists] == ["Maxine Funke"]


def test_bot_check_is_an_issue_to_retry(
    client: TestClient, session: Session, job_queue: InMemoryConnector, settings: Settings
) -> None:
    """Bandcamp's CDN answers with a challenge page: an issue with a retry, no rows."""
    challenge = b'<html><link href="/_fs-ch-1T1wmsGaOgGaSxcX/assets/styles.css" /></html>'
    blocked = httpx2.MockTransport(lambda request: httpx2.Response(200, content=challenge))
    importers: dict[ImportKind, Importer] = {
        ImportKind.bandcamp: BandcampImporter(FakeYtDlp(), blocked)
    }
    job = post_url(client, "bandcamp", KNOTWILG)
    assert run_queued(session, job_queue, settings, importers=importers) == {
        uuid.UUID(job["id"]): JobState.needs_review
    }
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "fetch_failed"
    assert issue["choices"] == ["retry"]
    assert "bot check" in issue["message"] and "does not try to get around" in issue["message"]
    assert count(session, Recording) == 0

    # Once Bandcamp answers again, a retry imports the album.
    importers[ImportKind.bandcamp] = BandcampImporter(FakeYtDlp(), web_transport())
    review(client, job["id"], {"issue_id": issue["id"], "choice": "retry"})
    assert run_queued(session, job_queue, settings, importers=importers) == {
        uuid.UUID(job["id"]): JobState.done
    }
    assert count(session, Recording) == 5


def test_preorder_tracks_without_stream_have_no_download(
    client: TestClient,
    session: Session,
    job_queue: InMemoryConnector,
    settings: Settings,
    ytdlp: FakeYtDlp,
) -> None:
    """A pre-order: tracks not released yet are imported without audio, the others
    downloaded."""
    text = (BANDCAMP / "knotwilg_leitmotiv-2.html").read_text()
    m = re.search(r'data-tralbum="(.+?)"', text, re.DOTALL)
    assert m is not None
    data = json.loads(html.unescape(m.group(1)))
    data["album_is_preorder"] = True
    for info in data["trackinfo"][2:]:
        info.update(file=None, unreleased_track=True, duration=0.0)
    page = text[: m.start(1)] + html.escape(json.dumps(data)) + text[m.end(1) :]
    transport = web_transport()

    def handle(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == KNOTWILG:
            return httpx2.Response(200, content=page.encode())
        return transport.handle_request(request)

    importers: dict[ImportKind, Importer] = {
        ImportKind.bandcamp: BandcampImporter(ytdlp, httpx2.MockTransport(handle))
    }
    job = post_url(client, "bandcamp", KNOTWILG)
    assert run_queued(session, job_queue, settings, importers=importers) == {
        uuid.UUID(job["id"]): JobState.done
    }
    assert len(ytdlp.downloads) == 2
    assert count(session, Recording) == 5
    files = session.exec(select(Source).where(Source.kind == SourceKind.file)).all()
    assert len(files) == 2
