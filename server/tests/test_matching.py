import uuid
from typing import Any

import pytest
from sqlmodel import Session

from ep.credits import CreditPart, get_or_create_artist_credit
from ep.matching import Decision, MatchMethod, match_artist, match_recording, match_release_group
from ep.matching.normalize import normalize, normalize_artist, normalize_title
from ep.models import Artist, EntityType, ExternalId, Recording, ReleaseGroup, ReleaseGroupType

# Normalisation


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Jóga", "joga"),
        ("Hyperballad!", "hyperballad"),
        ("Here Comes the Sun (Remastered 2009)", "here comes the sun"),
        ("Here Comes the Sun - 2009 Remaster", "here comes the sun"),
        ("Hey Jude \u2013 Remastered 2015", "hey jude"),  # en dash
        ("Paranoid [Deluxe Edition]", "paranoid"),
        ("Pinball Wizard (Mono)", "pinball wizard"),
        ("Sinnerman (feat. Someone)", "sinnerman"),
        ("Sinnerman [ft. Someone] (Remastered)", "sinnerman"),
        ("Sinnerman feat. Someone", "sinnerman"),
        ("Sinnerman featuring Someone (Live)", "sinnerman live"),  # live is a different recording
        ("Mono", "mono"),  # nothing left after stripping: keep the title
        ("Don't Stop Me Now", "don t stop me now"),
        ("Rock & Roll", "rock and roll"),
        ("Motörhead / Ace of Spades", "motorhead ace of spades"),
        ("Søvn", "sovn"),
    ],
)
def test_normalize_title(title: str, expected: str) -> None:
    assert normalize_title(title) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Björk", "bjork"),
        ("The Beatles", "beatles"),
        ("Beatles, The", "beatles"),
        ("The The", "the"),
        ("Simon & Garfunkel", "simon and garfunkel"),
        ("Kendrick Lamar feat. SZA", "kendrick lamar"),
        ("Sigur Rós", "sigur ros"),
        ("Theodore", "theodore"),
    ],
)
def test_normalize_artist(name: str, expected: str) -> None:
    assert normalize_artist(name) == expected


def test_normalize_collapses_whitespace_and_case() -> None:
    assert normalize("  ÉMILIE   Simon  ") == "emilie simon"


# Library rows


def artist(session: Session, name: str, **fields: Any) -> Artist:
    row = Artist(name=name, sort_name=fields.pop("sort_name", name), **fields)
    session.add(row)
    return row


def recording(
    session: Session, title: str, *artists: Artist, joins: tuple[str, ...] = (), **fields: Any
) -> Recording:
    parts = [
        CreditPart(a, a.name, joins[i] if i < len(joins) else "") for i, a in enumerate(artists)
    ]
    credit = get_or_create_artist_credit(session, parts)
    row = Recording(title=title, artist_credit_id=credit.id, **fields)
    session.add(row)
    session.flush()
    return row


def release_group(session: Session, title: str, *artists: Artist, **fields: Any) -> ReleaseGroup:
    credit = get_or_create_artist_credit(session, [CreditPart(a, a.name) for a in artists])
    row = ReleaseGroup(
        title=title,
        artist_credit_id=credit.id,
        type=ReleaseGroupType.album,
        **fields,
    )
    session.add(row)
    session.flush()
    return row


# Artists


def test_match_artist_ignores_accents_case_and_the(session: Session) -> None:
    bjork = artist(session, "Björk")
    beatles = artist(session, "The Beatles", sort_name="Beatles, The")
    artist(session, "Brant Bjork")
    session.flush()
    result = match_artist(session, "BJORK")
    assert result.decision is Decision.auto_match
    assert result.match is bjork
    assert result.candidates[0].method is MatchMethod.fuzzy
    assert match_artist(session, "Beatles").match is beatles


def test_match_artist_by_sort_name(session: Session) -> None:
    nina = artist(session, "Nina Simone", sort_name="Simone, Nina")
    session.flush()
    assert match_artist(session, "Simone, Nina").match is nina


def test_match_artist_same_name_needs_review(session: Session) -> None:
    artist(session, "Low", disambiguation="US slowcore band")
    artist(session, "Low", disambiguation="Swedish punk band")
    session.flush()
    result = match_artist(session, "Low")
    assert result.decision is Decision.needs_review
    assert len(result.candidates) == 2
    assert result.match is None


def test_match_artist_new_and_by_mbid(session: Session) -> None:
    mbid = uuid.uuid4()
    nina = artist(session, "Nina Simone", mbid=mbid)
    session.flush()
    assert match_artist(session, "Aphex Twin").decision is Decision.new
    assert match_artist(session).decision is Decision.new
    result = match_artist(session, "Completely different text", mbid=mbid)
    assert result.match is nina
    assert result.candidates[0].method is MatchMethod.mbid
    # An unknown MBID falls through to the name.
    assert match_artist(session, "Nina Simone", mbid=uuid.uuid4()).match is nina


# Recordings


def test_match_recording_strips_remaster_and_feat(session: Session) -> None:
    beatles = artist(session, "The Beatles")
    sun = recording(session, "Here Comes the Sun", beatles, length_ms=185_000)
    result = match_recording(
        session, "Here Comes the Sun - Remastered 2009", artist="Beatles", length_ms=186_000
    )
    assert result.decision is Decision.auto_match
    assert result.match is sun

    nina = artist(session, "Nina Simone")
    sinnerman = recording(session, "Sinnerman", nina)
    result = match_recording(session, "Sinnerman (feat. Someone)", artist="Nina Simone ft. Someone")
    assert result.match is sinnerman


