"""What the app shows that the server knows: play states, counts, details, search
groups, import progress and cancelling, host paths, server info."""

import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ep.importers.framework import JobCancelled, JobContext
from ep.importers.proposal import (
    CandidateType,
    Choice,
    IncomingArtist,
    IncomingCreditPart,
    IncomingMedium,
    IncomingRelease,
    IncomingTrack,
    Issue,
    IssueCandidate,
    IssueKind,
    Proposal,
    Subject,
    SubjectType,
)
from ep.jobs.queue import create_import
from ep.metadata.musicbrainz import MusicBrainz
from ep.models import (
    ImportJob,
    ImportKind,
    JobStage,
    JobState,
    RadioEpisode,
    RadioEpisodeTrack,
    RadioShow,
    RadioStation,
    Source,
    SourceKind,
)
from ep.settings import Settings
from tests.helpers import make_artist, make_recording, make_release_group, make_track, post

TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


def source(client: TestClient, kind: str, **fields: Any) -> dict[str, Any]:
    return post(client, "/sources", {"kind": kind, **fields})


def radio_entry(
    session: Session, episode_file: dict[str, Any], recording_id: str | None, start: int
) -> RadioEpisodeTrack:
    show = session.exec(select(RadioShow)).first() or RadioShow(
        station=RadioStation.nts, slug="late-night", name="Late Night Tapes"
    )
    episode = RadioEpisode(
        show=show,
        show_id=show.id,
        slug=f"ep-{start}",
        name=f"Episode {start}",
        url=f"https://www.nts.live/shows/late-night/episodes/ep-{start}",
        broadcast_at=datetime(2025, 3, 14, tzinfo=UTC),
        source_id=uuid.UUID(episode_file["id"]),
    )
    slice_ = Source(
        kind=SourceKind.slice,
        parent_id=uuid.UUID(episode_file["id"]),
        start_ms=start,
        end_ms=start + 1000,
        recording_id=uuid.UUID(recording_id) if recording_id else None,
    )
    entry = RadioEpisodeTrack(
        episode=episode,
        episode_id=episode.id,
        position=1,
        artist_text="Nina Simone",
        title_text="Sinnerman",
        recording_id=slice_.recording_id,
        source=slice_,
        offset_is_estimate=True,
    )
    session.add_all([show, episode, slice_, entry])
    session.commit()
    return entry


def test_play_states(
    client: TestClient, session: Session, audio_dir: Path, tmp_path: Path, settings: Settings
) -> None:
    nina = make_artist(client)
    album = make_release_group(client, nina)
    own, ranged, excerpt, missing, link_only, nothing = (
        make_recording(client, nina, title, length_ms=1000)
        for title in ("Own", "Ranged", "Excerpt", "Missing", "Link", "Nothing")
    )
    for position, recording in enumerate([own, ranged, excerpt, missing, link_only, nothing], 1):
        make_track(client, album, recording, position)
    source(client, "file", uri=str(audio_dir / "song.mp3"), recording_id=own["id"])
    album_file = source(client, "file", uri=str(audio_dir / "album.flac"))
    source(client, "slice", parent_id=album_file["id"], start_ms=1000, recording_id=ranged["id"])
    episode_file = source(client, "file", uri=str(audio_dir / "song.m4a"))
    entry = radio_entry(session, episode_file, excerpt["id"], 0)
    gone = settings.media_roots[2] / "gone.mp3"
    shutil.copyfile(audio_dir / "song.mp3", gone)
    lost = source(client, "file", uri=str(gone), recording_id=missing["id"])
    gone.unlink()
    source(client, "spotify", uri="https://open.spotify.com/track/x", recording_id=link_only["id"])

    tracks = client.get(f"/release-groups/{album['id']}/tracks").json()
    states = [t["playability"]["state"] for t in tracks]
    assert states == ["file", "slice", "radio_excerpt", "file_missing", "link_only", "no_source"]
    by_title = {t["recording"]["title"]: t["playability"] for t in tracks}
    assert by_title["Excerpt"]["radio_entry"] == {
        "track_id": str(entry.id),
        "episode_id": str(entry.episode_id),
    }
    assert by_title["Missing"]["playback"] is None
    assert by_title["Missing"]["missing_file_source_id"] == lost["id"]
    assert by_title["Link"]["link"]["kind"] == "spotify"
    assert by_title["Ranged"]["playback"]["file_duration_ms"] == album_file["duration_ms"]

    detail = client.get(f"/release-groups/{album['id']}").json()
    assert (detail["track_count"], detail["disc_count"], detail["length_ms"]) == (6, 1, 6000)
    assert detail["play_states"] == dict.fromkeys(states, 1)
    assert detail["link_kinds"] == ["spotify"]

    # Locate…: a new path for the missing file (re-encoded meanwhile); it plays again.
    found = settings.media_roots[2] / "found.opus"
    shutil.copyfile(audio_dir / "song.opus", found)
    assert client.patch(f"/sources/{lost['id']}", json={"uri": str(found)}).status_code == 200
    playability = client.get(f"/recordings/{missing['id']}").json()["playability"]
    assert playability["state"] == "file"
    assert (playability["playback"]["media_type"], playability["playback"]["codec"]) == (
        "audio/ogg",
        "opus",
    )

    # The radio entry as the sources list shows it.
    [excerpt_source] = client.get(f"/recordings/{excerpt['id']}/sources").json()
    assert excerpt_source["role"] == "radio_excerpt"
    assert excerpt_source["radio"]["show"]["name"] == "Late Night Tapes"
    assert excerpt_source["radio"]["offset_is_estimate"] is True
    assert excerpt_source["plays"] is True

    # Known only from an episode whose audio went missing: file missing, not no source.
    gone_episode = settings.media_roots[2] / "episode.m4a"
    shutil.copyfile(audio_dir / "song.m4a", gone_episode)
    lost_episode = source(client, "file", uri=str(gone_episode))
    radio_entry(session, lost_episode, nothing["id"], 5000)
    gone_episode.unlink()
    playability = client.get(f"/recordings/{nothing['id']}").json()["playability"]
    assert (playability["state"], playability["missing_file_source_id"]) == (
        "file_missing",
        lost_episode["id"],
    )


