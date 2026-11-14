"""Fuzzy search over artists, release groups and recordings, for autocomplete.

Matches the folded (lowercase, unaccented) name or title against the folded query by
trigram word similarity, which finds a query inside a longer value and tolerates small
typos, or by prefix: the whole value or a word in it starting with the query, so
that short prefixes find something ("bjo" -> "Björk", "sim" -> "Nina Simone").

Rank: word similarity + overall similarity (closer to the whole value) + a bonus for a
prefix match (more for the start of the value than for the start of a later word).

A query of several words is also matched against "<artist credit> <title>" and
"<title> <artist credit>" of release groups and recordings, so that "nina simone
sinnerman" finds the recording. Such a hit ranks by word similarity + similarity of that
combined text, without prefix bonus.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import ColumnElement, case, func
from sqlalchemy.orm import Mapped
from sqlmodel import Session, col, select

from ep.matching.match import folded
from ep.matching.normalize import unaccent
from ep.models import Artist, ArtistCredit, Base, EntityType, Recording, ReleaseGroup

# Word similarity (0-1) a value needs to be a hit when no word starts with the query.
SEARCH_MIN_SIMILARITY = 0.5
PREFIX_BONUS = 1.0
WORD_PREFIX_BONUS = 0.5

# Searched column and, for titles, the artist credit column to join on.
_COLUMNS: dict[EntityType, tuple[type[Base], Mapped[str], Mapped[uuid.UUID] | None]] = {
    EntityType.artist: (Artist, col(Artist.name), None),
    EntityType.release_group: (
        ReleaseGroup,
        col(ReleaseGroup.title),
        col(ReleaseGroup.artist_credit_id),
    ),
    EntityType.recording: (Recording, col(Recording.title), col(Recording.artist_credit_id)),
}
SEARCH_TYPES = tuple(_COLUMNS)


@dataclass(frozen=True)
class SearchHit:
    type: EntityType
    entity: Base
    score: float


def search(
    session: Session, query: str, types: Iterable[EntityType] = SEARCH_TYPES, limit: int = 20
) -> list[SearchHit]:
    """The best ``limit`` hits across ``types`` (of ``SEARCH_TYPES``), best first."""
    folded_query = " ".join(unaccent(query).split())
    if not folded_query:
        return []
    hits = [
        hit
        for entity_type in dict.fromkeys(types)
        for hit in _search_type(session, entity_type, folded_query, limit)
    ]
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:limit]


def _search_type(
    session: Session, entity_type: EntityType, query: str, limit: int
) -> list[SearchHit]:
    model, column, credit_column = _COLUMNS[entity_type]
    target = folded(column)
    word_similarity = func.word_similarity(query, target)
    starts = target.startswith(query, autoescape=True)
    word_starts = target.contains(" " + query, autoescape=True)
    rank: ColumnElement[float] = (
        word_similarity
        + func.similarity(query, target)
        + case((starts, PREFIX_BONUS), (word_starts, WORD_PREFIX_BONUS), else_=0.0)
    )
    hit = starts | word_starts | (word_similarity >= SEARCH_MIN_SIMILARITY)
    combine = credit_column is not None and " " in query
    if combine:
        credit = col(ArtistCredit.name)
        combined_ranks = []
        for combined in (
            folded(func.concat_ws(" ", credit, column)),
            folded(func.concat_ws(" ", column, credit)),
        ):
            combined_similarity = func.word_similarity(query, combined)
            hit |= combined_similarity >= SEARCH_MIN_SIMILARITY
            combined_ranks.append(combined_similarity + func.similarity(query, combined))
        rank = func.greatest(rank, *combined_ranks)
    statement = select(model, rank)
    if combine:
        statement = statement.join(ArtistCredit, credit_column == ArtistCredit.id)
    rows = session.exec(statement.where(hit).order_by(rank.desc(), column).limit(limit))
    return [SearchHit(entity_type, entity, float(score)) for entity, score in rows]
