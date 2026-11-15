"""NTS import end to end: the saved API responses, fake downloads, radio rows and endpoints."""

import json
import uuid
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from sqlmodel import Session, SQLModel, func, select

from ep.importers.framework import Importer
from ep.importers.nts import NtsImporter
from ep.models import (
    Artist,
    EntityTag,
    EntityType,
    ExternalId,
    ImportKind,
    JobState,
    Note,
    RadioEpisode,
    RadioEpisodeTrack,
    RadioShow,
    Recording,
    Source,
    SourceKind,
)
from ep.playback import recording_playbacks
from ep.radio import _deletable_audio, radio_only_ids
from ep.settings import Settings
from tests.fakes import (
    NTS,
    NTS_EPISODE,
    NTS_EPISODE_API,
    NTS_SOUNDCLOUD,
    FakeYtDlp,
    nts_transport,
)
from tests.helpers import credit, make_artist, make_recording, make_release_group, make_track, post
from tests.imports import get_job, review, run_queued
from tests.test_query_counts import count_selects

ENTRIES = 32
EVANGELINE_ISRC = "GBF089390057"
LIMBO_POSITION = 18


def count(session: Session, model: type[SQLModel]) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


@pytest.fixture
def ytdlp() -> FakeYtDlp:
    return FakeYtDlp()


@pytest.fixture
def responses() -> dict[str, httpx2.Response]:
    """NTS API responses that replace the fixtures, by URL."""
    return {}


@pytest.fixture
def run(
    session: Session,
    job_queue: InMemoryConnector,
    settings: Settings,
    ytdlp: FakeYtDlp,
    responses: dict[str, httpx2.Response],
):
    importers: dict[ImportKind, Importer] = {
        ImportKind.nts: NtsImporter(ytdlp, nts_transport(responses=responses))
    }

    def run() -> dict[uuid.UUID, JobState | None]:
        return run_queued(session, job_queue, settings, importers=importers)

    return run


def start(client: TestClient, url: str = NTS_EPISODE + "?utm_source=x") -> dict[str, Any]:
    response = client.post("/imports", json={"kind": "nts", "input": url})
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["input"] == NTS_EPISODE
    return job


@pytest.fixture
def library(client: TestClient, audio_dir: Path) -> dict[str, Any]:
    """What the library has before the import: "Evangeline" on a Cocteau Twins album
    (with its ISRC, and a file), "Ruth Marie" as a loose recording with a YouTube file,
    and two recordings "Limbo" by YMO that are equally good matches."""
    cocteau = make_artist(client, "Cocteau Twins")
    album = make_release_group(client, cocteau, "Four-Calendar Café")
    evangeline = make_recording(client, cocteau, "Evangeline", length_ms=262000)
    make_track(client, album, evangeline, 1)
    post(
        client,
        f"/recordings/{evangeline['id']}/external-ids",
        {"scheme": "isrc", "value": EVANGELINE_ISRC},
    )
    album_file = post(
        client,
        "/sources",
        {"kind": "file", "uri": str(audio_dir / "album.flac"), "recording_id": evangeline["id"]},
    )
    kozelek = make_artist(client, "Mark Kozelek")
    ruth = make_recording(client, kozelek, "Ruth Marie", length_ms=365000)
    link = post(client, "/sources", {"kind": "youtube", "uri": "https://www.youtube.com/watch?v=r"})
    post(
        client,
        "/sources",
        {
            "kind": "file",
            "uri": str(audio_dir / "song.mp3"),
            "parent_id": link["id"],
            "recording_id": ruth["id"],
        },
    )
    ymo = make_artist(client, "YMO")
    limbos = [make_recording(client, ymo, "Limbo", length_ms=length) for length in (240000, 300000)]
    return {
        "evangeline": evangeline,
        "album_file": album_file,
        "ruth": ruth,
        "kozelek": kozelek,
        "cocteau": cocteau,
        "limbos": limbos,
    }


def episode_tracks(session: Session) -> dict[int, RadioEpisodeTrack]:
    rows = session.exec(select(RadioEpisodeTrack)).all()
    return {row.position: row for row in rows}


