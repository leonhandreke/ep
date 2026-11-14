import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, func, select

from ep.api.musicbrainz import get_musicbrainz
from ep.importers.musicbrainz import ReleaseConflict, import_release
from ep.main import app
from ep.metadata.musicbrainz import MBRelease
from ep.models import (
    Artist,
    ArtistCredit,
    ArtistType,
    EntityType,
    ExternalId,
    Recording,
    ReleaseGroup,
    ReleaseGroupType,
    Track,
)
from tests.helpers import make_artist, make_release_group
from tests.mock_musicbrainz import (
    BIG_FUN_RELEASE,
    FLOATING_POINTS,
    MOVEMENT_1,
    MOVEMENT_1_ISRC,
    PROMISES_RELEASE,
    PROMISES_RELEASE_GROUP,
    MockMusicBrainz,
    fixture,
)


def promises() -> MBRelease:
    return MBRelease.model_validate_json(fixture("release_promises"))


def big_fun() -> MBRelease:
    return MBRelease.model_validate_json(fixture("release_big_fun"))


def count(session: Session, model: type[SQLModel]) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def test_import_release(session: Session) -> None:
    release_group = import_release(session, promises())

    assert release_group.mbid == uuid.UUID(PROMISES_RELEASE_GROUP)
    assert release_group.release_mbid == uuid.UUID(PROMISES_RELEASE)
    assert release_group.title == "Promises"
    assert release_group.type is ReleaseGroupType.album
    assert release_group.date == "2020-11-24"  # the release group's first release
    assert release_group.barcode == "680899009720"
    credit = release_group.artist_credit
    assert credit.name == "Floating Points, Pharoah Sanders & The London Symphony Orchestra"
    assert [(n.artist.name, n.credited_name, n.join_phrase) for n in credit.names] == [
        ("Floating Points", "Floating Points", ", "),
        ("Pharoah Sanders", "Pharoah Sanders", " & "),
        ("London Symphony Orchestra", "The London Symphony Orchestra", ""),
    ]
    pharoah = credit.names[1].artist
    assert (pharoah.sort_name, pharoah.disambiguation) == (
        "Sanders, Pharoah",
        "US jazz saxophonist",
    )
    assert pharoah.type is ArtistType.person
    assert credit.names[2].artist.type is ArtistType.group  # Orchestra

    tracks = release_group.tracks
    assert [(t.disc, t.position) for t in tracks] == [(1, i) for i in range(1, 10)]
    first = tracks[0].recording
    assert (first.mbid, first.title, first.length_ms) == (
        uuid.UUID(MOVEMENT_1),
        "Promises: Movement 1",
        384151,
    )
    assert first.artist_credit_id == credit.id  # the same credit, shared
    assert tracks[0].title is None
    assert count(session, ArtistCredit) == 1
    isrcs = session.exec(
        select(ExternalId.value).where(
            ExternalId.entity_type == EntityType.recording,
            ExternalId.entity_id == first.id,
            ExternalId.scheme == "isrc",
        )
    ).all()
    assert isrcs == [MOVEMENT_1_ISRC]


def test_import_is_idempotent(session: Session) -> None:
    first = import_release(session, promises())
    counts = [count(session, m) for m in (Artist, ArtistCredit, Recording, Track, ExternalId)]
    again = import_release(session, promises())
    assert again.id == first.id
    assert [count(session, m) for m in (Artist, ArtistCredit, Recording, Track, ExternalId)] == (
        counts
    )


def test_import_reuses_rows_by_mbid_and_keeps_edits(session: Session) -> None:
    artist = Artist(mbid=uuid.UUID(FLOATING_POINTS), name="FP (edited)", sort_name="FP")
    session.add(artist)
    session.flush()
    release_group = import_release(session, promises())
    assert release_group.artist_credit.names[0].artist_id == artist.id
    assert artist.name == "FP (edited)"
    assert release_group.artist_credit.name.startswith("Floating Points, ")  # as credited
    assert count(session, Artist) == 3


