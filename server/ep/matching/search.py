"""Fuzzy search over artists, release groups and recordings, for autocomplete.

Matches the folded (lowercase, unaccented) name or title against the folded query by
trigram word similarity, which finds a query inside a longer value and tolerates small
typos, or by prefix: the whole value or a word in it starting with the query, so
that short prefixes find something ("bjo" -> "Björk", "sim" -> "Nina Simone").

Rank: word similarity + overall similarity (closer to the whole value) + a bonus for a
prefix match (more for the start of the value than for the start of a later word).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import ColumnElement, case, func
from sqlalchemy.orm import Mapped
from sqlmodel import Session, col, select

from ep.matching.match import folded
from ep.matching.normalize import unaccent
from ep.models import Artist, Base, EntityType, Recording, ReleaseGroup

# Word similarity (0-1) a value needs to be a hit when no word starts with the query.
SEARCH_MIN_SIMILARITY = 0.5
PREFIX_BONUS = 1.0
WORD_PREFIX_BONUS = 0.5

_COLUMNS: dict[EntityType, tuple[type[Base], Mapped[str]]] = {
    EntityType.artist: (Artist, col(Artist.name)),
    EntityType.release_group: (ReleaseGroup, col(ReleaseGroup.title)),
    EntityType.recording: (Recording, col(Recording.title)),
}


@dataclass(frozen=True)
class SearchHit:
    type: EntityType
    entity: Base
    score: float


def search(
    session: Session, query: str, types: Iterable[EntityType], limit: int
) -> list[SearchHit]:
    """The best ``limit`` hits across ``types``, best first."""
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
    model, column = _COLUMNS[entity_type]
    target = folded(column)
    word_similarity = func.word_similarity(query, target)
    starts = target.startswith(query, autoescape=True)
    word_starts = target.contains(" " + query, autoescape=True)
    rank: ColumnElement[float] = (
        word_similarity
        + func.similarity(query, target)
        + case((starts, PREFIX_BONUS), (word_starts, WORD_PREFIX_BONUS), else_=0.0)
    )
    rows = session.exec(
        select(model, rank)
        .where(starts | word_starts | (word_similarity >= SEARCH_MIN_SIMILARITY))
        .order_by(rank.desc(), column)
        .limit(limit)
    )
    return [SearchHit(entity_type, entity, float(score)) for entity, score in rows]