def test_preferred_source(client: TestClient, audio_dir: Path) -> None:
    recording = make_recording(client, make_artist(client))
    first = source(client, "file", uri=str(audio_dir / "song.mp3"), recording_id=recording["id"])
    second = source(client, "file", uri=str(audio_dir / "song.m4a"), recording_id=recording["id"])
    link = source(client, "youtube", uri="https://youtu.be/x", recording_id=recording["id"])
    url = f"/recordings/{recording['id']}"
    assert client.get(url).json()["playability"]["playback"]["source_id"] == first["id"]

    response = client.patch(url, json={"preferred_source_id": second["id"]})
    assert response.status_code == 200
    assert response.json()["preferred_source_id"] == second["id"]
    assert client.get(url).json()["playability"]["playback"]["source_id"] == second["id"]
    sources = client.get(f"{url}/sources").json()
    assert [(s["preferred"], s["plays"]) for s in sources] == [
        (False, False),
        (True, True),
        (False, False),
    ]
    # Links do not play; null goes back to the default.
    assert client.patch(url, json={"preferred_source_id": link["id"]}).status_code == 422
    assert (
        client.patch(url, json={"preferred_source_id": None}).json()["preferred_source_id"] is None
    )
    assert client.get(url).json()["playability"]["playback"]["source_id"] == first["id"]
    # Deleting the preferred source clears the choice.
    client.patch(url, json={"preferred_source_id": second["id"]})
    assert client.delete(f"/sources/{second['id']}").status_code == 204
    assert client.get(url).json()["preferred_source_id"] is None


def test_artist_detail_filter_and_server_counts(
    client: TestClient, session: Session, audio_dir: Path
) -> None:
    bjork = make_artist(client, "Björk")
    make_artist(client, "Brant Bjork")
    make_artist(client, "Nina Simone")
    make_release_group(client, bjork, "Homogenic")
    make_recording(client, bjork, "Loose One")
    radio = make_artist(client, "Laurel Halo")
    heard = make_recording(client, radio, "Heard")
    entry = radio_entry(
        session, source(client, "file", uri=str(audio_dir / "song.m4a")), heard["id"], 0
    )
    detail = client.get(f"/artists/{bjork['id']}").json()
    assert detail["counts"] == {"release_groups": 1, "loose_recordings": 1, "radio_episodes": 0}
    radio_detail = client.get(f"/artists/{radio['id']}").json()
    assert radio_detail["radio_only"] is True
    assert radio_detail["counts"]["radio_episodes"] == 1
    assert radio_detail["counts"]["loose_recordings"] == 0  # radio-only ones are hidden

    names = [a["name"] for a in client.get("/artists", params={"q": "BJORK"}).json()]
    assert names == ["Björk", "Brant Bjork"]
    assert client.get("/artists", params={"q": "laurel"}).json() == []
    assert len(client.get("/artists", params={"q": "laurel", "include_radio_only": True}).json())

    server = client.get("/server").json()
    assert server["version"]
    assert server["counts"] == {
        "artists": 3,
        "radio_only_artists": 1,
        "release_groups": 1,
        "recordings": 2,
        "tracks": 0,
        "radio_shows": 1,
        "radio_episodes": 1,
    }
    assert server["library_dir"]["host_path"] == server["library_dir"]["path"]
    # The release group's radio appearances (none) and the recording's, with playback.
    [appearance] = client.get(f"/recordings/{heard['id']}/radio-appearances").json()
    assert appearance["playback"]["source_id"] == str(entry.source_id)