def test_match_recording_accents(session: Session) -> None:
    sigur_ros = artist(session, "Sigur Rós")
    song = recording(session, "Hoppípolla", sigur_ros)
    assert match_recording(session, "hoppipolla", artist="Sigur Ros").match is song


def test_match_recording_same_title_other_artist_is_new(session: Session) -> None:
    recording(session, "Sinnerman", artist(session, "Nina Simone"))
    result = match_recording(session, "Sinnerman", artist="Hozier")
    assert result.decision is Decision.new


def test_match_recording_picks_the_right_artist(session: Session) -> None:
    nina = recording(session, "Feeling Good", artist(session, "Nina Simone"))
    muse = recording(session, "Feeling Good", artist(session, "Muse"))
    assert match_recording(session, "Feeling Good", artist="Muse").match is muse
    assert match_recording(session, "Feeling Good", artist="Nina Simone").match is nina


def test_match_recording_duration_tolerance(session: Session) -> None:
    song = recording(session, "Sinnerman", artist(session, "Nina Simone"), length_ms=622_000)
    near = match_recording(session, "Sinnerman", artist="Nina Simone", length_ms=615_000)
    assert near.match is song
    # A radio edit: same title and artist, far shorter.
    edit = match_recording(session, "Sinnerman", artist="Nina Simone", length_ms=240_000)
    assert edit.decision is Decision.new
    # Unknown duration on either side: not checked.
    assert match_recording(session, "Sinnerman", artist="Nina Simone").match is song


def test_match_recording_main_artist_of_a_credit(session: Session) -> None:
    fp = artist(session, "Floating Points")
    ps = artist(session, "Pharoah Sanders")
    lso = artist(session, "London Symphony Orchestra")
    movement = recording(session, "Promises: Movement 1", fp, ps, lso, joins=(", ", " & "))
    assert match_recording(session, "Promises - Movement 1", artist="Floating Points").match is (
        movement
    )
    full = "Floating Points, Pharoah Sanders & The London Symphony Orchestra"
    assert match_recording(session, "Promises: Movement 1", artist=full).match is movement


def test_match_recording_without_artist_needs_review(session: Session) -> None:
    song = recording(session, "Sinnerman", artist(session, "Nina Simone"))
    result = match_recording(session, "Sinnerman")
    assert result.decision is Decision.needs_review
    assert result.candidates[0].entity is song


def test_match_recording_duplicates_need_review(session: Session) -> None:
    nina = artist(session, "Nina Simone")
    recording(session, "Sinnerman", nina)
    recording(session, "Sinnerman", nina)
    result = match_recording(session, "Sinnerman", artist="Nina Simone")
    assert result.decision is Decision.needs_review
    assert [c.score for c in result.candidates] == [100.0, 100.0]


def test_match_recording_by_isrc_and_external_id(session: Session) -> None:
    song = recording(session, "Sinnerman", artist(session, "Nina Simone"))
    session.add(
        ExternalId(
            entity_type=EntityType.recording, entity_id=song.id, scheme="isrc", value="USPR36500123"
        )
    )
    session.add(
        ExternalId(entity_type=EntityType.recording, entity_id=song.id, scheme="deezer", value="42")
    )
    session.flush()
    # Exact identifiers win over contradicting text.
    result = match_recording(session, "Something else", artist="Nobody", isrc="us-pr3-65-00123")
    assert result.match is song
    assert result.candidates[0].method is MatchMethod.external_id
    assert match_recording(session, external_ids=[("deezer", "42")]).match is song
    assert match_recording(session, external_ids=[("deezer", "43")]).decision is Decision.new


def test_match_recording_by_mbid(session: Session) -> None:
    mbid = uuid.uuid4()
    song = recording(session, "Sinnerman", artist(session, "Nina Simone"), mbid=mbid)
    result = match_recording(session, mbid=mbid, isrc="XX0000000000")
    assert result.match is song
    assert result.candidates[0].method is MatchMethod.mbid


# Release groups


def test_match_release_group(session: Session) -> None:
    beatles = artist(session, "The Beatles")
    abbey_road = release_group(session, "Abbey Road", beatles)
    release_group(session, "Abbey Road", artist(session, "George Benson"))
    result = match_release_group(session, "Abbey Road (Remastered)", artist="Beatles")
    assert result.match is abbey_road
    without_artist = match_release_group(session, "Abbey Road")
    assert without_artist.decision is Decision.needs_review


def test_match_release_group_by_ids(session: Session) -> None:
    nina = artist(session, "Nina Simone")
    mbid, release_mbid = uuid.uuid4(), uuid.uuid4()
    pastel = release_group(
        session, "Pastel Blues", nina, mbid=mbid, release_mbid=release_mbid, barcode="0680899009720"
    )
    assert match_release_group(session, mbid=mbid).match is pastel
    assert match_release_group(session, release_mbid=release_mbid).match is pastel
    # UPC and EAN-13 of the same barcode.
    by_barcode = match_release_group(session, "Other", barcode="680899009720")
    assert by_barcode.match is pastel
    assert by_barcode.candidates[0].method is MatchMethod.external_id
    session.add(
        ExternalId(
            entity_type=EntityType.release_group,
            entity_id=pastel.id,
            scheme="bandcamp",
            value="https://nina.bandcamp.com/album/pastel-blues",
        )
    )
    session.flush()
    bandcamp = [("bandcamp", "https://nina.bandcamp.com/album/pastel-blues")]
    assert match_release_group(session, external_ids=bandcamp).match is pastel
