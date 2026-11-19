import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, col, delete, func, select

from ep.api.deps import get_musicbrainz
from ep.credits import CreditPart, get_or_create_artist_credit
from ep.importers.musicbrainz import (
    RecordingConflict,
    ReleaseConflict,
    import_release,
    propose_recordings,
)
from ep.main import app
from ep.matching import Decision, MatchMethod
from ep.metadata.musicbrainz import MBArtist, MBRelease, MusicBrainzError
from ep.models import (
    Artist,
    ArtistCredit,
    ArtistType,
    EntityTag,
    EntityType,
    ExternalId,
    Recording,
    ReleaseGroup,
    ReleaseGroupType,
    Source,
    Tag,
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
    SHINICHI_ATOBE,
    MockMusicBrainz,
    atobe_promises,
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


def loose_recording(session: Session, title: str, artist_name: str) -> Recording:
    """A recording without MBID or track, as a YouTube import leaves it."""
    artist = Artist(name=artist_name, sort_name=artist_name)
    session.add(artist)
    credit = get_or_create_artist_credit(session, [CreditPart(artist, artist_name)])
    recording = Recording(title=title, artist_credit_id=credit.id)
    session.add(recording)
    session.flush()
    return recording


def test_propose_and_attach_loose_recording(session: Session) -> None:
    release = promises()
    loose = loose_recording(session, "Promises - Movement 1", "Floating Points")
    session.add(
        Source(kind="youtube", uri="https://www.youtube.com/watch?v=x", recording_id=loose.id)
    )
    session.add(
        ExternalId(
            entity_type=EntityType.recording, entity_id=loose.id, scheme="youtube", value="x"
        )
    )
    session.flush()

    proposals = propose_recordings(session, release)
    first_track = release.media[0].tracks[0]
    assert set(proposals) == {t.id for t in release.media[0].tracks}
    assert proposals[first_track.id].match is loose
    assert proposals[first_track.id].candidates[0].method is MatchMethod.fuzzy
    # Movement 2 is not auto-matched to the Movement 1 recording.
    assert proposals[release.media[0].tracks[1].id].match is None
    matches = {tid: r.match for tid, r in proposals.items() if r.match}
    assert list(matches) == [first_track.id]

    release_group = import_release(session, release, recordings=matches)
    assert release_group.tracks[0].recording_id == loose.id
    assert loose.mbid == uuid.UUID(MOVEMENT_1)
    assert loose.title == "Promises - Movement 1"  # kept; the track carries MB's title
    assert release_group.tracks[0].title == "Promises: Movement 1"
    assert loose.length_ms == 384151  # filled in: it had none
    assert loose.artist_credit.name == "Floating Points"  # kept
    assert count(session, Recording) == 9
    assert count(session, Source) == 1
    schemes = session.exec(
        select(ExternalId.scheme)
        .where(ExternalId.entity_id == loose.id)
        .order_by(ExternalId.scheme)
    ).all()
    assert schemes == ["isrc", "youtube"]

    # Imported again: every track now matches its recording by MBID.
    again = propose_recordings(session, release)
    assert all(r.candidates[0].method is MatchMethod.mbid for r in again.values())
    assert again[first_track.id].match is loose


def test_import_rejects_conflicting_prematched_recordings(session: Session) -> None:
    release = promises()
    tracks = release.media[0].tracks
    loose = loose_recording(session, "Movement", "Floating Points")
    with pytest.raises(RecordingConflict, match="is given for tracks of"):
        import_release(session, release, recordings={tracks[0].id: loose, tracks[1].id: loose})
    loose.mbid = uuid.uuid4()
    with pytest.raises(RecordingConflict, match="has MBID"):
        import_release(session, release, recordings={tracks[0].id: loose})
    with pytest.raises(ValueError, match="not tracks of release"):
        import_release(session, release, recordings={uuid.uuid4(): loose})
    loose.mbid = None
    session.flush()
    import_release(session, release)
    with pytest.raises(RecordingConflict, match="already in the library"):
        import_release(session, release, recordings={tracks[0].id: loose})


def test_propose_demotes_one_recording_matched_by_two_tracks(session: Session) -> None:
    release = big_fun()
    # Two tracks of different MusicBrainz recordings, both with this ISRC.
    loose = loose_recording(session, "Something", "Miles Davis")
    disc_1 = release.media[0].tracks
    two = [
        t.model_copy(
            update={"recording": t.recording.model_copy(update={"isrcs": ["XX0000000001"]})}
        )
        for t in disc_1[:2]
    ]
    medium = release.media[0].model_copy(update={"tracks": [*two, *disc_1[2:]]})
    release = release.model_copy(update={"media": [medium, release.media[1]]})
    session.add(
        ExternalId(
            entity_type=EntityType.recording,
            entity_id=loose.id,
            scheme="isrc",
            value="XX0000000001",
        )
    )
    session.flush()
    proposals = propose_recordings(session, release)
    for track in two:
        assert proposals[track.id].decision is Decision.needs_review
        assert proposals[track.id].candidates[0].entity is loose


# Endpoints


@pytest.fixture
def mb(client: TestClient) -> Iterator[MockMusicBrainz]:
    mock = MockMusicBrainz()
    app.dependency_overrides[get_musicbrainz] = lambda: mock
    yield mock
    del app.dependency_overrides[get_musicbrainz]


def test_import_endpoint(client: TestClient, mb: MockMusicBrainz) -> None:
    response = client.post(
        "/musicbrainz/import", json={"type": "release", "mbid": PROMISES_RELEASE}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["type"], body["mbid"]) == ("release_group", PROMISES_RELEASE_GROUP)
    assert body["link"] == f"[[release_group:{PROMISES_RELEASE_GROUP}|{body['title']}]]"
    assert client.get(f"/release-groups/{body['id']}").json()["release_mbid"] == PROMISES_RELEASE
    tracks = client.get(f"/release-groups/{body['id']}/tracks").json()
    assert len(tracks) == 9
    recording = tracks[0]["recording"]
    external_ids = client.get(f"/recordings/{recording['id']}/external-ids").json()
    assert [(e["scheme"], e["value"]) for e in external_ids] == [("isrc", MOVEMENT_1_ISRC)]


def test_import_endpoint_errors(client: TestClient, mb: MockMusicBrainz) -> None:
    response = client.post(
        "/musicbrainz/import", json={"type": "release", "mbid": str(uuid.uuid4())}
    )
    assert response.status_code == 404
    artist = make_artist(client)
    make_release_group(
        client,
        artist,
        mbid=PROMISES_RELEASE_GROUP,
        release_mbid=str(uuid.uuid4()),
    )
    response = client.post(
        "/musicbrainz/import", json={"type": "release", "mbid": PROMISES_RELEASE}
    )
    assert response.status_code == 409
    assert "already imported" in response.json()["detail"]


def test_import_endpoint_conflicting_release_mbid(client: TestClient, mb: MockMusicBrainz) -> None:
    # Another release group (of another MusicBrainz release group) claims this release.
    artist = make_artist(client)
    make_release_group(client, artist, mbid=str(uuid.uuid4()), release_mbid=BIG_FUN_RELEASE)
    response = client.post("/musicbrainz/import", json={"type": "release", "mbid": BIG_FUN_RELEASE})
    assert response.status_code == 409
    assert "already imported" in response.json()["detail"]


def test_import_endpoint_attaches_release_group_without_mbid(
    client: TestClient, mb: MockMusicBrainz
) -> None:
    # A release group made by hand for this release: it gains the release group's MBID.
    local = make_release_group(client, make_artist(client), release_mbid=BIG_FUN_RELEASE)
    response = client.post("/musicbrainz/import", json={"type": "release", "mbid": BIG_FUN_RELEASE})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == local["id"]
    assert response.json()["mbid"] is not None


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


def test_import_release_attaches_confident_artist_matches(session: Session) -> None:
    """A library artist without MBID that clearly is the release's artist gains the MBID;
    an ambiguous name (two candidates) gets a new artist; given artists are used."""
    floating = Artist(name="Floating Points", sort_name="Floating Points")
    pharoahs = [Artist(name="Pharoah Sanders", sort_name="Sanders, Pharoah") for _ in range(2)]
    lso = Artist(name="London Symphony Orchestra", sort_name="London Symphony Orchestra")
    session.add_all([floating, *pharoahs, lso])
    session.flush()
    release = promises()
    lso_mbid = release.artist_credit[2].artist.id
    import_release(session, release, artists={lso_mbid: None})
    assert floating.mbid == release.artist_credit[0].artist.id
    assert [p.mbid for p in pharoahs] == [None, None]
    assert lso.mbid is None  # "a new artist" was asked for
    assert count(session, Artist) == 6


def _tag_names(session: Session, release_group: ReleaseGroup) -> list[str]:
    return sorted(
        session.exec(
            select(Tag.name)
            .join(EntityTag, col(EntityTag.tag_id) == Tag.id)
            .where(EntityTag.entity_id == release_group.id)
        ).all()
    )


def test_genres_become_tags_of_a_new_release_group_only(session: Session) -> None:
    release = promises()
    assert release.release_group is not None
    assert [genre.name for genre in release.release_group.genres][:2] == ["ambient", "electronic"]
    release_group = import_release(session, release, tags=["Jazz", "spiritual jazz"])
    # Release group and release genres, and the tags given, once each ignoring case.
    expected = [
        "ambient",
        "electronic",
        "instrumental",
        "instrumental jazz",
        "jazz",  # MusicBrainz's spelling came first
        "modern classical",
        "spiritual jazz",
        "third stream",
    ]
    assert _tag_names(session, release_group) == sorted(expected)
    # The user removes one; importing again does not bring it back.
    ambient = session.exec(select(Tag.id).where(Tag.name == "ambient")).one()
    session.exec(delete(EntityTag).where(col(EntityTag.tag_id) == ambient))
    import_release(session, release, tags=["new"])
    assert "ambient" not in _tag_names(session, release_group)
    assert "new" not in _tag_names(session, release_group)


# Artists not named in Latin script


def _atobe(session: Session) -> Artist:
    return session.exec(select(Artist).where(Artist.mbid == uuid.UUID(SHINICHI_ATOBE))).one()


def _alias_lookups(mb: MockMusicBrainz) -> list[str]:
    return [r.url.path for r in mb.requests if r.url.params.get("inc") == "aliases"]


def test_import_release_names_an_artist_by_its_latin_alias(session: Session) -> None:
    """MusicBrainz's "跡部進一" becomes "Shinichi Atobe" (his primary English alias), with
    the MusicBrainz name as original name, credited by the alias: one aliases lookup,
    for him only. Importing again finds him by MBID, asking nothing."""
    mb = MockMusicBrainz()
    release_group = import_release(
        session, MBRelease.model_validate_json(atobe_promises()), musicbrainz=mb
    )
    atobe = _atobe(session)
    assert (atobe.name, atobe.sort_name, atobe.original_name) == (
        "Shinichi Atobe",
        "Atobe, Shinichi",
        "跡部進一",
    )
    assert atobe.original_name_normalized == "跡部進一"
    assert release_group.artist_credit.name.startswith("Shinichi Atobe, Pharoah Sanders")
    assert _alias_lookups(mb) == [f"/ws/2/artist/{SHINICHI_ATOBE}"]
    mb.requests.clear()
    import_release(session, MBRelease.model_validate_json(atobe_promises()), musicbrainz=mb)
    assert count(session, Artist) == 3
    assert mb.requests == []


def test_artist_keeps_its_name_without_aliases(session: Session) -> None:
    """Without a client, or when MusicBrainz cannot answer, the MusicBrainz name stays."""
    import_release(session, MBRelease.model_validate_json(atobe_promises()))
    assert (_atobe(session).name, _atobe(session).original_name) == ("跡部進一", None)


class _Unavailable(MockMusicBrainz):
    def get_artist(self, mbid: uuid.UUID) -> MBArtist:
        raise MusicBrainzError("MusicBrainz request failed")


def test_artist_aliases_lookup_failure_keeps_the_name(session: Session) -> None:
    mb = _Unavailable()
    import_release(session, MBRelease.model_validate_json(atobe_promises()), musicbrainz=mb)
    assert (_atobe(session).name, _atobe(session).original_name) == ("跡部進一", None)


def test_library_artist_found_by_original_name(session: Session) -> None:
    """A library artist named "跡部進一" (from tags, no MBID) is the MusicBrainz artist
    (matched by the original name): it gains the MBID and keeps its name."""
    tagged = Artist(name="跡部進一", sort_name="跡部進一")
    session.add(tagged)
    session.flush()
    import_release(
        session, MBRelease.model_validate_json(atobe_promises()), musicbrainz=MockMusicBrainz()
    )
    assert _atobe(session) is tagged
    assert (tagged.name, tagged.original_name) == ("跡部進一", None)


def test_import_endpoint_names_artist_by_latin_alias(
    client: TestClient, mb: MockMusicBrainz
) -> None:
    """An artist import looks the artist up with its aliases (one request); the artist
    is found by its original name in the list filter and in search."""
    response = client.post("/musicbrainz/import", json={"type": "artist", "mbid": SHINICHI_ATOBE})
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Shinichi Atobe"
    assert [r.url.params.get("inc") for r in mb.requests] == ["aliases"]
    artist = client.get(f"/artists/{response.json()['id']}").json()
    assert (artist["name"], artist["original_name"]) == ("Shinichi Atobe", "跡部進一")
    make_release_group(client, artist)  # listed: credited on a release group
    for q in "跡部", "atobe":
        listed = client.get("/artists", params={"q": q}).json()
        assert [a["name"] for a in listed] == ["Shinichi Atobe"], q
    hits = client.get("/search", params={"q": "跡部進一", "types": "artist"}).json()
    assert [hit["title"] for hit in hits] == ["Shinichi Atobe"]


# Re-imports give library artists their Latin-script name


def _old_style_atobe(session: Session, **fields: str) -> Artist:
    """Shinichi Atobe as imported before artists were named by their alias."""
    values = {"name": "跡部進一", "sort_name": "Atobe, Shinichi", **fields}
    artist = Artist(mbid=uuid.UUID(SHINICHI_ATOBE), **values)
    session.add(artist)
    session.flush()
    return artist


def test_reimport_renames_an_artist_still_named_by_musicbrainz(session: Session) -> None:
    """Like a missing cover filled in: the artist takes its Latin-script name, the old
    one becomes the original name (the sort name, in Latin script, stays); told to
    ``log``. One aliases lookup, also when the release credits him on nothing new."""
    atobe = _old_style_atobe(session)
    mb, messages = MockMusicBrainz(), []
    release = MBRelease.model_validate_json(atobe_promises())
    import_release(session, release, musicbrainz=mb, log=messages.append)
    assert (atobe.name, atobe.sort_name, atobe.original_name) == (
        "Shinichi Atobe",
        "Atobe, Shinichi",
        "跡部進一",
    )
    assert messages == ["artist '跡部進一' renamed to 'Shinichi Atobe'"]
    assert _alias_lookups(mb) == [f"/ws/2/artist/{SHINICHI_ATOBE}"]
    assert count(session, Artist) == 3


def test_reimport_of_an_imported_album_renames_its_artist(session: Session) -> None:
    """The release group and recordings are in the library already: their credits are
    kept, the artist is still renamed."""
    import_release(session, MBRelease.model_validate_json(atobe_promises()))
    atobe = _atobe(session)
    assert atobe.name == "跡部進一"
    mb = MockMusicBrainz()
    import_release(session, MBRelease.model_validate_json(atobe_promises()), musicbrainz=mb)
    assert (atobe.name, atobe.original_name) == ("Shinichi Atobe", "跡部進一")
    assert len(_alias_lookups(mb)) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "Atobe"},  # renamed in EP
        {"name": "跡部 進一"},  # renamed in EP, in Japanese still
        {"original_name": "跡部進一"},  # named so already
    ],
)
def test_reimport_leaves_an_artist_named_otherwise_alone(
    session: Session, fields: dict[str, str]
) -> None:
    atobe = _old_style_atobe(session, **fields)
    before = (atobe.name, atobe.sort_name, atobe.original_name)
    mb = MockMusicBrainz()
    import_release(session, MBRelease.model_validate_json(atobe_promises()), musicbrainz=mb)
    assert (atobe.name, atobe.sort_name, atobe.original_name) == before
    assert _alias_lookups(mb) == []


def test_reimport_of_latin_named_artists_asks_for_no_aliases(session: Session) -> None:
    import_release(session, promises())
    mb = MockMusicBrainz()
    import_release(session, promises(), musicbrainz=mb)
    assert _alias_lookups(mb) == []
    assert mb.requests == []


def test_reimport_keeps_the_name_when_musicbrainz_fails(session: Session) -> None:
    atobe = _old_style_atobe(session)
    import_release(
        session, MBRelease.model_validate_json(atobe_promises()), musicbrainz=_Unavailable()
    )
    assert (atobe.name, atobe.original_name) == ("跡部進一", None)


def test_import_endpoint_renames_an_artist_in_the_library(
    client: TestClient, session: Session, mb: MockMusicBrainz
) -> None:
    atobe = _old_style_atobe(session)
    session.commit()
    response = client.post("/musicbrainz/import", json={"type": "artist", "mbid": SHINICHI_ATOBE})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(atobe.id)
    artist = client.get(f"/artists/{atobe.id}").json()
    assert (artist["name"], artist["original_name"]) == ("Shinichi Atobe", "跡部進一")