def test_host_paths(client: TestClient, settings: Settings, audio_dir: Path) -> None:
    host = Path("/Users/someone/Music/audio")
    settings.host_paths = {audio_dir: host}
    file = source(client, "file", uri=str(host / "song.mp3"))  # a host path is mapped
    assert file["uri"] == str(audio_dir / "song.mp3")
    assert file["host_path"] == str(host / "song.mp3")
    server = client.get("/server").json()
    assert {"path": str(audio_dir), "host_path": str(host)} in server["media_roots"]
    assert server["library_dir"]["host_path"] is None  # not mapped


def test_watch_dir_status(client: TestClient, session: Session, settings: Settings) -> None:
    music = settings.media_roots[2]
    watch = client.post("/watch-dirs", json={"path": str(music)}).json()
    assert (watch["status"], watch["last_import_at"], watch["host_path"]) == (
        "idle",
        None,
        str(music),
    )
    (music / "Album").mkdir()
    job = create_import(session, ImportKind.folder, str(music / "Album"))
    job.watch_dir_id = uuid.UUID(watch["id"])
    session.commit()
    [listed] = client.get("/watch-dirs").json()
    assert listed["status"] == "importing"
    assert TIMESTAMP.match(listed["last_import_at"])
    paused = client.patch(f"/watch-dirs/{watch['id']}", json={"enabled": False}).json()
    assert paused["status"] == "paused"
    [summary] = client.get("/imports").json()
    assert summary["watch_dir_id"] == watch["id"]
    assert summary["input_host_path"] == str(music / "Album")


def test_track_order_and_new_tracks(client: TestClient) -> None:
    bjork = make_artist(client, "Björk")
    album = make_release_group(client, bjork, "Medúlla")
    first = post(
        client,
        "/tracks",
        {"release_group_id": album["id"], "new_recording": {"title": "Pleasure Is All Mine"}},
    )
    assert first["position"] == 1
    assert first["recording"]["artist_credit"]["name"] == "Björk"  # the album's credit
    second = post(
        client,
        "/tracks",
        {
            "release_group_id": album["id"],
            "new_recording": {
                "title": "Where Is the Line",
                "artist_credit": [
                    {"artist_id": bjork["id"], "join_phrase": " feat. "},
                    {"new_artist": {"name": "Robert Wyatt"}},
                ],
            },
        },
    )
    assert second["position"] == 2
    assert second["recording"]["artist_credit"]["name"] == "Björk feat. Robert Wyatt"
    both = {"release_group_id": album["id"], "recording_id": first["recording"]["id"]}
    response = client.post("/tracks", json={**both, "new_recording": {"title": "x"}})
    assert response.status_code == 422

    url = f"/release-groups/{album['id']}/track-order"
    body = {
        "tracks": [
            {"id": second["id"], "disc": 1, "position": 1},
            {"id": first["id"], "disc": 2, "position": 1},
        ]
    }
    reordered = client.put(url, json=body).json()
    assert [(t["id"], t["disc"], t["position"]) for t in reordered] == [
        (second["id"], 1, 1),
        (first["id"], 2, 1),
    ]
    assert client.put(url, json={"tracks": body["tracks"][:1]}).status_code == 422
    same = [{**place, "disc": 1, "position": 1} for place in body["tracks"]]
    assert client.put(url, json={"tracks": same}).status_code == 422