def test_import_episode(
    client: TestClient,
    session: Session,
    run: Any,
    ytdlp: FakeYtDlp,
    library: dict[str, Any],
    settings: Settings,
) -> None:
    recordings_before, artists_before = count(session, Recording), count(session, Artist)
    job = start(client)
    run()
    job = get_job(client, job["id"])
    proposal = job["proposal"]

    # Applied, with the one uncertain entry ("Limbo": two equally good recordings) left
    # out for review: a non-blocking issue, the job back in the inbox.
    assert job["state"] == "needs_review", job["log"]
    [issue] = proposal["issues"]
    assert issue["kind"] == "uncertain_recording" and not issue["blocking"]
    assert issue["subject"]["label"] == f"{LIMBO_POSITION}. Limbo — YMO"
    assert {c["id"] for c in issue["candidates"]} == {r["id"] for r in library["limbos"]}
    assert ytdlp.downloads == [NTS_SOUNDCLOUD]

    radio = proposal["radio"]
    assert radio["episode"]["genres"] == ["Folk", "Ambient", "Indie Rock", "Dream Pop"]
    assert radio["episode"]["location"] == "Paris"
    assert len(radio["entries"]) == ENTRIES

    show = session.exec(select(RadioShow)).one()
    assert (show.station, show.slug, show.name) == ("nts", "early-bird-show-w-pam", show.name)
    assert show.name == "The Early Bird Show w/ PAM"
    assert show.image_path and Path(show.image_path).is_file()
    episode = session.exec(select(RadioEpisode)).one()
    assert episode.url == NTS_EPISODE and episode.show_id == show.id
    assert episode.name == "The Early Bird Show w/ PAM & Alice"
    assert episode.broadcast_at and episode.broadcast_at.isoformat() == "2026-11-12T07:00:00+00:00"
    assert episode.image_path and Path(episode.image_path).is_file()

    # Sources: nts (the episode page) -> soundcloud -> file -> a slice per timed entry.
    file = session.get_one(Source, episode.source_id)
    assert file.kind is SourceKind.file and file.managed and file.recording_id is None
    path = Path(file.uri or "")
    assert path == settings.library_dir / "_radio" / "NTS" / "The Early Bird Show w_ PAM" / (
        "2026-11-12 The Early Bird Show w_ PAM & Alice.opus"
    )
    assert path.is_file()
    assert file.parent is not None and file.parent.kind is SourceKind.soundcloud
    assert file.parent.uri == NTS_SOUNDCLOUD
    assert file.parent.parent is not None and file.parent.parent.kind is SourceKind.nts
    assert file.parent.parent.uri == NTS_EPISODE

    tracks = episode_tracks(session)
    assert sorted(tracks) == list(range(1, ENTRIES + 1))
    assert all(t.source_id is not None for t in tracks.values())  # every entry has a time
    first = tracks[1]
    assert (first.artist_text, first.title_text, first.offset_is_estimate) == (
        "Nigel Peake",
        "NORTH SIX PARTS",
        True,
    )
    assert first.source is not None
    assert (first.source.start_ms, first.source.end_ms) == (8000, 430000)
    assert not tracks[2].offset_is_estimate

    # Matched: by ISRC to the album recording, fuzzy to the loose recording.
    evangeline = next(t for t in tracks.values() if t.title_text == "Evangeline")
    assert str(evangeline.recording_id) == library["evangeline"]["id"]
    ruth = tracks[5]
    assert str(ruth.recording_id) == library["ruth"]["id"]
    # Uncertain: no recording yet, but its slice is there (playable from the tracklist).
    limbo = tracks[LIMBO_POSITION]
    assert limbo.recording_id is None and limbo.source is not None
    assert limbo.source.recording_id is None
    # The rest are new standalone recordings, each with its slice.
    assert count(session, Recording) == recordings_before + ENTRIES - 3
    new_artists = count(session, Artist) - artists_before
    # One per new recording: matched recordings keep their credit (no "The Cocteau Twins").
    assert new_artists == ENTRIES - 3
    for track in tracks.values():
        if track.recording_id is not None:
            assert track.source is not None and track.source.recording_id == track.recording_id

    # External IDs: ISRC, Deezer, ACRCloud and the NTS uid on the recordings.
    ids = set(
        session.exec(
            select(ExternalId.scheme, ExternalId.value).where(
                ExternalId.entity_type == EntityType.recording,
                ExternalId.entity_id == evangeline.recording_id,
            )
        ).all()
    )
    assert ("isrc", EVANGELINE_ISRC) in ids
    assert ("deezer", "2286152") in ids
    assert ("nts", "147f7f38-e242-4312-b25c-1d2dd3a5920b") in ids
    peake = session.get_one(Recording, first.recording_id)
    assert peake.mbid is None and peake.length_ms is None
    # A library recording without an MBID, matched by text, gains the entry's MBID (as
    # artists and release groups do); a new one has it.
    assert session.get_one(Recording, ruth.recording_id).mbid == uuid.UUID(
        "15c4306c-0eec-424c-a5c0-a506bec52320"
    )
    ackerman = session.get_one(Recording, tracks[6].recording_id)
    assert ackerman.mbid == uuid.UUID("ba534795-89e5-4da2-af32-fe5b10de3b92")

    # Answering the issue adds the entry; nothing else is created or downloaded again.
    counts = {model: count(session, model) for model in (Recording, Artist, Source, ExternalId)}
    review(
        client,
        job["id"],
        {
            "issue_id": issue["id"],
            "choice": "pick_candidate",
            "candidate_id": library["limbos"][1]["id"],
        },
    )
    run()
    assert get_job(client, job["id"])["state"] == "done"
    assert ytdlp.downloads == [NTS_SOUNDCLOUD]
    session.expire_all()
    limbo = episode_tracks(session)[LIMBO_POSITION]
    assert str(limbo.recording_id) == library["limbos"][1]["id"]
    assert limbo.source is not None and limbo.source.recording_id == limbo.recording_id
    assert count(session, Recording) == counts[Recording]
    assert count(session, Artist) == counts[Artist]
    assert count(session, Source) == counts[Source]

    # Importing the episode again updates it, never duplicates.
    counts[ExternalId] = count(session, ExternalId)
    start(client, NTS_EPISODE)
    run()
    assert count(session, RadioEpisode) == count(session, RadioShow) == 1
    assert count(session, RadioEpisodeTrack) == ENTRIES
    for model, before in counts.items():
        assert count(session, model) == before, model
    assert ytdlp.downloads == [NTS_SOUNDCLOUD]


