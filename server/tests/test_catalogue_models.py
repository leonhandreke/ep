import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ep.credits import (
    CreditPart,
    delete_artist_credit_if_unused,
    get_or_create_artist_credit,
    render_credit,
)
from ep.models import (
    Artist,
    ArtistCredit,
    ArtistCreditName,
    EntityType,
    ExternalId,
    Recording,
    ReleaseGroup,
    ReleaseGroupType,
    Track,
)


@pytest.fixture
def simon(session: Session) -> Artist:
    artist = Artist(name="Paul Simon", sort_name="Simon, Paul")
    session.add(artist)
    return artist


@pytest.fixture
def garfunkel(session: Session) -> Artist:
    artist = Artist(name="Art Garfunkel", sort_name="Garfunkel, Art")
    session.add(artist)
    return artist


def duo(simon: Artist, garfunkel: Artist) -> list[CreditPart]:
    return [CreditPart(simon, "Simon", " & "), CreditPart(garfunkel, "Garfunkel")]


def test_render_credit(simon: Artist, garfunkel: Artist) -> None:
    assert render_credit(duo(simon, garfunkel)) == "Simon & Garfunkel"


def test_credit_is_created_once_and_reused(
    session: Session, simon: Artist, garfunkel: Artist
) -> None:
    credit = get_or_create_artist_credit(session, duo(simon, garfunkel))
    session.flush()
    assert credit.name == "Simon & Garfunkel"
    assert [(n.artist_id, n.position) for n in credit.names] == [(simon.id, 0), (garfunkel.id, 1)]

    assert get_or_create_artist_credit(session, duo(simon, garfunkel)) is credit
    # Same rendered name, different artists: a different credit.
    impostor = Artist(name="Simon", sort_name="Simon")
    session.add(impostor)
    other = get_or_create_artist_credit(
        session, [CreditPart(impostor, "Simon", " & "), CreditPart(garfunkel, "Garfunkel")]
    )
    assert other is not credit
    assert other.name == credit.name


def test_credit_needs_an_artist(session: Session) -> None:
    with pytest.raises(ValueError):
        get_or_create_artist_credit(session, [])


def test_unused_credit_is_deleted_with_its_names(session: Session, simon: Artist) -> None:
    used = get_or_create_artist_credit(session, [CreditPart(simon, "Paul Simon")])
    unused = get_or_create_artist_credit(session, [CreditPart(simon, "Simon")])
    session.add(Recording(title="Graceland", artist_credit_id=used.id))
    delete_artist_credit_if_unused(session, used)
    delete_artist_credit_if_unused(session, unused)
    session.flush()
    assert session.exec(select(ArtistCredit)).all() == [used]
    assert len(session.exec(select(ArtistCreditName)).all()) == 1


def test_mbid_unique_but_optional(session: Session) -> None:
    session.add_all([Artist(name="A", sort_name="A"), Artist(name="B", sort_name="B")])
    session.flush()
    mbid = uuid.uuid4()
    session.add_all([Artist(name="C", sort_name="C", mbid=mbid)])
    session.flush()
    session.add(Artist(name="D", sort_name="D", mbid=mbid))
    with pytest.raises(IntegrityError):
        session.flush()


def album(session: Session, artist: Artist) -> tuple[ReleaseGroup, Recording]:
    credit = get_or_create_artist_credit(session, [CreditPart(artist, artist.name)])
    release_group = ReleaseGroup(
        title="Graceland", artist_credit_id=credit.id, type=ReleaseGroupType.album, date="1986"
    )
    recording = Recording(title="Graceland", artist_credit_id=credit.id)
    session.add_all([release_group, recording])
    session.add(Track(release_group_id=release_group.id, recording_id=recording.id, position=1))
    session.flush()
    return release_group, recording


def test_tracks_are_deleted_with_their_release_group(session: Session, simon: Artist) -> None:
    release_group, recording = album(session, simon)
    session.delete(release_group)
    session.flush()
    assert session.exec(select(Track)).all() == []
    assert session.get(Recording, recording.id) is recording


def test_track_position_is_unique_per_disc(session: Session, simon: Artist) -> None:
    release_group, recording = album(session, simon)
    session.add(
        Track(release_group_id=release_group.id, recording_id=recording.id, disc=2, position=1)
    )
    session.flush()
    session.add(Track(release_group_id=release_group.id, recording_id=recording.id, position=1))
    with pytest.raises(IntegrityError):
        session.flush()


def test_enums_are_stored_as_values(session: Session, simon: Artist) -> None:
    session.add(
        ExternalId(entity_type=EntityType.release_group, entity_id=simon.id, scheme="x", value="y")
    )
    session.flush()
    stored = session.connection().exec_driver_sql("SELECT entity_type FROM external_id").scalar()
    assert stored == "release_group"