def test_search_groups_counts_radio_and_tags(
    client: TestClient, session: Session, audio_dir: Path
) -> None:
    bjork = make_artist(client, "Björk")
    vespertine = make_release_group(client, bjork, "Vespertine")
    cocoon = make_recording(client, bjork, "Cocoon", length_ms=268_000)
    make_track(client, vespertine, cocoon)
    make_recording(client, bjork, "Loose")
    entry = radio_entry(
        session, source(client, "file", uri=str(audio_dir / "song.m4a")), cocoon["id"], 2000
    )
    hits = client.get("/search", params={"q": "bjork vesp"}).json()
    [album] = [h for h in hits if h["type"] == "release_group"]
    assert (album["track_count"], album["release_group_type"]) == (1, "album")
    [episode] = [h for h in hits if h["type"] == "radio_episode"]
    assert episode["radio_match"]["track_id"] == str(entry.id)
    assert episode["radio_match"]["recording"]["title"] == "Cocoon"
    assert episode["radio_match"]["start_ms"] == 2000
    [artist] = client.get("/search", params={"q": "bjork", "types": "artist"}).json()
    assert (artist["release_group_count"], artist["loose_recording_count"]) == (1, 1)
    [track] = client.get("/search", params={"q": "cocoon", "types": "recording"}).json()
    assert (track["release_group_title"], track["length_ms"]) == ("Vespertine", 268_000)

    # per_type caps each type; tag_id keeps only tagged entities.
    capped = client.get("/search", params={"q": "bjork", "per_type": 1}).json()
    assert max(sum(h["type"] == t for h in capped) for t in {h["type"] for h in capped}) == 1
    [tag] = client.post(f"/entity-tags/recording/{cocoon['id']}", json={"name": "strings"}).json()
    tagged = client.get("/search", params={"q": "bjork cocoon", "tag_id": tag["id"]}).json()
    assert [h["title"] for h in tagged] == ["Cocoon"]


def test_link_classification(client: TestClient) -> None:
    info = client.get(
        "/links/classify", params={"url": "https://fennesz.bandcamp.com/album/agora?from=x"}
    ).json()
    assert info == {
        "link_kind": "bandcamp_album",
        "source_kind": "bandcamp",
        "import_kind": "bandcamp",
        "url": "https://fennesz.bandcamp.com/album/agora",
    }
    spotify = client.get(
        "/links/classify", params={"url": "https://open.spotify.com/track/abc?si=1"}
    ).json()
    assert (spotify["link_kind"], spotify["import_kind"]) == ("spotify", None)
    unknown = client.get("/links/classify", params={"url": "https://example.com"}).json()
    assert set(unknown.values()) == {None}


def proposal_with_issue(audio_dir: Path) -> Proposal:
    credit_ = [IncomingCreditPart(artist_key="nina", credited_name="Nina Simone")]
    return Proposal(
        media=[IncomingMedium(key="m", path=str(audio_dir / "song.mp3"))],
        artists=[IncomingArtist(key="nina", name="Nina Simone")],
        releases=[
            IncomingRelease(
                key="rg",
                title="Pastel Blues",
                credit=credit_,
                tracks=[
                    IncomingTrack(
                        key="t", medium_key="m", position=1, title="Sinnerman", credit=credit_
                    )
                ],
            )
        ],
        issues=[
            Issue(
                id="uncertain_recording:t",
                kind=IssueKind.uncertain_recording,
                message="Is 'Sinnerman' one of these?",
                subject=Subject(type=SubjectType.track, key="t", label="Sinnerman"),
                candidates=[
                    IssueCandidate(
                        type=CandidateType.recording, id=uuid.uuid4(), title="S", score=80
                    )
                ],
                choices=[Choice.pick_candidate, Choice.create_new],
            )
        ],
    )


