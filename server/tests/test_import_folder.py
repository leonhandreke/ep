"""Folder import end to end: generated albums in temp dirs, MusicBrainz from the mock."""

import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from sqlmodel import Session, SQLModel, func, select

from ep.importers import resolve
from ep.importers.folder import FolderImporter, cover_art_url
from ep.importers.resolve import catalog_number_in, search_titles
from ep.metadata.musicbrainz import MBRelease
from ep.models import (
    Artist,
    ExternalId,
    ImportKind,
    JobState,
    Recording,
    ReleaseGroup,
    Source,
    SourceKind,
    Track,
)
from ep.settings import Settings
from tests import mock_musicbrainz
from tests.fakes import png_bytes, web_transport
from tests.helpers import make_artist, make_recording, make_release_group, post
from tests.imports import (
    TEST_IMPORTERS,
    get_job,
    post_import,
    review,
    run_queued,
    write_audio,
)
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


def promises_tagged(directory: Path, cover: Path | None) -> Path:
    """Promises tagged with MusicBrainz IDs (Picard style), short files, and cover.png
    (with ``cover``)."""
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
    if cover is not None:
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
    assert all(t["playability"]["playback"]["start_ms"] == 0 for t in tracks)
    sources = session.exec(select(Source).order_by(Source.uri)).all()
    assert len(sources) == 9
    for source in sources:
        assert source.kind is SourceKind.file and not source.managed
        assert source.sha256 == digest(Path(source.uri or ""))
        assert source.codec == "flac" and source.recording_id is not None
    isrcs = session.exec(select(ExternalId.value).where(ExternalId.scheme == "isrc")).all()
    assert "USLB12010001" in isrcs
    # A track's source panel: the file's facts and the import that added it.
    [listed] = client.get(f"/recordings/{tracks[0]['recording']['id']}/sources").json()
    file = listed["file"]
    assert (file["codec"], file["media_type"], file["exists"]) == ("flac", "audio/flac", True)
    assert file["size"] == Path(file["path"]).stat().st_size
    assert file["channels"] is not None and file["sample_rate"] is not None
    imported_by = listed["imported_by"]
    assert (imported_by["id"], imported_by["kind"], imported_by["state"]) == (
        job["id"],
        "folder",
        "done",
    )
    assert imported_by["input"] == job["input"]
    assert imported_by["input_host_path"] == job["input_host_path"]
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


def test_folder_found_by_search_without_edition(
    client: TestClient, session: Session, music: Path, run: Any
) -> None:
    album = deluxe_promises(music / "Promises (Deluxe Edition)")
    job = post_import(client, album)
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    assert release_group(session).release_mbid == uuid.UUID(PROMISES_RELEASE)
    log = [entry["message"] for entry in get_job(client, job["id"])["log"]]
    assert (
        f"found in MusicBrainz (searched as 'Promises'): Promises — {PROMISES_ARTIST} "
        f"({PROMISES_RELEASE})"
    ) in log


def deluxe_promises(album: Path) -> Path:
    """Promises, untagged with MusicBrainz IDs, as "Promises (Deluxe Edition)"."""
    for track in PROMISES_TRACKS:
        write_audio(
            album / f"{track.position:02d}.flac",
            (track.length or 0) / 1000,
            {"title": track.title, "album_artist": PROMISES_ARTIST,
             "album": "Promises (Deluxe Edition)", "track": track.position},
        )  # fmt: skip
    return album


