"""YouTube import end to end: saved yt-dlp output, MusicBrainz from the mock, fake downloads."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from sqlmodel import Session, SQLModel, func, select

from ep.importers.framework import Importer, JobContext, match_proposal
from ep.importers.youtube import (
    YouTubeImporter,
    clean_chapter_title,
    clean_title,
    split_artist_title,
)
from ep.jobs.queue import create_import
from ep.metadata.musicbrainz import MusicBrainz
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
from tests.fakes import (
    CHAPTERED_VIDEO,
    PLAYLIST,
    PLAYLIST_CANONICAL,
    SINGLE_VIDEO,
    UNRELEASED,
    FakeYtDlp,
    web_transport,
)
from tests.imports import get_job, review, run_queued
from tests.mock_musicbrainz import NEIGHBORHOODS_RELEASE

MOVEMENT_1_SEARCH_HIT = "4ecdd69e-a8bb-4b01-b510-809a1c3abc71"  # 6:00, in the search fixture
NEIGHBORHOODS = [
    "Saturday Morning Doze",
    "At the Store",
    "August Haze",
    "The Secret Place",
    "After School",
    "Gloaming",
    "From the Bluff",
    "Night Games",
]


def count(session: Session, model: type[SQLModel]) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


@pytest.fixture
def ytdlp() -> FakeYtDlp:
    return FakeYtDlp()


@pytest.fixture
def run(
    session: Session, job_queue: InMemoryConnector, settings: Settings, ytdlp: FakeYtDlp
) -> Any:
    importers: dict[ImportKind, Importer] = {
        ImportKind.youtube: YouTubeImporter(ytdlp, web_transport())
    }

    def run() -> dict[uuid.UUID, JobState | None]:
        return run_queued(session, job_queue, settings, importers=importers)

    return run


def post_video(client: TestClient, url: str) -> dict[str, Any]:
    response = client.post("/imports", json={"kind": "youtube", "input": url})
    assert response.status_code == 201, response.text
    return response.json()


def issues(client: TestClient, job: dict[str, Any]) -> list[dict[str, Any]]:
    return get_job(client, job["id"])["proposal"]["issues"]


# Titles


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("aedis - salt echoes [d2e006]", ("aedis", "salt echoes")),
        ("Ernest Hood - Neighborhoods (Full Album)", ("Ernest Hood", "Neighborhoods")),
        ("Tales Under The Oak - The Toad Folk II (full album) (fantasy synth / folk)",
         ("Tales Under The Oak", "The Toad Folk II")),
        ("Nina Simone - Sinnerman (Official Audio)", ("Nina Simone", "Sinnerman")),
        ("Daft Punk - One More Time [Official Music Video] HD", ("Daft Punk", "One More Time")),
        ("Burial \u2013 Archangel (Lyrics)", ("Burial", "Archangel")),
        ("Artist - Song | Official Visualizer", ("Artist", "Song")),
        ("Premiere: Artist - Track (Extended Mix) [4K]", ("Artist", "Track (Extended Mix)")),
        ('Björk "Hyperballad"', ("Björk", "Hyperballad")),
        ("Artist - Song (Live at Glastonbury)", ("Artist", "Song (Live at Glastonbury)")),
        ("Body Music", (None, "Body Music")),
        # One noise word after the only dash is the title.
        ("Madonna - Music", ("Madonna", "Music")),
        ("India.Arie - Video", ("India.Arie", "Video")),
        ("Artist - Song - Video", ("Artist", "Song")),
        ("Nightcall - Official Video", (None, "Nightcall")),
        ("Artist - Song - HD", ("Artist", "Song")),
        ("Saturday Morning Doze", (None, "Saturday Morning Doze")),
    ],
)  # fmt: skip
def test_video_titles(title: str, expected: tuple[str | None, str]) -> None:
    assert split_artist_title(clean_title(title)) == expected


def test_chapter_titles() -> None:
    assert clean_chapter_title("Track 01: Traces of Absence") == "Traces of Absence"
    assert clean_chapter_title("03 - Night Games") == "Night Games"
    assert clean_chapter_title("1. Intro (Official Audio)") == "Intro"
    assert clean_chapter_title("The Festival (Tale)") == "The Festival (Tale)"
    assert clean_chapter_title("2046") == "2046"


# A single video


def test_single_video_is_a_loose_recording(
    client: TestClient, session: Session, run: Any, settings: Settings, ytdlp: FakeYtDlp
) -> None:
    job = post_video(client, "https://youtu.be/GSWtPDAN3PE?si=tracking")
    assert job["input"] == SINGLE_VIDEO
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    log = [entry["message"] for entry in get_job(client, job["id"])["log"]]
    assert "YouTube: 'salt echoes' by aedis (from 'aedis - salt echoes [d2e006]')" in log
    assert any("not found in MusicBrainz; importing it as entered" in m for m in log)
    assert any(m.startswith(f"downloading {SINGLE_VIDEO}: 50%") for m in log), log

    recording = session.exec(select(Recording)).one()
    assert (recording.title, recording.artist_credit.name) == ("salt echoes", "aedis")
    assert (recording.mbid, recording.length_ms, recording.tracks) == (None, 409000, [])
    assert count(session, ReleaseGroup) == 0
    file = session.exec(select(Source).where(Source.kind == SourceKind.file)).one()
    assert file.recording_id == recording.id and file.managed and file.codec == "opus"
    assert Path(file.uri or "") == settings.library_dir / "aedis" / "salt echoes.opus"
    assert file.parent is not None
    assert (file.parent.kind, file.parent.uri) == (SourceKind.youtube, SINGLE_VIDEO)

    # Playable: the stream endpoint serves the file.
    response = client.get(f"/sources/{file.id}/stream")
    assert response.status_code == 200 and response.content == Path(file.uri or "").read_bytes()

    # The same video again, by another URL: nothing new.
    post_video(client, "https://www.youtube.com/watch?v=GSWtPDAN3PE&feature=share")
    run()
    assert count(session, Source) == 2 and count(session, Recording) == 1
    assert ytdlp.downloads == [SINGLE_VIDEO]


def movement_video(ytdlp: FakeYtDlp, seconds: float | None) -> str:
    url = "https://www.youtube.com/watch?v=bbbbbbbbbbb"
    ytdlp.videos[url] = VideoInfo(
        id="bbbbbbbbbbb",
        title="Floating Points, Pharoah Sanders - Movement 1 [Official Audio]",
        webpage_url=url,
        channel="Luaka Bop",
        duration=seconds,
    )
    return url


def test_single_video_identified_in_musicbrainz(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp
) -> None:
    """One search hit with the same title, artist (featured artists aside) and length:
    its recording, with MusicBrainz's credit."""
    post_video(client, movement_video(ytdlp, 360))
    run()
    recording = session.exec(select(Recording)).one()
    assert recording.mbid == uuid.UUID(MOVEMENT_1_SEARCH_HIT)
    assert recording.artist_credit.name == (
        "Floating Points, Pharoah Sanders feat. London Symphony Orchestra"
    )
    artists = session.exec(select(Artist)).all()
    assert len(artists) == 3 and all(artist.mbid for artist in artists)


