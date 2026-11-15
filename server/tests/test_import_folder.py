"""Folder import end to end: generated albums in temp dirs, MusicBrainz from the mock."""

import hashlib
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from sqlmodel import Session, SQLModel, func, select

from ep.metadata.musicbrainz import MBRelease
from ep.models import (
    Artist,
    EntityType,
    ExternalId,
    ImportJob,
    JobState,
    Recording,
    ReleaseGroup,
    Source,
    SourceKind,
    Track,
)
from ep.settings import Settings
from tests.helpers import credit, make_artist, make_recording, make_release_group, post
from tests.imports import get_job, post_import, review, run_queued, write_audio
from tests.mock_musicbrainz import PROMISES_RELEASE, PROMISES_RELEASE_GROUP, fixture

PROMISES = MBRelease.model_validate_json(fixture("release_promises"))
PROMISES_TRACKS = PROMISES.media[0].tracks
PROMISES_ARTIST = "Floating Points, Pharoah Sanders & The London Symphony Orchestra"


def count(session: Session, model: type[SQLModel]) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def music(settings: Settings) -> Path:
    """A media root for the test's folders."""
    return settings.media_roots[2]


@pytest.fixture
def run(session: Session, job_queue: InMemoryConnector, settings: Settings) -> Any:
    def run() -> dict[uuid.UUID, JobState | None]:
        return run_queued(session, job_queue, settings)

    return run