def test_entry_answer_and_manual_edits_survive_reimport(
    client: TestClient, session: Session, run: Any, library: dict[str, Any]
) -> None:
    start(client)
    run()
    episode = session.exec(select(RadioEpisode)).one()
    tracks = episode_tracks(session)
    # Re-point an entry by hand and adjust its range.
    target = library["limbos"][0]
    entry = tracks[3]
    response = client.patch(
        f"/radio/episode-tracks/{entry.id}",
        json={"recording_id": target["id"], "start_ms": 450000, "end_ms": 800000},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recording"]["id"] == target["id"]
    assert (body["start_ms"], body["end_ms"], body["offset_is_estimate"]) == (450000, 800000, False)
    assert body["in_library"] is False
    assert body["playback"]["start_ms"] == 450000
    # Imported again: the edit stands.
    start(client, NTS_EPISODE)
    run()
    session.expire_all()
    entry = episode_tracks(session)[3]
    assert str(entry.recording_id) == target["id"]
    assert entry.source is not None and (entry.source.start_ms, entry.source.end_ms) == (
        450000,
        800000,
    )
    assert count(session, RadioEpisodeTrack) == ENTRIES
    # Un-matched by hand.
    response = client.patch(f"/radio/episode-tracks/{entry.id}", json={"recording_id": None})
    assert response.status_code == 200
    assert response.json()["recording"] is None
    assert client.patch(f"/radio/episode-tracks/{entry.id}", json={"end_ms": 1}).status_code == 422
    assert entry.episode_id == episode.id


def test_fetch_failures_are_issues(
    client: TestClient, session: Session, run: Any, responses: dict[str, httpx2.Response]
) -> None:
    responses[NTS_EPISODE_API] = httpx2.Response(503, content=b"down")
    job = start(client)
    run()
    job = get_job(client, job["id"])
    assert job["state"] == "needs_review"
    [issue] = job["proposal"]["issues"]
    assert (issue["kind"], issue["choices"], issue["blocking"]) == ("fetch_failed", ["retry"], True)
    responses[NTS_EPISODE_API] = httpx2.Response(200, content=b'{"name": "changed"}')
    review(client, job["id"], {"issue_id": issue["id"], "choice": "retry"})
    run()
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "unexpected_response"
    assert "embeds: Field required" in issue["message"]
    responses[NTS_EPISODE_API] = httpx2.Response(404, content=b"not found")
    client.post(f"/imports/{job['id']}/retry")
    run()
    [issue] = get_job(client, job["id"])["proposal"]["issues"]
    assert issue["kind"] == "metadata_not_found"
    assert count(session, RadioEpisode) == 0


def test_download_failure_is_an_issue(
    client: TestClient, session: Session, run: Any, ytdlp: FakeYtDlp
) -> None:
    ytdlp.failing_downloads.add(NTS_SOUNDCLOUD)
    job = start(client)
    run()
    job = get_job(client, job["id"])
    assert job["state"] == "needs_review"
    [issue] = [i for i in job["proposal"]["issues"] if i["kind"] == "download_failed"]
    assert issue["choices"] == ["retry", "skip"] and issue["blocking"]
    assert count(session, RadioEpisode) == 0
    ytdlp.failing_downloads.clear()
    review(client, job["id"], {"issue_id": issue["id"], "choice": "retry"})
    run()
    assert get_job(client, job["id"])["state"] == "done"
    assert count(session, RadioEpisodeTrack) == ENTRIES


def test_radio_only_filtering_and_playback(
    client: TestClient, session: Session, run: Any, library: dict[str, Any], audio_dir: Path
) -> None:
    start(client)
    run()
    names = {a["name"] for a in client.get("/artists").json()}
    assert {"Cocteau Twins", "Mark Kozelek", "YMO"} <= names
    assert "Nigel Peake" not in names
    everyone = {a["name"] for a in client.get("/artists?include_radio_only=true").json()}
    assert "Nigel Peake" in everyone
    peake = session.exec(select(Artist).where(Artist.name == "Nigel Peake")).one()
    assert client.get(f"/artists/{peake.id}/recordings").json() == []
    [recording] = client.get(f"/artists/{peake.id}/recordings?include_radio_only=true").json()
    assert recording["title"] == "NORTH SIX PARTS"
    kozelek = client.get(f"/artists/{library['kozelek']['id']}/recordings").json()
    assert [r["title"] for r in kozelek] == ["Ruth Marie"]
    # Search hides radio-only artists and recordings by default.
    assert not [h for h in client.get("/search?q=nigel peake").json() if h["type"] == "artist"]
    hits = client.get("/search?q=nigel peake&include_radio_only=true").json()
    assert any(h["type"] == "artist" and h["title"] == "Nigel Peake" for h in hits)
    hits = client.get("/search?q=north six parts").json()
    assert not [h for h in hits if h["type"] == "recording"]
    assert any(
        h["type"] == "recording"
        for h in client.get("/search?q=north six parts&include_radio_only=true").json()
    )

    # A radio-only recording that gets a proper file is no longer radio-only, and plays
    # the file, not the radio slice (older as it is).
    peake_id = session.exec(select(Recording.id).where(Recording.title == "NORTH SIX PARTS")).one()
    [entry] = [t for t in episode_tracks(session).values() if t.recording_id == peake_id]
    file = post(
        client,
        "/sources",
        {"kind": "file", "uri": str(audio_dir / "song.m4a"), "recording_id": str(peake_id)},
    )
    playback = recording_playbacks(session, [peake_id])[peake_id]
    assert str(playback.source_id) == file["id"] and playback.source_id != entry.source_id
    names = {a["name"] for a in client.get("/artists").json()}
    assert "Nigel Peake" in names
    # Evangeline: the album track plays the album file.
    rg_tracks = client.get(f"/release-groups/{_release_group_of(client, library)}/tracks").json()
    assert rg_tracks[0]["playability"]["playback"]["source_id"] == library["album_file"]["id"]


def _release_group_of(client: TestClient, library: dict[str, Any]) -> str:
    detail = client.get(f"/recordings/{library['evangeline']['id']}").json()
    return detail["tracks"][0]["release_group"]["id"]


def test_radio_endpoints(
    client: TestClient, session: Session, run: Any, library: dict[str, Any], settings: Settings
) -> None:
    start(client)
    run()
    [show] = client.get("/radio/shows").json()
    assert show["name"] == "The Early Bird Show w/ PAM"
    assert show["image_url"] == f"/covers/radio-show/{show['id']}"
    assert client.get(show["image_url"]).headers["content-type"] == "image/png"
    assert client.get(f"/radio/shows/{show['id']}").json() == {**show, "episode_count": 1}
    [episode] = client.get(f"/radio/shows/{show['id']}/episodes").json()
    assert episode["url"] == NTS_EPISODE and episode["location"] == "Paris"
    assert client.get(episode["image_url"]).status_code == 200

    detail = client.get(f"/radio/episodes/{episode['id']}").json()
    assert detail["show"]["id"] == show["id"]
    assert detail["playback"]["file_source_id"] == episode["source_id"]
    assert detail["playback"]["start_ms"] == 0
    tracks = detail["tracks"]
    assert [t["position"] for t in tracks] == list(range(1, ENTRIES + 1))
    first = tracks[0]
    assert (first["start_ms"], first["end_ms"], first["offset_is_estimate"]) == (8000, 430000, True)
    assert first["playback"]["file_source_id"] == episode["source_id"]
    assert (first["playback"]["start_ms"], first["playback"]["end_ms"]) == (8000, 430000)
    assert first["in_library"] is False and first["library_playback"] is None
    assert first["recording"]["artist_credit"]["name"] == "Nigel Peake"
    ruth = tracks[4]
    assert ruth["recording"]["id"] == library["ruth"]["id"]
    assert ruth["in_library"] is True and ruth["library_playback"]["file_source_id"]
    assert tracks[LIMBO_POSITION - 1]["recording"] is None
    # The slice streams the episode file.
    stream = client.get(f"/sources/{first['source_id']}/stream", headers={"Range": "bytes=0-9"})
    assert stream.status_code == 206

    # Appearances.
    [appearance] = client.get(f"/recordings/{library['ruth']['id']}/radio-appearances").json()
    assert appearance["episode"]["id"] == episode["id"]
    assert appearance["show"]["id"] == show["id"]
    assert (appearance["position"], appearance["start_ms"]) == (5, 1044000)
    by_artist = client.get(f"/artists/{library['kozelek']['id']}/radio-appearances").json()
    assert [a["track_id"] for a in by_artist] == [appearance["track_id"]]
    assert client.get(f"/recordings/{uuid.uuid4()}/radio-appearances").status_code == 404

    # Search and resolve.
    hits = client.get("/search?q=early bird&types=radio_show&types=radio_episode").json()
    assert {(h["type"], h["id"]) for h in hits} == {
        ("radio_show", show["id"]),
        ("radio_episode", episode["id"]),
    }
    episode_hit = next(h for h in hits if h["type"] == "radio_episode")
    assert episode_hit["subtitle"] == "The Early Bird Show w/ PAM, 2026-11-12"
    assert episode_hit["cover_url"] == episode["image_url"] and episode_hit["mbid"] is None
    for type, id in ("radio_show", show["id"]), ("radio_episode", episode["id"]):
        assert client.get(f"/resolve/{type}/{id}").json() == {"type": type, "id": id}

    # Edits.
    response = client.patch(f"/radio/shows/{show['id']}", json={"description": "Dawn chorus"})
    assert response.json()["description"] == "Dawn chorus"
    response = client.patch(
        f"/radio/episodes/{episode['id']}", json={"location": None, "name": "PAM & Alice"}
    )
    assert (response.json()["location"], response.json()["name"]) == (None, "PAM & Alice")

    # Deleting the episode removes what only it knew; library recordings stay.
    recordings, artists = count(session, Recording), count(session, Artist)
    audio = Path(session.get_one(Source, uuid.UUID(episode["source_id"])).uri or "")
    response = client.delete(f"/radio/episodes/{episode['id']}?delete_audio=true")
    assert response.status_code == 204, response.text
    session.expire_all()
    assert count(session, RadioEpisodeTrack) == 0
    assert count(session, Recording) == recordings - (ENTRIES - 3)
    assert count(session, Artist) < artists
    for kept in (library["evangeline"], library["ruth"], *library["limbos"]):
        assert session.get(Recording, uuid.UUID(kept["id"])) is not None
    assert session.exec(select(Artist).where(Artist.name == "Nigel Peake")).first() is None
    kinds = set(session.exec(select(Source.kind)).all())
    assert SourceKind.slice not in kinds and SourceKind.nts not in kinds
    assert SourceKind.soundcloud not in kinds
    assert not audio.exists()
    assert list((settings.library_dir / "_covers" / "radio-episode").iterdir()) == []
    assert client.delete(f"/radio/shows/{show['id']}").status_code == 204
    assert count(session, RadioShow) == 0


def test_radio_list_query_counts(
    client: TestClient, session: Session, run: Any, library: dict[str, Any]
) -> None:
    start(client)
    run()
    show = session.exec(select(RadioShow)).one()
    episode = session.exec(select(RadioEpisode)).one()
    for path, expected in [
        ("/radio/shows", 1),
        (f"/radio/shows/{show.id}/episodes", 2),
        # episode + show, tracks + recordings + credits, credit names, sources + ancestors
        (f"/radio/episodes/{episode.id}", 4),
        # recording, entries + episodes + shows + recording credits + slices, names,
        # slices + ancestors
        (f"/recordings/{library['ruth']['id']}/radio-appearances", 4),
        (f"/artists/{library['kozelek']['id']}/radio-appearances", 4),
        ("/artists", 1),
        # artist, recordings + credits, names, sources + ancestors
        (f"/artists/{library['kozelek']['id']}/recordings", 4),
    ]:
        with count_selects(session) as statements:
            response = client.get(path)
        assert response.status_code == 200, response.text
        assert len(statements) == expected, (path, "\n\n".join(statements))


def test_episode_with_untimed_entry(
    client: TestClient, session: Session, run: Any, responses: dict[str, httpx2.Response]
) -> None:
    """An entry without any time is in the tracklist, with its recording but no slice."""
    data = json.loads((NTS / "episode_early-bird-show-w-pam-12th-november-2026.json").read_bytes())
    entry = data["embeds"]["tracklist"]["results"][0]
    entry["offset_estimate"] = entry["duration_estimate"] = None
    responses[NTS_EPISODE_API] = httpx2.Response(200, content=json.dumps(data).encode())
    start(client)
    run()
    first = episode_tracks(session)[1]
    assert first.source_id is None and first.recording_id is not None


def _episode_data() -> dict[str, Any]:
    return json.loads((NTS / "episode_early-bird-show-w-pam-12th-november-2026.json").read_bytes())


def _serve(responses: dict[str, httpx2.Response], data: dict[str, Any]) -> None:
    tracklist = data["embeds"]["tracklist"]
    tracklist["metadata"]["resultset"]["count"] = len(tracklist["results"])
    responses[NTS_EPISODE_API] = httpx2.Response(200, content=json.dumps(data).encode())


def test_a_track_played_twice_is_one_recording(
    client: TestClient,
    session: Session,
    run: Any,
    library: dict[str, Any],
    responses: dict[str, httpx2.Response],
) -> None:
    """An episode that plays a track twice: both entries are the same recording, whether
    it is new (by MBID, by text) or in the library (by ISRC)."""
    data = _episode_data()
    entries = data["embeds"]["tracklist"]["results"]
    # Ackerman (new, MBID), Evangeline (library, ISRC), Nigel Peake (new, text only).
    for offset, index in zip((7300, 7500, 7700), (5, 7, 0), strict=True):
        repeat: dict[str, Any] = {**entries[index], "uid": str(uuid.uuid4())}
        repeat |= {"offset": offset, "duration": 150}
        repeat |= {"offset_estimate": None, "duration_estimate": None}
        entries.append(repeat)
    _serve(responses, data)
    recordings_before = count(session, Recording)
    job = start(client)
    run()
    assert get_job(client, job["id"])["state"] == "needs_review"  # only Limbo is open
    assert count(session, Recording) == recordings_before + ENTRIES - 3
    tracks = episode_tracks(session)
    for first, again in (6, ENTRIES + 1), (8, ENTRIES + 2), (1, ENTRIES + 3):
        assert tracks[first].recording_id is not None
        assert tracks[again].recording_id == tracks[first].recording_id
        assert tracks[again].source_id not in (None, tracks[first].source_id)


def test_reimport_drops_what_the_station_removed(
    client: TestClient,
    session: Session,
    run: Any,
    library: dict[str, Any],
    responses: dict[str, httpx2.Response],
) -> None:
    """NTS removes the first entry: its row, slice and radio-only recording go; the
    other entries (now one position up) keep their recordings and manual edits."""
    start(client)
    run()
    tracks = episode_tracks(session)
    peake = tracks[1]
    peake_recording, peake_slice = peake.recording_id, peake.source_id
    assert peake_recording is not None and peake_slice is not None
    edited = tracks[3]
    target = library["limbos"][0]
    response = client.patch(
        f"/radio/episode-tracks/{edited.id}", json={"recording_id": target["id"]}
    )
    assert response.status_code == 200, response.text
    sources = count(session, Source)

    data = _episode_data()
    del data["embeds"]["tracklist"]["results"][0]
    _serve(responses, data)
    start(client, NTS_EPISODE)
    run()
    session.expire_all()
    tracks = episode_tracks(session)
    assert sorted(tracks) == list(range(1, ENTRIES))
    assert str(tracks[2].recording_id) == target["id"]  # the edit, one position up
    assert session.get(Recording, peake_recording) is None
    assert session.get(Source, peake_slice) is None
    assert count(session, Source) == sources - 1
    assert session.exec(select(Artist).where(Artist.name == "Nigel Peake")).first() is None


def test_repointing_an_entry_deletes_the_recording_radio_made(
    client: TestClient, session: Session, run: Any, library: dict[str, Any]
) -> None:
    start(client)
    run()
    entry = episode_tracks(session)[1]
    made = entry.recording_id
    assert made is not None
    response = client.patch(
        f"/radio/episode-tracks/{entry.id}", json={"recording_id": library["ruth"]["id"]}
    )
    assert response.status_code == 200, response.text
    session.expire_all()
    assert session.get(Recording, made) is None
    assert session.exec(select(Artist).where(Artist.name == "Nigel Peake")).first() is None
    # A library recording is never deleted that way.
    response = client.patch(f"/radio/episode-tracks/{entry.id}", json={"recording_id": None})
    assert response.status_code == 200, response.text
    assert session.get(Recording, uuid.UUID(library["ruth"]["id"])) is not None


def test_episode_detail_has_the_file_length(
    client: TestClient, session: Session, run: Any, library: dict[str, Any]
) -> None:
    start(client)
    run()
    episode = session.exec(select(RadioEpisode)).one()
    file = session.get_one(Source, episode.source_id)
    detail = client.get(f"/radio/episodes/{episode.id}").json()
    assert detail["duration_ms"] == file.duration_ms is not None


def test_delete_audio_only_deletes_library_files(tmp_path: Path) -> None:
    library = tmp_path / "library"
    watched = tmp_path / "watch"
    (library / "_radio").mkdir(parents=True)
    watched.mkdir()
    inside = library / "_radio" / "episode.opus"
    outside = watched / "episode.opus"
    for path in inside, outside:
        path.write_bytes(b"audio")
    (library / "link.opus").symlink_to(outside)
    (library / "watch").symlink_to(watched, target_is_directory=True)

    def deletable(uri: Path | str, managed: bool = True, protected: tuple[Path, ...] = (watched,)):
        source = Source(kind=SourceKind.file, uri=str(uri), managed=managed)
        return _deletable_audio(source, library, protected)

    assert deletable(inside) == inside.resolve()
    assert deletable(inside, managed=False) is None
    assert deletable(library / ".." / "watch" / "episode.opus") is None
    assert deletable(outside) is None
    assert deletable(library / "link.opus") is None
    assert deletable(library / "watch" / "episode.opus") is None
    assert deletable(library / "_radio" / "missing.opus") is None
    # A watched directory inside the library.
    assert deletable(inside, protected=(library / "_radio",)) is None
    assert inside.exists() and outside.exists()


def test_radio_only_combinations(
    client: TestClient, session: Session, run: Any, library: dict[str, Any], audio_dir: Path
) -> None:
    start(client)
    run()
    # Featured only on an album track's recording: visible.
    guest = make_artist(client, "Guest Singer")
    duet = post(
        client,
        "/recordings",
        {"title": "Duet", "artist_credit": credit(library["cocteau"], guest, join=" feat. ")},
    )
    album = make_release_group(client, library["cocteau"], "Duets")
    make_track(client, album, duet, 1)
    # Hand-made, no sources, in no tracklist: visible.
    lonely = make_artist(client, "Lonely")
    make_recording(client, lonely, "Alone")
    # Mixed: a library recording and a radio-only one (re-pointed entry's recording).
    peake = session.exec(select(Artist).where(Artist.name == "Nigel Peake")).one()
    made = make_recording(client, {"id": str(peake.id)}, "Library Song")
    post(
        client,
        "/sources",
        {"kind": "file", "uri": str(audio_dir / "song.m4a"), "recording_id": made["id"]},
    )
    names = {a["name"] for a in client.get("/artists?limit=200").json()}
    assert {"Guest Singer", "Lonely", "Nigel Peake", "Cocteau Twins"} <= names
    assert "Malibu" not in names  # only radio slices
    titles = [r["title"] for r in client.get(f"/artists/{peake.id}/recordings").json()]
    assert titles == ["Library Song"]
    titles = {r["title"] for r in client.get(f"/artists/{guest['id']}/recordings").json()}
    assert titles == {"Duet"}
    # A recording on an album and played on the radio: visible; plays the album file.
    hits = client.get("/search?q=evangeline&types=recording").json()
    assert [h["id"] for h in hits] == [library["evangeline"]["id"]]
    evangeline = uuid.UUID(library["evangeline"]["id"])
    playback = recording_playbacks(session, [evangeline])[evangeline]
    assert str(playback.source_id) == library["album_file"]["id"]
    # A loose recording with a YouTube file and a radio slice: visible.
    kozelek = client.get(f"/artists/{library['kozelek']['id']}/recordings").json()
    assert [r["title"] for r in kozelek] == ["Ruth Marie"]


# Tags and notes


def entity_tags(client: TestClient, type: str, id: object) -> list[str]:
    response = client.get(f"/entity-tags/{type}/{id}")
    assert response.status_code == 200, response.text
    return [tag["name"] for tag in response.json()]


def test_genres_and_moods_become_tags_of_a_new_episode_only(
    client: TestClient,
    session: Session,
    run: Any,
    library: dict[str, Any],
    responses: dict[str, httpx2.Response],
) -> None:
    data = _episode_data()
    data["moods"] = [
        {"id": "moods-sunny", "value": "Sunny"},
        {"id": "genres-folk", "value": "folk"},
    ]
    _serve(responses, data)
    start(client)
    run()
    episode = session.exec(select(RadioEpisode)).one()
    # Ignoring case, "folk" is "Folk".
    assert entity_tags(client, "radio_episode", episode.id) == [
        "Ambient",
        "Dream Pop",
        "Folk",
        "Indie Rock",
        "Sunny",
    ]
    # The user removes one and adds one; importing again does not bring it back.
    [ambient] = [t for t in client.get("/tags").json() if t["name"] == "Ambient"]
    assert (
        client.delete(f"/entity-tags/radio_episode/{episode.id}/{ambient['id']}").status_code == 204
    )
    client.post(f"/entity-tags/radio_episode/{episode.id}", json={"name": "Mine"})
    note = client.put(
        f"/notes/radio_episode/{episode.id}", json={"body": "Lovely.", "base_updated_at": None}
    )
    assert note.status_code == 200
    data["genres"].append({"id": "genres-new", "value": "New Genre"})
    _serve(responses, data)
    start(client, NTS_EPISODE)
    run()
    session.expire_all()
    assert entity_tags(client, "radio_episode", episode.id) == [
        "Dream Pop",
        "Folk",
        "Indie Rock",
        "Mine",
        "Sunny",
    ]
    assert client.get(f"/notes/radio_episode/{episode.id}").json()["body"] == "Lovely."


def test_annotated_radio_recordings_are_kept(
    client: TestClient,
    session: Session,
    run: Any,
    library: dict[str, Any],
    responses: dict[str, httpx2.Response],
) -> None:
    """Deleting an episode, re-importing it or re-pointing an entry deletes the
    recordings only radio knew, but never one with a note, a tag or a link to it (the
    user's writing is never deleted as a side effect); the episode's own note goes."""
    start(client)
    run()
    tracks = episode_tracks(session)
    radio_only = radio_only_ids(
        session, (t.recording_id for t in tracks.values() if t.recording_id)
    )
    positions = [p for p in sorted(tracks) if tracks[p].recording_id in radio_only][:5]
    assert positions[0] == 1
    made = {p: session.get_one(Recording, tracks[p].recording_id) for p in positions}
    noted, tagged, linked, repointed, plain = made.values()
    noted_artist = noted.artist_credit.names[0].artist
    put = {"base_updated_at": None}
    assert client.put(f"/notes/recording/{noted.id}", json={**put, "body": "!"}).status_code == 200
    assert client.post(f"/entity-tags/recording/{tagged.id}", json={"name": "x"}).status_code == 200
    author = library["kozelek"]["id"]
    episode = session.exec(select(RadioEpisode)).one()
    body = f"[[recording:{linked.id}|this]] on [[radio_episode:{episode.id}|the show]]"
    assert client.put(f"/notes/artist/{author}", json={**put, "body": body}).status_code == 200
    put_note = client.put(f"/notes/recording/{repointed.id}", json={**put, "body": "keep"})
    assert put_note.status_code == 200
    episode_note = client.put(f"/notes/radio_episode/{episode.id}", json={**put, "body": "ep"})
    assert episode_note.status_code == 200

    # Re-pointing the entry away from an annotated recording keeps it.
    response = client.patch(
        f"/radio/episode-tracks/{tracks[positions[3]].id}",
        json={"recording_id": library["ruth"]["id"]},
    )
    assert response.status_code == 200, response.text
    session.expire_all()
    assert session.get(Recording, repointed.id) is not None

    # NTS drops the first entry from the tracklist: its recording has a note, it stays.
    data = _episode_data()
    del data["embeds"]["tracklist"]["results"][0]
    _serve(responses, data)
    start(client, NTS_EPISODE)
    run()
    session.expire_all()
    assert session.get(Recording, noted.id) is not None

    kept = [recording.id for recording in (noted, tagged, linked, repointed)]
    plain_id = plain.id
    assert client.delete(f"/radio/episodes/{episode.id}").status_code == 204
    session.expire_all()
    assert all(session.get(Recording, id) is not None for id in kept)
    assert session.get(Recording, plain_id) is None
    assert session.get(Artist, noted_artist.id) is not None
    # Kept recordings are in no tracklist: no longer hidden as radio-only.
    assert radio_only_ids(session, kept) == set()
    # The episode's note went; the link to it is broken, the text stays.
    assert client.get(f"/notes/recording/{kept[0]}").json()["body"] == "!"
    note = client.get(f"/notes/artist/{author}").json()
    assert note["body"] == body
    assert [link["target"] is None for link in note["links"]] == [False, True]
    assert session.exec(select(Note).where(Note.entity_type == "radio_episode")).all() == []


def test_annotated_artist_of_a_deleted_radio_recording_is_kept(
    client: TestClient, session: Session, run: Any, library: dict[str, Any]
) -> None:
    start(client)
    run()
    entry = episode_tracks(session)[1]
    recording = session.get_one(Recording, entry.recording_id)
    artist = recording.artist_credit.names[0].artist
    response = client.post(f"/entity-tags/artist/{artist.id}", json={"name": "favourite"})
    assert response.status_code == 200
    response = client.patch(
        f"/radio/episode-tracks/{entry.id}", json={"recording_id": library["ruth"]["id"]}
    )
    assert response.status_code == 200
    session.expire_all()
    assert session.get(Recording, recording.id) is None
    assert session.get(Artist, artist.id) is not None
    assert entity_tags(client, "artist", artist.id) == ["favourite"]


def test_deleting_a_show_deletes_its_and_its_episodes_notes_and_tags(
    client: TestClient, session: Session, run: Any, library: dict[str, Any]
) -> None:
    start(client)
    run()
    show = session.exec(select(RadioShow)).one()
    episode = session.exec(select(RadioEpisode)).one()
    for type, id in ("radio_show", show.id), ("radio_episode", episode.id):
        response = client.put(f"/notes/{type}/{id}", json={"body": "n", "base_updated_at": None})
        assert response.status_code == 200
    assert entity_tags(client, "radio_episode", episode.id)  # imported genres
    assert client.delete(f"/radio/shows/{show.id}").status_code == 204
    assert session.exec(select(Note)).all() == []
    assert session.exec(select(EntityTag)).all() == []