def test_import_summary_preview_and_queue(
    client: TestClient, session: Session, audio_dir: Path, settings: Settings
) -> None:
    other = make_recording(client, make_artist(client), "Another")
    review = create_import(
        session, ImportKind.youtube, "https://www.youtube.com/watch?v=GSWtPDAN3PE"
    )
    review.state = JobState.needs_review
    review.proposal = proposal_with_issue(audio_dir).model_dump(mode="json")
    running = create_import(session, ImportKind.bandcamp, "https://a.bandcamp.com/album/b")
    running.state, running.stage = JobState.running, JobStage.download
    running.progress_done, running.progress_total, running.progress_item = 1, 4, 0.5
    session.commit()
    waiting = create_import(session, ImportKind.folder, str(audio_dir))
    session.commit()

    summary = client.get("/imports/summary").json()
    assert {k: summary[k] for k in ("queued", "running", "needs_review", "failed")} == {
        "queued": 1,
        "running": 1,
        "needs_review": 1,
        "failed": 0,
    }
    assert summary["running_fraction"] == pytest.approx(1.5 / 4)
    assert TIMESTAMP.match(summary["changed_at"])

    jobs = {j["id"]: j for j in client.get("/imports").json()}
    job = jobs[str(review.id)]
    assert (job["title"], job["subtitle"], job["link_kind"]) == (
        "Pastel Blues",
        "Nina Simone",
        "youtube_video",
    )
    assert job["first_issue"] == {
        "id": "uncertain_recording:t",
        "kind": "uncertain_recording",
        "message": "Is 'Sinnerman' one of these?",
    }
    assert job["counts"] == {"tracks": 1, "failed": 0}
    assert jobs[str(running.id)]["progress"]["step"] == "downloading"
    assert jobs[str(waiting.id)]["waiting_for"] == str(running.id)
    assert TIMESTAMP.match(job["created_at"])

    # Preview the incoming track's audio.
    stream = client.get(f"/imports/{review.id}/stream", params={"track_key": "t"})
    assert stream.status_code == 200
    assert stream.content == (audio_dir / "song.mp3").read_bytes()
    assert client.get(f"/imports/{review.id}/stream", params={"track_key": "x"}).status_code == 404

    # "Search the library for another": any recording is a valid pick.
    answer = {"issue_id": "uncertain_recording:t", "choice": "pick_candidate"}
    ok = client.post(
        f"/imports/{review.id}/review", json={"answers": [{**answer, "candidate_id": other["id"]}]}
    )
    assert ok.status_code == 200, ok.text
    bad = {**answer, "candidate_id": str(uuid.uuid4())}
    session.get_one(ImportJob, review.id).state = JobState.needs_review
    session.commit()
    assert client.post(f"/imports/{review.id}/review", json={"answers": [bad]}).status_code == 422

    # Cancelling: a queued job at once, a running one at its next step.
    assert client.post(f"/imports/{waiting.id}/cancel").json()["state"] == "dismissed"
    assert client.post(f"/imports/{running.id}/cancel").json()["state"] == "dismissed"
    assert client.post(f"/imports/{running.id}/cancel").status_code == 409


def test_running_job_notices_cancel(session: Session, settings: Settings) -> None:
    job = create_import(session, ImportKind.youtube, "https://youtu.be/x")
    job.state = JobState.running
    session.commit()
    with MusicBrainz() as musicbrainz:
        ctx = JobContext(session, job, musicbrainz, settings)
        ctx.log("working")
        session.get_one(ImportJob, job.id).state = JobState.dismissed  # the API's cancel
        session.commit()
        with pytest.raises(JobCancelled):
            ctx.advance()
        # The worker's own state change (here: stopping for review) does not win.
        with pytest.raises(JobCancelled):
            ctx.set_state(JobState.needs_review)
        assert session.get_one(ImportJob, job.id).state is JobState.dismissed


def test_timestamps_have_milliseconds_in_utc(client: TestClient) -> None:
    artist = make_artist(client)
    assert TIMESTAMP.match(artist["created_at"]) and TIMESTAMP.match(artist["updated_at"])


@pytest.mark.parametrize(
    ("name", "media_type", "codec"),
    [
        ("song.mp3", "audio/mpeg", "mp3"),
        ("song.m4a", "audio/mp4", "aac"),
        ("song.opus", "audio/ogg", "opus"),
        ("song.webm", "audio/webm", "opus"),
    ],
)
def test_stream_types_and_ranges(
    client: TestClient, audio_dir: Path, name: str, media_type: str, codec: str
) -> None:
    """What a player needs: the format before fetching (playback info), the same type on
    the stream, and a 206 with the total for AVPlayer's first two-byte probe."""
    file = source(client, "file", uri=str(audio_dir / name))
    assert (file["playback"]["media_type"], file["playback"]["codec"]) == (media_type, codec)
    size = (audio_dir / name).stat().st_size
    response = client.get(f"/sources/{file['id']}/stream", headers={"Range": "bytes=0-1"})
    assert response.status_code == 206
    assert response.headers["content-type"] == media_type
    assert response.headers["content-range"] == f"bytes 0-1/{size}"