def test_reimport_links_album_to_musicbrainz(
    client: TestClient, session: Session, music: Path, run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An album imported before as tagged (MusicBrainz search found nothing then) is
    looked up again on re-import and linked in place: same release group and
    recordings, which gain the MBIDs; the title stays; no file imported again."""
    album = deluxe_promises(music / "Promises (Deluxe Edition)")
    with monkeypatch.context() as patch:  # as before the search without edition
        patch.setattr(resolve, "search_titles", lambda title: [title])
        job = post_import(client, album)
        assert run() == {uuid.UUID(job["id"]): JobState.done}
    rg = release_group(session)
    assert (rg.title, rg.mbid, rg.release_mbid, rg.cover_path) == (
        "Promises (Deluxe Edition)",
        None,
        None,
        None,
    )
    recordings = {r.id for r in session.exec(select(Recording)).all()}
    artists = count(session, Artist)

    assert client.post(f"/imports/{job['id']}/retry").status_code == 200
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    session.expire_all()
    rg = release_group(session)
    assert (rg.title, rg.mbid, rg.release_mbid) == (
        "Promises (Deluxe Edition)",
        uuid.UUID(PROMISES_RELEASE_GROUP),
        uuid.UUID(PROMISES_RELEASE),
    )
    assert rg.cover_path  # from the Cover Art Archive
    by_title = {r.title: r for r in session.exec(select(Recording)).all()}
    assert set(r.id for r in by_title.values()) == recordings
    assert [by_title[t.recording.title].mbid for t in PROMISES_TRACKS] == [
        t.recording.id for t in PROMISES_TRACKS
    ]
    assert (count(session, Track), count(session, Source)) == (9, 9)
    # The release's other artists credit nothing in the library: not added.
    assert count(session, Artist) == artists
    read = get_job(client, job["id"])
    assert read["proposal"]["issues"] == []
    log = [entry["message"] for entry in read["log"]]
    assert "1 album(s) imported before have no MusicBrainz release; looking them up again" in log
    assert (
        f"found in MusicBrainz (searched as 'Promises'): Promises — {PROMISES_ARTIST} "
        f"({PROMISES_RELEASE})"
    ) in log


def test_reimport_raises_no_issue_for_album_in_library(
    client: TestClient, session: Session, music: Path, run: Any
) -> None:
    """Candidates that do not fit an album imported before: it stays as it is, without
    asking again."""
    for n, track in enumerate(PROMISES_TRACKS, start=1):
        write_audio(
            music / "Promises" / f"{n}.flac",
            1,  # far shorter than on MusicBrainz
            {"title": track.title, "album_artist": "Floating Points", "album": "Promises",
             "track": n},
        )  # fmt: skip
    job = post_import(client, music / "Promises")
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    review(client, job["id"], {"issue_id": issue["id"], "choice": "keep_as_entered"})
    assert run() == {uuid.UUID(job["id"]): JobState.done}

    again = post_import(client, music / "Promises")
    assert run() == {uuid.UUID(again["id"]): JobState.done}
    read = get_job(client, again["id"])
    assert read["proposal"]["issues"] == []
    assert "'Promises — Floating Points' not found in MusicBrainz; left as it is" in [
        entry["message"] for entry in read["log"]
    ]
    assert release_group(session).release_mbid is None


@pytest.mark.parametrize(
    ("title", "fallbacks"),
    [
        ("Midnights (3am Edition)", ["Midnights"]),
        ("Abbey Road [Super Deluxe Edition] (Remastered)", ["Abbey Road"]),
        ("Sgt. Pepper - 2017 Remaster", ["Sgt. Pepper"]),
        (
            "The Dark Side Of The Moon [Toshiba-EMI Black Triangle CP35-3017]",
            ["The Dark Side Of The Moon"],
        ),
        ("Kind of Blue (Legacy Edition) [CK 64935]", ["Kind of Blue [CK 64935]", "Kind of Blue"]),
        ("(What's the Story) Morning Glory?", []),
        ("Editions of You", []),
        ("(Deluxe Edition)", []),
    ],
)
def test_search_titles(title: str, fallbacks: list[str]) -> None:
    assert search_titles(title) == [title, *fallbacks]


def test_catalog_number_in_title() -> None:
    def release(*numbers: str) -> MBRelease:
        return MBRelease.model_validate(
            {"id": str(uuid.uuid4()), "title": "x",
             "label-info": [{"catalog-number": n} for n in numbers]}
        )  # fmt: skip

    title = "The Dark Side Of The Moon [Toshiba-EMI Black Triangle CP35-3017]"
    assert catalog_number_in(release("SHVL-804", "CP35-3017"), title)
    assert not catalog_number_in(release("CP35-301"), title)  # whole words only
    assert not catalog_number_in(release(), title)


def test_folder_cover_in_artwork_folder(
    client: TestClient, session: Session, music: Path, run: Any, cover_png: Path
) -> None:
    album = sine_waves(music / "Sine Waves")
    (album / "Artwork").mkdir()
    shutil.copy(cover_png, album / "Artwork" / "Front.png")
    (album / "Artwork" / "Disc.png").write_bytes(png_bytes())
    post_import(client, album)
    run()
    cover_path = release_group(session).cover_path
    assert cover_path and digest(Path(cover_path)) == digest(cover_png)


@pytest.mark.parametrize("choice", ["keep_as_entered"])
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


def test_folder_cover_only_image(
    client: TestClient, session: Session, music: Path, run: Any, cover_png: Path
) -> None:
    """Without a cover/folder/front image, the only image in the album folder is the
    cover; images in a subfolder (scans) do not count."""
    album = sine_waves(music / "Sine Waves")
    shutil.copy(cover_png, album / "The Oscillators - Sine Waves.png")
    (album / "scans").mkdir()
    shutil.copy(cover_png, album / "scans" / "back.png")
    job = post_import(client, album)
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    rg = release_group(session)
    assert rg.cover_path and digest(Path(rg.cover_path)) == digest(cover_png)


def test_folder_cover_from_cover_art_archive(
    client: TestClient,
    session: Session,
    music: Path,
    cover_png: Path,
    job_queue: InMemoryConnector,
    settings: Settings,
) -> None:
    """No cover in the folder (two images that could be anything): an album found in
    MusicBrainz gets its release's front cover from the Cover Art Archive."""
    album = promises_tagged(music / "Promises", cover=None)
    shutil.copy(cover_png, album / "back.png")
    shutil.copy(cover_png, album / "inlay.png")
    requests: list[str] = []
    importers = {**TEST_IMPORTERS, ImportKind.folder: FolderImporter(web_transport(requests))}
    job = post_import(client, album)
    assert run_queued(session, job_queue, settings, importers=importers) == {
        uuid.UUID(job["id"]): JobState.done
    }
    assert requests == [cover_art_url(uuid.UUID(PROMISES_RELEASE))]
    rg = release_group(session)
    assert rg.cover_path and Path(rg.cover_path).read_bytes() == png_bytes()


def test_folder_cover_not_in_cover_art_archive(
    client: TestClient,
    session: Session,
    music: Path,
    job_queue: InMemoryConnector,
    settings: Settings,
) -> None:
    """The Cover Art Archive has no cover for the release: imported without one."""
    album = promises_tagged(music / "Promises", cover=None)

    def not_found(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404)

    transport = httpx2.MockTransport(not_found)
    importers = {**TEST_IMPORTERS, ImportKind.folder: FolderImporter(transport)}
    job = post_import(client, album)
    assert run_queued(session, job_queue, settings, importers=importers) == {
        uuid.UUID(job["id"]): JobState.done
    }
    assert release_group(session).cover_path is None
    assert any("cover not fetched" in e["message"] for e in get_job(client, job["id"])["log"])


def test_reimport_adds_missing_cover(
    client: TestClient, session: Session, music: Path, run: Any, cover_png: Path
) -> None:
    """Re-importing a folder (a retry of a done job) gives an album imported from it
    without a cover the cover found now; one with a cover keeps it."""
    album = sine_waves(music / "Sine Waves")
    job = post_import(client, album)
    run()
    assert release_group(session).cover_path is None

    shutil.copy(cover_png, album / "Sine Waves.png")
    assert client.post(f"/imports/{job['id']}/retry").status_code == 200
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    rg = release_group(session)
    session.refresh(rg)
    cover_path = release_group(session).cover_path
    assert cover_path and digest(Path(cover_path)) == digest(cover_png)
    log = [entry["message"] for entry in get_job(client, job["id"])["log"]]
    assert "cover added to Sine Waves" in log
    assert count(session, ReleaseGroup) == 1 and count(session, Source) == 3

    cover_path = rg.cover_path
    (album / "Sine Waves.png").unlink()
    shutil.copy(cover_png, album / "cover.png")
    assert client.post(f"/imports/{job['id']}/retry").status_code == 200
    run()
    session.refresh(rg)
    assert rg.cover_path == cover_path


def test_reimport_adds_cover_from_cover_art_archive(
    client: TestClient,
    session: Session,
    music: Path,
    job_queue: InMemoryConnector,
    settings: Settings,
) -> None:
    """An album matched to MusicBrainz, imported while the Cover Art Archive had no
    cover: a re-import fetches it."""
    album = promises_tagged(music / "Promises", cover=None)

    def not_found(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404)

    job = post_import(client, album)
    no_covers = FolderImporter(httpx2.MockTransport(not_found))
    importers = {**TEST_IMPORTERS, ImportKind.folder: no_covers}
    run_queued(session, job_queue, settings, importers=importers)
    rg = release_group(session)
    assert rg.cover_path is None

    requests: list[str] = []
    importers = {**TEST_IMPORTERS, ImportKind.folder: FolderImporter(web_transport(requests))}
    assert client.post(f"/imports/{job['id']}/retry").status_code == 200
    assert run_queued(session, job_queue, settings, importers=importers) == {
        uuid.UUID(job["id"]): JobState.done
    }
    assert requests == [cover_art_url(uuid.UUID(PROMISES_RELEASE))]
    session.refresh(rg)
    cover_path = release_group(session).cover_path
    assert cover_path and Path(cover_path).read_bytes() == png_bytes()


def test_reimport_adds_cover_to_cue_sheet_album(
    client: TestClient, session: Session, music: Path, run: Any, cover_png: Path
) -> None:
    """A cue sheet album: its tracks are slices of one file source."""
    album = music / "Live"
    write_audio(album / "live.flac", 4)
    (album / "live.cue").write_text(
        'PERFORMER "The Oscillators"\nTITLE "Live at the Lab"\nFILE "live.flac" WAVE\n'
        '  TRACK 01 AUDIO\n    TITLE "Warm Up"\n    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n    TITLE "Encore"\n    INDEX 01 00:02:00\n',
        encoding="utf-8",
    )
    job = post_import(client, album)
    run()
    rg = release_group(session)
    assert rg.title == "Live at the Lab" and rg.cover_path is None

    shutil.copy(cover_png, album / "The Oscillators - Live.png")
    assert client.post(f"/imports/{job['id']}/retry").status_code == 200
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    session.refresh(rg)
    cover_path = release_group(session).cover_path
    assert cover_path and digest(Path(cover_path)) == digest(cover_png)


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
        (
            t["recording"]["title"],
            t["recording"]["artist_credit"]["name"],
            t["playability"]["playback"],
        )
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
    stream = client.get(f"/sources/{tracks[1]['playability']['playback']['source_id']}/stream")
    assert stream.status_code == 200
    assert stream.content == (album / "live.flac").read_bytes()


def test_unreadable_file(client: TestClient, session: Session, music: Path, run: Any) -> None:
    album = sine_waves(music / "Sine Waves", tracks=2)
    (album / "3.mp3").write_bytes(b"not audio at all")
    job = post_import(client, album)
    # The readable files are imported; the unreadable one waits in the inbox.
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    assert count(session, Source) == 2
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["blocking"] is False
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


def test_uncertain_release_group_and_recording(
    client: TestClient, session: Session, music: Path, run: Any
) -> None:
    artist = make_artist(client, "The Oscillators")
    first = make_release_group(client, artist, "Sine Waves", date="2019")
    # Same year: either could be it (another year would decide it, see test_matching).
    make_release_group(client, artist, "Sine Waves", date="2019-11")
    loose = [make_recording(client, artist, "Sine One") for _ in range(2)]
    sine_waves(music / "Sine Waves")
    job = post_import(client, music / "Sine Waves")
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "uncertain_release_group"
    assert issue["choices"] == ["pick_candidate", "create_new", "skip"]
    assert {c["detail"] for c in issue["candidates"]} == {"2019", "2019-11"}

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
    # Track 1 takes the recording at position 1; the others look like it ("Movement 2"),
    # but it is taken: they are new.
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
            "must be a YouTube video or playlist URL",
        ),
        (
            {"kind": "bandcamp", "input": "https://bandcamp.com/discover"},
            "must be a Bandcamp album or track page URL (https://<name>.bandcamp.com/album/...)",
        ),
        (
            {"kind": "nts", "input": "https://www.nts.live/shows/x"},
            "must be an NTS episode URL (https://www.nts.live/shows/<show>/episodes/<episode>)",
        ),
    ]:
        response = create(body)
        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"] == msg