def test_single_video_uncertain_in_musicbrainz(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp
) -> None:
    """Without a length the hit is not certain: the inbox asks."""
    job = post_video(client, movement_video(ytdlp, None))
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = issues(client, job)
    assert issue["kind"] == "uncertain_metadata"
    assert issue["choices"] == ["pick_candidate", "keep_as_entered", "skip"]
    [candidate] = issue["candidates"]
    assert (candidate["type"], candidate["id"]) == ("musicbrainz_recording", MOVEMENT_1_SEARCH_HIT)

    review(client, job["id"], {"issue_id": issue["id"], "choice": "keep_as_entered"})
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    recording = session.exec(select(Recording)).one()
    assert (recording.title, recording.mbid) == ("Movement 1", None)


def test_unavailable_video_is_an_issue(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp
) -> None:
    ytdlp.unavailable.add(SINGLE_VIDEO)
    job = post_video(client, SINGLE_VIDEO)
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = issues(client, job)
    assert issue["kind"] == "metadata_not_found"
    assert "This video has been removed" in issue["message"]
    assert issue["choices"] == ["retry", "skip"]

    # Retry once it is back.
    ytdlp.unavailable.clear()
    review(client, job["id"], {"issue_id": issue["id"], "choice": "retry"})
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    assert count(session, Recording) == 1


# A video with chapters


def test_chaptered_video_is_a_single_file_album(
    client: TestClient, session: Session, run: Any, settings: Settings
) -> None:
    job = post_video(client, CHAPTERED_VIDEO)
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    release_group = session.exec(select(ReleaseGroup)).one()
    assert (release_group.title, release_group.artist_credit.name) == (
        "The Toad Folk II",
        "Tales Under The Oak",
    )
    assert release_group.cover_path is not None
    tracks = sorted(release_group.tracks, key=lambda t: t.position)
    assert len(tracks) == 9
    assert tracks[0].recording.title == "Wild Swamps"
    assert tracks[8].recording.title == "The Festival (Tale)"
    assert tracks[1].recording.length_ms == 195000

    file = session.exec(select(Source).where(Source.kind == SourceKind.file)).one()
    assert file.recording_id is None  # a container
    assert Path(file.uri or "").relative_to(settings.library_dir).as_posix() == (
        "Tales Under The Oak/The Toad Folk II/The Toad Folk II.opus"
    )
    slices = sorted(file.children, key=lambda s: s.start_ms or 0)
    assert [(s.start_ms, s.end_ms) for s in slices[:2]] == [(0, 315000), (315000, 510000)]
    assert slices[-1].end_ms == 3977000
    response = client.get(f"/tracks/{tracks[1].id}")
    playability = response.json()["playability"]
    assert (playability["state"], playability["playback"]["start_ms"]) == ("slice", 315000)


