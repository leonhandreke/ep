"""Matching artists, release groups and recordings against the local library.

Each entry point takes whatever the caller knows (names, MBIDs, external IDs, a
duration) and tries, in order:

1. MBID: a local row with that MBID is an automatic match.
2. External IDs (ISRC, barcode, Deezer, ...): one row with any of them is an automatic
   match; several need review.
3. Fuzzy: candidates by Postgres trigram word similarity of the normalised query to
   the normalised title or name (``ep.matching.sql``, index-backed), re-scored with
   rapidfuzz on normalised strings (``ep.matching.normalize``), which also strips
   featured artists and edition suffixes. A recording or release group scores
   ``title score * artist score / 100``, so the same title by another artist scores
   low. Recordings whose duration differs by more than ``DURATION_TOLERANCE_MS`` are
   dropped when both durations are known. Titles with different numbers ("Movement 1"
   and "Movement 2", "Part II" and "Part III") are never matched automatically.

When the caller knows an MBID, rows with a *different* MBID are never candidates in
steps 2 and 3: they are known to be other entities.

Candidates are rows whose value contains (most of) the query: word similarity of the
query to the best-matching part of the value. The other way round (a short value
within a long query) is not indexable and not needed: a value covering less than
about 40% of the query cannot reach ``REVIEW_SCORE`` with rapidfuzz, and one covering
more has a word similarity above ``CANDIDATE_MIN_SIMILARITY`` anyway.

The result lists candidates, best first, and a decision: ``auto_match`` (use
``result.match``), ``needs_review`` (ask the user) or ``new`` (create an entity).
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from rapidfuzz import fuzz
from sqlalchemy import ColumnElement, and_, func, or_, true
from sqlalchemy.orm import Mapped, QueryableAttribute, selectinload
from sqlalchemy.orm.interfaces import ORMOption
from sqlmodel import Session, col, select

from ep.matching.normalize import normalize_artist, normalize_title
from ep.matching.sql import set_word_similarity_threshold, word_similar
from ep.metadata.musicbrainz import normalize_isrc
from ep.models import (
    Artist,
    ArtistCredit,
    ArtistCreditName,
    Base,
    EntityType,
    ExternalId,
    Recording,
    ReleaseGroup,
)

# Scores are 0-100.
# At least this, and AUTO_MATCH_MARGIN ahead of the runner-up: match without asking.
AUTO_MATCH_SCORE = 90.0
AUTO_MATCH_MARGIN = 5.0
# At least this: ask the user. Below: treat as new.
REVIEW_SCORE = 60.0
# Candidates scoring lower are not returned at all.
MIN_CANDIDATE_SCORE = 40.0
# Candidates returned, best first.
MAX_CANDIDATES = 10
# Two known durations further apart than this are different recordings.
DURATION_TOLERANCE_MS = 10_000
# Rows fetched by trigram word similarity (0-1, above this) before re-scoring with
# rapidfuzz.
CANDIDATE_MIN_SIMILARITY = 0.3
CANDIDATE_LIMIT = 50


class Decision(StrEnum):
    auto_match = "auto_match"
    needs_review = "needs_review"
    new = "new"


class MatchMethod(StrEnum):
    mbid = "mbid"
    external_id = "external_id"
    fuzzy = "fuzzy"


@dataclass(frozen=True)
class Candidate[T: Base]:
    entity: T
    score: float
    method: MatchMethod


@dataclass(frozen=True)
class MatchResult[T: Base]:
    decision: Decision
    # Best first.
    candidates: list[Candidate[T]]

    @property
    def match(self) -> T | None:
        """The matched entity if the decision is ``auto_match``."""
        if self.decision is Decision.auto_match:
            return self.candidates[0].entity
        return None


ExternalIds = Iterable[tuple[str, str]]
"""(scheme, value) pairs, e.g. ``[("isrc", "USPR36500123"), ("deezer", "3135556")]``."""


def text_score(a: str, b: str) -> float:
    """Similarity of two normalised strings, 0-100; word order does not matter."""
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))


# Arabic numbers and Roman numerals up to 39 (after normalisation: lowercase words).
_NUMBER = re.compile(r"^(?:\d+|(?=[ivx])x{0,3}(?:ix|iv|v?i{0,3}))$")


def numbers(normalized: str) -> list[str]:
    """The numbers in a normalised title, in order: "part ii 2" -> ["ii", "2"]."""
    return [word for word in normalized.split() if _NUMBER.match(word)]


def credit_score(artist: str, credit: ArtistCredit, artist_names: Sequence[str] = ()) -> float:
    """How well artist text matches a credit: whole, or any one artist on either side.

    "Floating Points" matches "Floating Points, Pharoah Sanders & The London Symphony
    Orchestra" fully: radio tracklists and file tags often name only the main artist.
    The other way round, the query's own ``artist_names`` (the artists of the credit,
    when the caller knows them) are compared too: credit text is not split, since
    "Earth, Wind & Fire" is one artist.
    """
    queries = {normalize_artist(name) for name in (artist, *artist_names)} - {""}
    names = [credit.name]
    for part in credit.names:
        names += [part.credited_name, part.artist.name]
    return max(
        text_score(query, normalize_artist(name)) for query in queries or {""} for name in names
    )


def decide[T: Base](candidates: list[Candidate[T]], *, allow_auto: bool = True) -> Decision:
    if not candidates or candidates[0].score < REVIEW_SCORE:
        return Decision.new
    best = candidates[0].score
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    if allow_auto and best >= AUTO_MATCH_SCORE and best - runner_up >= AUTO_MATCH_MARGIN:
        return Decision.auto_match
    return Decision.needs_review


def _result[T: Base](
    scored: Iterable[tuple[T, float]], *, allow_auto: bool = True
) -> MatchResult[T]:
    candidates = sorted(
        (
            Candidate(entity, score, MatchMethod.fuzzy)
            for entity, score in scored
            if score >= MIN_CANDIDATE_SCORE
        ),
        key=lambda candidate: candidate.score,
        reverse=True,
    )[:MAX_CANDIDATES]
    return MatchResult(decide(candidates, allow_auto=allow_auto), candidates)


def _exact[T: Base](rows: Sequence[T], method: MatchMethod) -> MatchResult[T] | None:
    if not rows:
        return None
    decision = Decision.auto_match if len(rows) == 1 else Decision.needs_review
    return MatchResult(decision, [Candidate(row, 100.0, method) for row in rows])


def _by_mbid[T: Base](
    session: Session, model: type[T], column: Mapped[uuid.UUID | None], mbid: uuid.UUID
) -> MatchResult[T] | None:
    rows = session.exec(select(model).where(column == mbid)).all()
    return _exact(rows, MatchMethod.mbid)


def _mbid_compatible(
    column: Mapped[uuid.UUID | None], mbid: uuid.UUID | None
) -> ColumnElement[bool]:
    """SQL: the row has no MBID or the given one (true if no MBID is given)."""
    if mbid is None:
        return true()
    return or_(col(column).is_(None), col(column) == mbid)


def _by_external_ids[T: Base](
    session: Session,
    model: type[T],
    entity_type: EntityType,
    external_ids: ExternalIds,
    compatible: ColumnElement[bool],
) -> MatchResult[T] | None:
    pairs = [
        (scheme, normalize_isrc(value) if scheme == "isrc" else value.strip())
        for scheme, value in external_ids
    ]
    if not pairs:
        return None
    entity_ids = select(ExternalId.entity_id).where(
        ExternalId.entity_type == entity_type,
        or_(*(and_(col(ExternalId.scheme) == s, col(ExternalId.value) == v) for s, v in pairs)),
    )
    rows = session.exec(select(model).where(col(model.id).in_(entity_ids), compatible)).all()
    return _exact(rows, MatchMethod.external_id)


def _similarity(query: str, column: Mapped[str]) -> ColumnElement[float]:
    """Trigram similarity of a normalised query and a normalised column, 0-1, either way
    round, for ranking candidates.

    ``word_similarity`` finds a query within a longer value ("here comes the sun" in
    "Here Comes the Sun (Remastered 2009)") and the other way round.
    """
    return func.greatest(func.word_similarity(query, column), func.word_similarity(column, query))


def _candidate(query: str, column: Mapped[str]) -> ColumnElement[bool]:
    """SQL, index-backed: the query is within the normalised column's value (word
    similarity above ``CANDIDATE_MIN_SIMILARITY``; call ``set_word_similarity_threshold``
    first)."""
    return word_similar(query, column)


def _relationship(attribute: object) -> QueryableAttribute[Any]:
    # SQLModel types relationship attributes as their targets.
    return cast(QueryableAttribute[Any], attribute)


def _with_credit(relationship: ArtistCredit) -> ORMOption:
    """Load option: the artist credit with its artists, for ``credit_score``."""
    return (
        selectinload(_relationship(relationship))
        .selectinload(_relationship(ArtistCredit.names))
        .selectinload(_relationship(ArtistCreditName.artist))
    )


def match_artist(
    session: Session,
    name: str | None = None,
    *,
    mbid: uuid.UUID | None = None,
    external_ids: ExternalIds = (),
) -> MatchResult[Artist]:
    """Match an artist by MBID, external IDs, then name (or sort name)."""
    if mbid and (result := _by_mbid(session, Artist, col(Artist.mbid), mbid)):
        return result
    compatible = _mbid_compatible(col(Artist.mbid), mbid)
    if result := _by_external_ids(session, Artist, EntityType.artist, external_ids, compatible):
        return result
    if not name or not (query := normalize_artist(name)):
        return MatchResult(Decision.new, [])
    similarity = func.greatest(
        _similarity(query, col(Artist.name_normalized)),
        _similarity(query, col(Artist.sort_name_normalized)),
    )
    set_word_similarity_threshold(session, CANDIDATE_MIN_SIMILARITY)
    artists = session.exec(
        select(Artist)
        .where(
            _candidate(query, col(Artist.name_normalized))
            | _candidate(query, col(Artist.sort_name_normalized)),
            compatible,
        )
        .order_by(similarity.desc())
        .limit(CANDIDATE_LIMIT)
    )
    return _result(
        (
            artist,
            max(
                text_score(query, normalize_artist(artist.name)),
                text_score(query, normalize_artist(artist.sort_name)),
            ),
        )
        for artist in artists
    )


def match_release_group(
    session: Session,
    title: str | None = None,
    *,
    artist: str | None = None,
    artist_names: Sequence[str] = (),
    mbid: uuid.UUID | None = None,
    release_mbid: uuid.UUID | None = None,
    barcode: str | None = None,
    external_ids: ExternalIds = (),
) -> MatchResult[ReleaseGroup]:
    """Match a release group by MBID (its own or its release's), barcode and external IDs,
    then title and artist credit text. Without artist text, fuzzy matches need review.
    ``artist_names``: the individual artists of the credit, if known (see ``credit_score``).
    """
    if mbid and (result := _by_mbid(session, ReleaseGroup, col(ReleaseGroup.mbid), mbid)):
        return result
    if release_mbid and (
        result := _by_mbid(session, ReleaseGroup, col(ReleaseGroup.release_mbid), release_mbid)
    ):
        return result
    compatible = _mbid_compatible(col(ReleaseGroup.mbid), mbid)
    if barcode and (digits := barcode.strip().lstrip("0")):
        # A UPC-A is the same barcode as the EAN-13 with a leading zero.
        rows = session.exec(
            select(ReleaseGroup).where(func.ltrim(ReleaseGroup.barcode, "0") == digits, compatible)
        ).all()
        if result := _exact(rows, MatchMethod.external_id):
            return result
    if result := _by_external_ids(
        session, ReleaseGroup, EntityType.release_group, external_ids, compatible
    ):
        return result
    if not title:
        return MatchResult(Decision.new, [])
    query = normalize_title(title)
    rows = _fuzzy_candidates(
        session,
        ReleaseGroup,
        col(ReleaseGroup.title_normalized),
        col(ReleaseGroup.artist_credit_id),
        _with_credit(ReleaseGroup.artist_credit),
        query,
        artist,
        compatible,
    )
    return _result(
        (
            (rg, _title_artist_score(query, rg.title, artist, artist_names, rg.artist_credit))
            for rg in rows
        ),
        allow_auto=artist is not None,
    )


def match_recording(
    session: Session,
    title: str | None = None,
    *,
    artist: str | None = None,
    artist_names: Sequence[str] = (),
    length_ms: int | None = None,
    mbid: uuid.UUID | None = None,
    isrc: str | None = None,
    external_ids: ExternalIds = (),
) -> MatchResult[Recording]:
    """Match a recording by MBID, ISRC and external IDs, then title, artist credit text and
    duration. Without artist text, fuzzy matches need review. ``artist_names``: the
    individual artists of the credit, if known (see ``credit_score``).
    """
    if mbid and (result := _by_mbid(session, Recording, col(Recording.mbid), mbid)):
        return result
    ids = [*external_ids, *([("isrc", isrc)] if isrc else [])]
    compatible = _mbid_compatible(col(Recording.mbid), mbid)
    if result := _by_external_ids(session, Recording, EntityType.recording, ids, compatible):
        return result
    if not title:
        return MatchResult(Decision.new, [])
    query = normalize_title(title)
    rows = _fuzzy_candidates(
        session,
        Recording,
        col(Recording.title_normalized),
        col(Recording.artist_credit_id),
        _with_credit(Recording.artist_credit),
        query,
        artist,
        compatible,
    )
    return _result(
        (
            (
                recording,
                _title_artist_score(
                    query, recording.title, artist, artist_names, recording.artist_credit
                ),
            )
            for recording in rows
            if not _durations_differ(length_ms, recording.length_ms)
        ),
        allow_auto=artist is not None,
    )


def _fuzzy_candidates[T: Base](
    session: Session,
    model: type[T],
    title_column: Mapped[str],
    credit_column: Mapped[uuid.UUID],
    load_credit: ORMOption,
    query: str,
    artist: str | None,
    compatible: ColumnElement[bool],
) -> Sequence[T]:
    """Rows with a similar title, ranked by title and (if given) artist credit similarity.

    Ties (a common title within many longer ones) go to the closest whole title, so that
    "Love" is among the candidates for "love" however many "Love Song"s there are.
    """
    rank = _similarity(query, title_column)
    set_word_similarity_threshold(session, CANDIDATE_MIN_SIMILARITY)
    statement = (
        select(model).where(_candidate(query, title_column), compatible).options(load_credit)
    )
    if artist and (artist_query := normalize_artist(artist)):
        statement = statement.join(ArtistCredit, credit_column == ArtistCredit.id)
        rank = rank + _similarity(artist_query, col(ArtistCredit.name_normalized))
    statement = statement.order_by(rank.desc(), func.similarity(query, title_column).desc())
    return session.exec(statement.limit(CANDIDATE_LIMIT)).all()


def _title_artist_score(
    query: str,
    title: str,
    artist: str | None,
    artist_names: Sequence[str],
    credit: ArtistCredit,
) -> float:
    normalized = normalize_title(title)
    score = text_score(query, normalized)
    if numbers(query) != numbers(normalized):
        # "Movement 1" and "Movement 2" differ in one character: ask, never auto-match.
        score = min(score, AUTO_MATCH_SCORE - 1)
    if artist:
        score = score * credit_score(artist, credit, artist_names) / 100
    return score


def _durations_differ(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and abs(a - b) > DURATION_TOLERANCE_MS