def test_uncertain_artist_of_a_musicbrainz_release(
    client: TestClient, session: Session, music: Path, run: Any, cover_png: Path
) -> None:
    make_artist(client, "Pharoah Sanders", disambiguation="the saxophonist")
    other = make_artist(client, "Pharoah Sanders", disambiguation="someone else")
    promises_tagged(music / "Promises", cover_png)
    job = post_import(client, music / "Promises")
    assert run() == {uuid.UUID(job["id"]): JobState.needs_review}
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    pharoah = PROMISES.artist_credit[1].artist
    assert issue["id"] == f"uncertain_artist:mbid:{pharoah.id}"
    assert issue["choices"] == ["pick_candidate", "create_new"]
    assert count(session, ReleaseGroup) == 0

    answer = {"issue_id": issue["id"], "choice": "pick_candidate", "candidate_id": other["id"]}
    review(client, job["id"], answer)
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    assert session.get_one(Artist, uuid.UUID(other["id"])).mbid == pharoah.id
    assert count(session, Artist) == 4  # two Pharoahs, Floating Points, the LSO


def test_release_credit_unused_by_the_album_creates_no_artist(
    client: TestClient,
    session: Session,
    music: Path,
    run: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release credited otherwise than its release group (Ashra's "New Age of Earth":
    the release group and recordings are Ash Ra Tempel, Manuel Göttsching): the album
    takes the release group's credit, and the release's artist, credited on nothing,
    is not created."""
    release = json.loads(mock_musicbrainz.fixture("release_promises"))
    release["artist-credit"] = [
        {
            "name": "Promises Ensemble",
            "joinphrase": "",
            "artist": {
                "id": "2d262241-ad1e-4b2d-904a-c38039f0a531",
                "name": "Promises Ensemble",
                "sort-name": "Promises Ensemble",
                "disambiguation": "",
            },
        }
    ]
    original = mock_musicbrainz.fixture
    monkeypatch.setattr(
        mock_musicbrainz,
        "fixture",
        lambda name: json.dumps(release).encode() if name == "release_promises" else original(name),
    )
    promises_tagged(music / "Promises", None)
    job = post_import(client, music / "Promises")
    assert run() == {uuid.UUID(job["id"]): JobState.done}
    [release_group] = session.exec(select(ReleaseGroup)).all()
    assert release_group.artist_credit.name == PROMISES_ARTIST
    names = set(session.exec(select(Artist.name)).all())
    assert names == {"Floating Points", "Pharoah Sanders", "London Symphony Orchestra"}


def test_musicbrainz_artist_named_by_its_latin_alias(
    client: TestClient,
    session: Session,
    music: Path,
    job_queue: InMemoryConnector,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release's artist named "跡部進一" on MusicBrainz is proposed and created as
    "Shinichi Atobe" (his primary English alias), with the original name, and credited
    so; the album's other artists (Latin names) are not looked up."""
    release = mock_musicbrainz.atobe_promises()
    original = mock_musicbrainz.fixture
    monkeypatch.setattr(
        mock_musicbrainz,
        "fixture",
        lambda name: release if name == "release_promises" else original(name),
    )
    promises_tagged(music / "Promises", None)
    job = post_import(client, music / "Promises")
    mb = mock_musicbrainz.MockMusicBrainz()
    assert run_queued(session, job_queue, settings, mb) == {uuid.UUID(job["id"]): JobState.done}
    aliases = [r.url.path for r in mb.requests if r.url.params.get("inc") == "aliases"]
    assert aliases == [f"/ws/2/artist/{mock_musicbrainz.SHINICHI_ATOBE}"]
    atobe = session.exec(
        select(Artist).where(Artist.mbid == uuid.UUID(mock_musicbrainz.SHINICHI_ATOBE))
    ).one()
    assert (atobe.name, atobe.sort_name, atobe.original_name) == (
        "Shinichi Atobe",
        "Atobe, Shinichi",
        "跡部進一",
    )
    [release_group] = session.exec(select(ReleaseGroup)).all()
    assert release_group.artist_credit.name.startswith("Shinichi Atobe, ")
    log = client.get(f"/imports/{job['id']}").json()["log"]
    assert any("'跡部進一' is named 'Shinichi Atobe'" in entry["message"] for entry in log)
    assert count(session, Artist) == 3


def test_reimport_renames_an_artist_still_named_by_musicbrainz(
    client: TestClient,
    session: Session,
    music: Path,
    job_queue: InMemoryConnector,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry of a done job (everything in the library already, so no artist is
    credited anew) renames an artist that still has its MusicBrainz name in another
    script, with one aliases lookup, and says so in the job log; one named otherwise
    in EP is left alone."""
    release = mock_musicbrainz.atobe_promises()
    original = mock_musicbrainz.fixture
    monkeypatch.setattr(
        mock_musicbrainz,
        "fixture",
        lambda name: release if name == "release_promises" else original(name),
    )
    promises_tagged(music / "Promises", None)
    job = post_import(client, music / "Promises")
    assert run_queued(session, job_queue, settings) == {uuid.UUID(job["id"]): JobState.done}
    atobe = session.exec(
        select(Artist).where(Artist.mbid == uuid.UUID(mock_musicbrainz.SHINICHI_ATOBE))
    ).one()
    atobe.name, atobe.original_name = "跡部進一", None  # as imported before
    session.commit()

    def reimport() -> list[str]:
        """The aliases lookups of a retry."""
        assert client.post(f"/imports/{job['id']}/retry").status_code == 200
        mb = mock_musicbrainz.MockMusicBrainz()
        done = {uuid.UUID(job["id"]): JobState.done}
        assert run_queued(session, job_queue, settings, mb) == done
        session.refresh(atobe)
        return [r.url.path for r in mb.requests if r.url.params.get("inc") == "aliases"]

    assert reimport() == [f"/ws/2/artist/{mock_musicbrainz.SHINICHI_ATOBE}"]
    assert (atobe.name, atobe.original_name) == ("Shinichi Atobe", "跡部進一")
    log = get_job(client, job["id"])["log"]
    assert any(e["message"] == "artist '跡部進一' renamed to 'Shinichi Atobe'" for e in log)
    assert count(session, Artist) == 3

    atobe.name, atobe.original_name = "Atobe", None  # renamed in EP
    session.commit()
    assert reimport() == []  # Latin script now: nothing to look up
    assert (atobe.name, atobe.original_name) == ("Atobe", None)