# A playlist


def test_playlist_is_one_release_group(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp
) -> None:
    job = post_video(client, PLAYLIST)
    assert job["input"] == PLAYLIST_CANONICAL
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    # Entry 3, "Unreleased" (another channel), is not on the release MusicBrainz has.
    [issue] = issues(client, job)
    assert issue["kind"] == "uncertain_metadata"
    assert issue["subject"]["key"] == UNRELEASED
    assert issue["choices"] == ["keep_as_entered", "skip"]
    assert ytdlp.downloads == []

    review(client, job["id"], {"issue_id": issue["id"], "choice": "keep_as_entered"})
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    release_group = session.exec(select(ReleaseGroup)).one()
    assert release_group.title == "Neighborhoods"
    assert release_group.release_mbid == uuid.UUID(NEIGHBORHOODS_RELEASE)
    assert release_group.artist_credit.name == "Ernest Hood"
    tracks = sorted(release_group.tracks, key=lambda t: t.position)
    assert [t.recording.title for t in tracks] == NEIGHBORHOODS
    assert all(t.recording.mbid for t in tracks)
    files = session.exec(select(Source).where(Source.kind == SourceKind.file)).all()
    assert len(files) == 9 and len(ytdlp.downloads) == 9
    assert {f.recording_id for f in files} >= {t.recording_id for t in tracks}
    # A recording without release group; without artist fields or "Artist - Title",
    # a playlist entry is credited to the playlist's artist.
    unreleased = session.exec(select(Recording).where(Recording.title == "Unreleased")).one()
    assert (unreleased.artist_credit.name, unreleased.tracks) == ("Ernest Hood", [])


def test_failed_playlist_entries_do_not_lose_the_rest(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp
) -> None:
    removed = "https://www.youtube.com/watch?v=YwNUiTGxqG4"  # 2. At the Store
    blocked = "https://www.youtube.com/watch?v=01rc740rdNY"  # 9. Night Games
    ytdlp.unavailable.add(removed)
    ytdlp.failing_downloads.add(blocked)
    job = post_video(client, PLAYLIST)
    run()
    unreleased = next(i for i in issues(client, job) if i["subject"]["key"] == UNRELEASED)
    review(client, job["id"], {"issue_id": unreleased["id"], "choice": "skip"})
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [gone] = [i for i in issues(client, job) if i["answer"] is None]
    assert (gone["kind"], gone["subject"]["key"]) == ("metadata_not_found", removed)
    assert "This video has been removed" in gone["message"]
    assert ytdlp.downloads == []  # nothing is downloaded while metadata is open

    # Skipped: the others are downloaded and imported; one of them fails.
    review(client, job["id"], {"issue_id": gone["id"], "choice": "skip"})
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [failed] = [i for i in issues(client, job) if i["answer"] is None]
    assert (failed["kind"], failed["subject"]["key"]) == ("download_failed", blocked)
    assert "available in your country" in failed["message"]
    assert failed["blocking"] is False
    assert len(session.exec(select(Source).where(Source.kind == SourceKind.file)).all()) == 6

    review(client, job["id"], {"issue_id": failed["id"], "choice": "skip"})
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    files = session.exec(select(Source).where(Source.kind == SourceKind.file)).all()
    assert len(files) == 6
    # The entries downloaded before the failure were not downloaded again.
    assert len(ytdlp.downloads) == 7
    release_group = session.exec(select(ReleaseGroup)).one()
    with_files = {f.recording_id for f in files}
    played = [t.recording.title for t in release_group.tracks if t.recording_id in with_files]
    assert sorted(played) == sorted(set(NEIGHBORHOODS) - {"At the Store", "Night Games"})
    assert count(session, Track) == 8  # the release's tracks, as MusicBrainz lists them


# The real fixture URLs (metadata only)


@pytest.mark.network
@pytest.mark.parametrize("url", [SINGLE_VIDEO, PLAYLIST_CANONICAL])
def test_network_metadata(session: Session, settings: Settings, url: str) -> None:
    job = create_import(session, ImportKind.youtube, url)
    with MusicBrainz() as musicbrainz:
        ctx = JobContext(session, job, musicbrainz, settings)
        proposal = YouTubeImporter().build(ctx)
        match_proposal(ctx, proposal)
    if url == SINGLE_VIDEO:
        [track] = proposal.loose_tracks
        assert (track.title, track.credit[0].credited_name) == ("salt echoes", "aedis")
    else:
        [release] = proposal.releases
        assert release.title == "Neighborhoods"
        assert release.release_mbid is not None
        assert len(release.tracks) == 8
        assert [t.key for t in proposal.loose_tracks] == [UNRELEASED]