def test_import_adds_tracks_to_existing_release_group_without_release(session: Session) -> None:
    import_release(session, promises())
    release_group = session.exec(select(ReleaseGroup)).one()
    release_group.release_mbid = None
    session.delete(release_group.tracks[0])
    session.flush()
    session.expire(release_group, ["tracks"])
    again = import_release(session, promises())
    assert again.release_mbid == uuid.UUID(PROMISES_RELEASE)
    assert len(again.tracks) == 9


def test_import_rejects_second_release_of_a_release_group(session: Session) -> None:
    import_release(session, promises())
    other = promises().model_copy(update={"id": uuid.uuid4()})
    with pytest.raises(ReleaseConflict, match="already imported from release"):
        import_release(session, other)


def test_import_multi_disc_release(session: Session) -> None:
    release_group = import_release(session, big_fun())
    assert release_group.date == "1974-04-19"
    assert [(t.disc, t.position) for t in release_group.tracks] == [
        (disc, position) for disc in (1, 2) for position in (1, 2, 3, 4)
    ]
    frog = release_group.tracks[6]
    assert frog.recording.title == "The Little Blue Frog (mst)"
    assert frog.title == "The Little Blue Frog"  # the track title overrides the recording's


# Endpoints


@pytest.fixture
def mb(client: TestClient) -> Iterator[MockMusicBrainz]:
    mock = MockMusicBrainz()
    app.dependency_overrides[get_musicbrainz] = lambda: mock
    yield mock
    del app.dependency_overrides[get_musicbrainz]


def test_import_endpoint(client: TestClient, mb: MockMusicBrainz) -> None:
    response = client.post("/musicbrainz/import", json={"release_mbid": PROMISES_RELEASE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mbid"] == PROMISES_RELEASE_GROUP
    assert body["release_mbid"] == PROMISES_RELEASE
    tracks = client.get(f"/release-groups/{body['id']}/tracks").json()
    assert len(tracks) == 9
    recording = tracks[0]["recording"]
    external_ids = client.get(f"/recordings/{recording['id']}/external-ids").json()
    assert [(e["scheme"], e["value"]) for e in external_ids] == [("isrc", MOVEMENT_1_ISRC)]


def test_import_endpoint_errors(client: TestClient, mb: MockMusicBrainz) -> None:
    response = client.post("/musicbrainz/import", json={"release_mbid": str(uuid.uuid4())})
    assert response.status_code == 404
    artist = make_artist(client)
    make_release_group(
        client,
        artist,
        mbid=PROMISES_RELEASE_GROUP,
        release_mbid=str(uuid.uuid4()),
    )
    response = client.post("/musicbrainz/import", json={"release_mbid": PROMISES_RELEASE})
    assert response.status_code == 409
    assert "already imported" in response.json()["detail"]


def test_import_endpoint_conflicting_release_mbid(client: TestClient, mb: MockMusicBrainz) -> None:
    # Another release group already claims this release: a constraint violation, 409.
    make_release_group(client, make_artist(client), release_mbid=BIG_FUN_RELEASE)
    response = client.post("/musicbrainz/import", json={"release_mbid": BIG_FUN_RELEASE})
    assert response.status_code == 409


def test_musicbrainz_search_endpoint(client: TestClient, mb: MockMusicBrainz) -> None:
    response = client.get("/musicbrainz/search", params={"type": "artist", "q": "bjork"})
    assert response.status_code == 200
    assert response.json()[0] == {
        "type": "artist",
        "mbid": "87c5dedd-371d-4a53-9f7f-80522fb7f3cb",
        "title": "Björk",
        "artist_credit": None,
        "disambiguation": "",
        "date": None,
        "score": 100,
    }
    releases = client.get("/musicbrainz/search", params={"type": "release", "q": "promises"})
    hit = releases.json()[0]
    assert hit["artist_credit"].startswith("Floating Points, Pharoah Sanders")
    assert hit["date"] == "2020-11-24"
    recordings = client.get("/musicbrainz/search", params={"type": "recording", "q": "movement"})
    assert recordings.json()[0]["title"] == "Movement 1"
    # Plain text: query syntax is escaped.
    client.get("/musicbrainz/search", params={"type": "artist", "q": "AC/DC"})
    assert mb.requests[-1].url.params["query"] == r"AC\/DC"