@pytest.fixture(scope="session")
def cover_png(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("cover") / "cover.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=8x8", "-frames:v", "1",
         str(path)],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def promises_untagged(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Promises without MusicBrainz IDs, each file as long as its MusicBrainz track."""
    directory = tmp_path_factory.mktemp("promises") / "Promises"
    for track in PROMISES_TRACKS:
        write_audio(
            directory / f"{track.position:02d} {track.title}.flac",
            (track.length or 0) / 1000,
            {
                "title": track.title,
                "artist": PROMISES_ARTIST,
                "album_artist": PROMISES_ARTIST,
                "album": "Promises",
                "track": track.position,
            },
        )
    return directory


def promises_tagged(directory: Path, cover: Path) -> Path:
    """Promises tagged with MusicBrainz IDs (Picard style), short files, and cover.png."""
    for track in PROMISES_TRACKS:
        write_audio(
            directory / f"{track.position:02d}.flac",
            1,
            {
                "title": track.title,
                "artist": "Floating Points",
                "album": "Promises (tagged)",
                "track": track.position,
                "MUSICBRAINZ_ALBUMID": PROMISES_RELEASE,
                "MUSICBRAINZ_RELEASETRACKID": str(track.id),
                "MUSICBRAINZ_TRACKID": str(track.recording.id),
            },
        )
    shutil.copy(cover, directory / "Cover.PNG")
    return directory


def sine_waves(directory: Path, cover: Path | None = None, tracks: int = 3) -> Path:
    """An album MusicBrainz does not know: "Sine Waves" by The Oscillators."""
    for n in range(1, tracks + 1):
        write_audio(
            directory / f"{n}.mp3",
            2,
            {
                "title": ["Sine One", "Square Wave", "Triangle Shape", "Sawtooth Drone"][n - 1],
                "artist": "The Oscillators",
                "album": "Sine Waves",
                "track": f"{n}/{tracks}",
                "date": "2019-04-01",
                "TSRC": f"GBAAA190000{n}",
            },
            cover=cover if n == 1 else None,
        )
    return directory


def release_group(session: Session) -> ReleaseGroup:
    return session.exec(select(ReleaseGroup)).one()


def test_folder_with_musicbrainz_ids(
    client: TestClient, session: Session, music: Path, run: Any, cover_png: Path, settings: Settings
) -> None:
    album = promises_tagged(music / "Floating Points" / "Promises", cover_png)
    before = {path: digest(path) for path in album.iterdir()}
    job = post_import(client, music / "Floating Points")
    assert job["state"] == "queued"
    assert run() == {uuid.UUID(job["id"]): JobState.done}

    rg = release_group(session)
    assert (rg.mbid, rg.release_mbid, rg.title) == (
        uuid.UUID(PROMISES_RELEASE_GROUP),
        uuid.UUID(PROMISES_RELEASE),
        "Promises",
    )
    assert rg.artist_credit.name == PROMISES_ARTIST
    tracks = client.get(f"/release-groups/{rg.id}/tracks").json()
    assert [t["recording"]["title"] for t in tracks] == [t.recording.title for t in PROMISES_TRACKS]
    assert all(t["playback"] and t["playback"]["start_ms"] == 0 for t in tracks)
    sources = session.exec(select(Source).order_by(Source.uri)).all()
    assert len(sources) == 9
    for source in sources:
        assert source.kind is SourceKind.file and not source.managed
        assert source.sha256 == digest(Path(source.uri or ""))
        assert source.codec == "flac" and source.recording_id is not None
    isrcs = session.exec(select(ExternalId.value).where(ExternalId.scheme == "isrc")).all()
    assert "USLB12010001" in isrcs
    # The cover is stored in the library, nothing is written next to the files.
    assert rg.cover_path and Path(rg.cover_path).is_relative_to(settings.library_dir)
    assert {path: digest(path) for path in album.iterdir()} == before

    proposal = get_job(client, job["id"])["proposal"]
    assert proposal["releases"][0]["target"]["kind"] == "musicbrainz"
    assert {a["target"]["kind"] for a in proposal["artists"]} == {"musicbrainz"}


def test_folder_found_by_musicbrainz_search(
    client: TestClient, session: Session, music: Path, run: Any, promises_untagged: Path
) -> None:
    shutil.copytree(promises_untagged, music / "Promises")
    job = post_import(client, music / "Promises")
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    rg = release_group(session)
    # Among nine fitting editions the worldwide digital one.
    assert rg.release_mbid == uuid.UUID(PROMISES_RELEASE)
    assert count(session, Track) == 9
    log = [entry["message"] for entry in get_job(client, job["id"])["log"]]
    assert f"found in MusicBrainz: Promises — {PROMISES_ARTIST} ({PROMISES_RELEASE})" in log


@pytest.mark.parametrize("choice", ["keep_as_entered", "pick_candidate", "skip"])
def test_musicbrainz_candidates_that_do_not_fit(
    client: TestClient, session: Session, music: Path, run: Any, choice: str
) -> None:
    for n, track in enumerate(PROMISES_TRACKS, start=1):
        write_audio(
            music / "Promises" / f"{n}.flac",
            1,  # far shorter than on MusicBrainz
            {"title": track.title, "album_artist": "Floating Points", "album": "Promises",
             "track": n},
        )  # fmt: skip
    job = post_import(client, music / "Promises")
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    read = get_job(client, job["id"])
    [issue] = read["proposal"]["issues"]
    assert issue["kind"] == "uncertain_metadata"
    assert issue["choices"] == ["pick_candidate", "keep_as_entered", "skip"]
    assert len(issue["candidates"]) == 5
    assert issue["candidates"][0] == {
        "type": "musicbrainz_release",
        "id": PROMISES_RELEASE,
        "title": "Promises",
        "subtitle": PROMISES_ARTIST,
        "detail": "2021-03-26 · XW · Digital Media · 9 tracks",
        "score": 100.0,
    }
    # Undecided until answered: nothing below the release is matched yet.
    assert {t["target"]["kind"] for t in read["proposal"]["releases"][0]["tracks"]} == {"undecided"}

    answer: dict[str, Any] = {"issue_id": issue["id"], "choice": choice}
    if choice == "pick_candidate":
        answer["candidate_id"] = PROMISES_RELEASE
    review(client, job["id"], answer)
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    if choice == "skip":
        assert count(session, ReleaseGroup) == count(session, Source) == 0
        return
    rg = release_group(session)
    assert rg.title == "Promises"
    assert rg.release_mbid == (uuid.UUID(PROMISES_RELEASE) if choice == "pick_candidate" else None)
    assert rg.artist_credit.name == (
        PROMISES_ARTIST if choice == "pick_candidate" else "Floating Points"
    )
    assert count(session, Source) == 9


def test_folder_not_in_musicbrainz(
    client: TestClient, session: Session, music: Path, run: Any, cover_png: Path, settings: Settings
) -> None:
    sine_waves(music / "Sine Waves", cover=cover_png)
    job = post_import(client, music / "Sine Waves")
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    rg = release_group(session)
    assert (rg.title, rg.date, rg.mbid, rg.artist_credit.name) == (
        "Sine Waves",
        "2019-04-01",
        None,
        "The Oscillators",
    )
    assert rg.cover_path and Path(rg.cover_path).is_relative_to(settings.library_dir)
    tracks = client.get(f"/release-groups/{rg.id}/tracks").json()
    assert [(t["position"], t["recording"]["title"]) for t in tracks] == [
        (1, "Sine One"),
        (2, "Square Wave"),
        (3, "Triangle Shape"),
    ]
    # MP3 padding depends on the encoder version.
    assert tracks[0]["recording"]["length_ms"] == pytest.approx(2000, abs=250)
    ids = client.get(f"/recordings/{tracks[0]['recording']['id']}/external-ids").json()
    assert [(i["scheme"], i["value"]) for i in ids] == [("isrc", "GBAAA1900001")]
    log = [entry["message"] for entry in get_job(client, job["id"])["log"]]
    assert (
        "'Sine Waves — The Oscillators' not found in MusicBrainz; importing the tags as entered"
        in log
    )
    assert get_job(client, job["id"])["proposal"]["issues"] == []


def test_reimport_does_nothing(client: TestClient, session: Session, music: Path, run: Any) -> None:
    sine_waves(music / "Sine Waves")
    post_import(client, music / "Sine Waves")
    run()
    counts = [count(session, m) for m in (Artist, ReleaseGroup, Recording, Track, Source)]
    job = post_import(client, music / "Sine Waves")
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    assert [count(session, m) for m in (Artist, ReleaseGroup, Recording, Track, Source)] == counts
    log = [entry["message"] for entry in get_job(client, job["id"])["log"]]
    assert "3 of 3 audio file(s) are already in the library" in log
    assert log[-1] == "done: nothing new to import"


def test_new_file_in_imported_folder_joins_its_release_group(
    client: TestClient, session: Session, music: Path, run: Any
) -> None:
    sine_waves(music / "Sine Waves")
    post_import(client, music / "Sine Waves")
    run()
    sine_waves(music / "Sine Waves", tracks=4)  # rewrites 1-3 identically, adds 4
    for path in (music / "Sine Waves").glob("[123].mp3"):
        source = session.exec(select(Source).where(Source.uri == str(path))).one()
        source.sha256 = digest(path)
    post_import(client, music / "Sine Waves")
    run()
    assert count(session, ReleaseGroup) == 1
    assert [t.position for t in release_group(session).tracks] == [1, 2, 3, 4]


def test_cue_sheet_album(client: TestClient, session: Session, music: Path, run: Any) -> None:
    album = music / "Live"
    write_audio(album / "live.flac", 30, {"album": "ignored: the cue sheet wins"})
    (album / "live.cue").write_text(
        'REM DATE 1999\nPERFORMER "The Oscillators"\nTITLE "Live at the Lab"\n'
        'FILE "live.wav" WAVE\n'
        '  TRACK 01 AUDIO\n    TITLE "Warm Up"\n    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n    TITLE "Feedback"\n    PERFORMER "Guest"\n'
        "    INDEX 00 00:09:70\n    INDEX 01 00:10:00\n"
        '  TRACK 03 AUDIO\n    TITLE "Encore"\n    ISRC GBAAA9900003\n    INDEX 01 00:20:37\n',
        encoding="utf-8",
    )
    job = post_import(client, album)
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    rg = release_group(session)
    assert (rg.title, rg.date, rg.artist_credit.name) == (
        "Live at the Lab",
        "1999",
        "The Oscillators",
    )
    [file] = session.exec(select(Source).where(Source.kind == SourceKind.file)).all()
    assert file.recording_id is None and file.uri == str(album / "live.flac")
    tracks = client.get(f"/release-groups/{rg.id}/tracks").json()
    played = [
        (t["recording"]["title"], t["recording"]["artist_credit"]["name"], t["playback"])
        for t in tracks
    ]
    assert [(title, artist, p["start_ms"], p["end_ms"]) for title, artist, p in played] == [
        ("Warm Up", "The Oscillators", 0, 10000),
        ("Feedback", "Guest", 10000, 20493),
        ("Encore", "The Oscillators", 20493, None),
    ]
    assert {p["file_source_id"] for *_, p in played} == {str(file.id)}
    assert tracks[2]["recording"]["length_ms"] == pytest.approx(30000 - 20493, abs=100)
    slices = session.exec(select(Source).where(Source.kind == SourceKind.slice)).all()
    assert {s.parent_id for s in slices} == {file.id}
    # Streaming a slice serves the whole file.
    stream = client.get(f"/sources/{tracks[1]['playback']['source_id']}/stream")
    assert stream.status_code == 200
    assert stream.content == (album / "live.flac").read_bytes()


def test_unreadable_file(client: TestClient, session: Session, music: Path, run: Any) -> None:
    album = sine_waves(music / "Sine Waves", tracks=2)
    (album / "3.mp3").write_bytes(b"not audio at all")
    job = post_import(client, album)
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "unreadable_file"
    assert issue["id"] == f"unreadable_file:{album / '3.mp3'}"
    assert issue["subject"]["type"] == "medium"
    assert issue["choices"] == ["skip", "retry"]
    assert "cannot be read as audio" in issue["message"]

    review(client, job["id"], {"issue_id": issue["id"], "choice": "retry"})  # still broken
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    review(client, job["id"], {"issue_id": issue["id"], "choice": "skip"})
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    assert count(session, Source) == 2


def test_attaches_to_existing_loose_recording(
    client: TestClient, session: Session, music: Path, run: Any
) -> None:
    artist = make_artist(client, "The Oscillators")
    loose = make_recording(client, artist, "Square Wave")
    sine_waves(music / "Sine Waves")
    job = post_import(client, music / "Sine Waves")
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    rg = release_group(session)
    assert [str(t.recording_id) for t in rg.tracks][1] == loose["id"]
    assert count(session, Recording) == 3
    assert count(session, Artist) == 1
    sources = client.get(f"/recordings/{loose['id']}/sources").json()
    assert [s["uri"] for s in sources] == [str(music / "Sine Waves" / "2.mp3")]


def test_uncertain_release_group_and_recording(
    client: TestClient, session: Session, music: Path, run: Any
) -> None:
    artist = make_artist(client, "The Oscillators")
    first = make_release_group(client, artist, "Sine Waves", date="2019")
    make_release_group(client, artist, "Sine Waves", date="2020")
    loose = [make_recording(client, artist, "Sine One") for _ in range(2)]
    sine_waves(music / "Sine Waves")
    job = post_import(client, music / "Sine Waves")
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "uncertain_release_group"
    assert issue["choices"] == ["pick_candidate", "create_new", "skip"]
    assert {c["detail"] for c in issue["candidates"]} == {"2019", "2020"}

    # Into an empty release group: its tracks are matched one by one.
    review(
        client,
        job["id"],
        {"issue_id": issue["id"], "choice": "pick_candidate", "candidate_id": first["id"]},
    )
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    issues = get_job(client, job["id"])["proposal"]["issues"]
    open_issue = next(i for i in issues if i["answer"] is None)
    assert open_issue["kind"] == "uncertain_recording"
    assert open_issue["subject"] == {
        "type": "track",
        "key": str(music / "Sine Waves" / "1.mp3"),
        "label": "1. Sine One — The Oscillators",
    }
    assert open_issue["choices"] == ["pick_candidate", "create_new", "skip"]
    assert open_issue["candidates"][0]["detail"] is None  # no length
    review(
        client,
        job["id"],
        {"issue_id": open_issue["id"], "choice": "pick_candidate", "candidate_id": loose[1]["id"]},
    )
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    tracks = session.exec(
        select(Track).where(Track.release_group_id == uuid.UUID(first["id"]))
    ).all()
    assert len(tracks) == 3
    assert str(next(t for t in tracks if t.position == 1).recording_id) == loose[1]["id"]
    assert count(session, ReleaseGroup) == 2


def test_tagged_release_unknown_to_musicbrainz(
    client: TestClient, session: Session, music: Path, run: Any
) -> None:
    unknown = uuid.uuid4()
    write_audio(
        music / "Gone" / "1.flac", 1, {"title": "Gone", "artist": "Nobody", "album": "Gone",
                                       "MUSICBRAINZ_ALBUMID": str(unknown)}
    )  # fmt: skip
    job = post_import(client, music / "Gone")
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "metadata_not_found"
    assert str(unknown) in issue["message"]
    assert issue["choices"] == ["keep_as_entered", "skip", "retry"]
    review(client, job["id"], {"issue_id": issue["id"], "choice": "keep_as_entered"})
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    rg = release_group(session)
    assert (rg.title, rg.release_mbid) == ("Gone", None)


def test_release_conflict(
    client: TestClient, session: Session, music: Path, run: Any, cover_png: Path
) -> None:
    artist = make_artist(client, "Floating Points")
    existing = make_release_group(
        client, artist, "Promises", mbid=PROMISES_RELEASE_GROUP, release_mbid=str(uuid.uuid4())
    )
    vinyl = make_recording(client, artist, "Promises: Movement 1")
    track = {"release_group_id": existing["id"], "recording_id": vinyl["id"], "position": 1}
    post(client, "/tracks", track)
    promises_tagged(music / "Promises", cover_png)
    job = post_import(client, music / "Promises")
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "release_conflict"
    assert issue["choices"] == ["pick_candidate", "skip"]
    assert [c["id"] for c in issue["candidates"]] == [existing["id"]]

    review(
        client,
        job["id"],
        {"issue_id": issue["id"], "choice": "pick_candidate", "candidate_id": existing["id"]},
    )
    # Track 1 takes the recording at position 1; the others look like it ("Movement 2"):
    # never matched automatically, the user is asked.
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    issues = get_job(client, job["id"])["proposal"]["issues"]
    open_issues = [i for i in issues if i["answer"] is None]
    assert {i["kind"] for i in open_issues} == {"uncertain_recording"}
    assert len(open_issues) == 8
    review(client, job["id"], *({"issue_id": i["id"], "choice": "create_new"} for i in open_issues))
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    tracks = client.get(f"/release-groups/{existing['id']}/tracks").json()
    assert len(tracks) == 9
    assert tracks[0]["recording"]["id"] == vinyl["id"]  # the track at that position
    assert count(session, ReleaseGroup) == 1


def test_import_input_is_validated(client: TestClient, music: Path, tmp_path: Path) -> None:
    def create(body: dict[str, Any]) -> Any:
        return client.post("/imports", json=body)

    outside = tmp_path / "outside"
    outside.mkdir()
    for body, msg in [
        ({"kind": "folder", "input": "relative/path"}, "an absolute path is required"),
        ({"kind": "folder", "input": str(outside)}, "must be under one of the media roots"),
        ({"kind": "folder", "input": str(music / "missing")}, "not a directory"),
        (
            {"kind": "youtube", "input": "https://youtu.be/x"},
            "youtube imports are not available yet",
        ),
    ]:
        response = create(body)
        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"] == msg


def test_folder_removed_before_the_job_runs(
    client: TestClient, session: Session, music: Path, run: Any
) -> None:
    sine_waves(music / "Sine Waves")
    job = post_import(client, music / "Sine Waves")
    shutil.rmtree(music / "Sine Waves")
    assert run() == {uuid.UUID(job["id"]): JobState.failed}
    read = get_job(client, job["id"])
    assert read["error"].endswith("is not a directory (moved or deleted?)")
    assert read["actions"] == ["retry", "dismiss"]
    assert session.get_one(ImportJob, uuid.UUID(job["id"])).state is JobState.failed


def test_isrc_external_ids_are_not_duplicated(
    client: TestClient, session: Session, music: Path, run: Any
) -> None:
    artist = make_artist(client, "The Oscillators")
    recording = post(client, "/recordings", {"title": "Sine One", "artist_credit": credit(artist)})
    isrc = {"scheme": "isrc", "value": "GBAAA1900001"}
    client.post(f"/recordings/{recording['id']}/external-ids", json=isrc)
    sine_waves(music / "Sine Waves")
    post_import(client, music / "Sine Waves")
    run()
    rows = session.exec(
        select(ExternalId).where(
            ExternalId.entity_type == EntityType.recording,
            ExternalId.entity_id == uuid.UUID(recording["id"]),
        )
    ).all()
    assert [r.value for r in rows] == ["GBAAA1900001"]
