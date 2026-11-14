"""``ep_normalize`` (SQL, migration 0004) agrees with ``ep.matching.normalize.normalize``.

The database normalises stored names and titles (generated ``*_normalized`` columns),
Python normalises queries and rapidfuzz inputs; both must give the same text.
"""

import unicodedata

import pytest
from sqlalchemy import text
from sqlmodel import Session

from ep.credits import CreditPart, get_or_create_artist_credit
from ep.matching.normalize import normalize
from ep.models import Artist, Recording

EXAMPLES = [
    "Jóga",
    "Hyperballad!",
    "Here Comes the Sun (Remastered 2009)",
    "Hey Jude – Remastered 2015",
    "Sinnerman [ft. Someone] (Remastered)",
    "Don't Stop Me Now",
    "Rock & Roll",
    "Motörhead / Ace of Spades",
    "  ÉMILIE   Simon  ",
    "Søvn Æon Œuvre Straße Þór Łódź Đorđe Sigurðsson Istanbul İstanbul",
    "Dvořák: Symphony No. 9 “From the New World”",
    "école",  # decomposed accent
    "ﬁn de siècle ½ x²",  # ligature, fraction, superscript
    "AC/DC under_score",
    "Ελληνικά Пётр Ильич Чайковский",
    "ガギ ｶﾞｷﾞ 坂本龍一 방탄소년단",
    "Tiësto feat. Nelly Furtado",
    "",
    "!!!",
]


def sql_normalize(session: Session, values: list[str]) -> dict[str, str]:
    """``ep_normalize`` of each value that is assigned in Postgres' Unicode version."""
    rows = session.connection().execute(
        text(
            "SELECT value, ep_normalize(value) FROM unnest(CAST(:values AS text[])) AS value"
            " WHERE unicode_assigned(value)"
        ),
        {"values": values},
    )
    return {value: normalized for value, normalized in rows}


def test_examples_agree(session: Session) -> None:
    normalized = sql_normalize(session, EXAMPLES)
    assert normalized.keys() == set(EXAMPLES)
    for value, sql in normalized.items():
        assert sql == normalize(value), value


def _compared(char: str) -> bool:
    if unicodedata.category(char) in ("Cs", "Co", "Cn"):
        return False
    # Postgres counts only marks with the Unicode "Alphabetic" property (Indic vowel
    # signs, but not Hebrew points or the Devanagari nukta) as word characters; Python
    # cannot tell them apart and keeps all. Accents (U+0300-U+036F) are removed by both.
    decomposed = unicodedata.normalize("NFKD", char)
    if any(unicodedata.category(c)[0] == "M" and not "̀" <= c <= "ͯ" for c in decomposed):
        return False
    # Negative circled and squared Latin letters: symbols that Unicode counts as
    # alphabetic.
    return not "\U0001f130" <= char <= "\U0001f189"


def test_every_character_agrees(session: Session) -> None:
    # Each character between two letters, so that it is not stripped as an edge space.
    values = [f"a{chr(code)}b" for code in range(0x20, 0x30000) if _compared(chr(code))]
    normalized = sql_normalize(session, values)
    assert len(normalized) > 140_000  # all but characters new in Python's Unicode version
    differing = {value: sql for value, sql in normalized.items() if sql != normalize(value)}
    assert differing == {}


@pytest.mark.parametrize("name", ["Björk", "The Beatles", "Simon & Garfunkel", "Sigur Rós"])
def test_generated_columns(session: Session, name: str) -> None:
    artist = Artist(name=name, sort_name=f"{name}, sort")
    session.add(artist)
    credit = get_or_create_artist_credit(session, [CreditPart(artist, name)])
    recording = Recording(title=f"{name} (Live)", artist_credit_id=credit.id)
    session.add(recording)
    session.flush()
    assert artist.name_normalized == normalize(name)
    assert artist.sort_name_normalized == normalize(f"{name}, sort")
    assert credit.name_normalized == normalize(name)
    assert recording.title_normalized == normalize(f"{name} (Live)")
    recording.title = "Renamed"
    session.flush()
    assert recording.title_normalized == "renamed"
