"""Fuzzy search over artists, release groups and recordings, for autocomplete.

Query and names/titles are compared normalised by ``ep_normalize`` (the query through
``ep.matching.sql.trigram_queries``, names and titles in their generated
``*_normalized`` columns): lowercase, unaccented, punctuation as spaces. A
value is a hit when it starts with the query, a word in it starts with the query (so
that short prefixes find something: "bjo" -> "Björk", "sim" -> "Nina Simone"), or the
query's trigram word similarity to it is at least ``SEARCH_MIN_SIMILARITY`` (finds a query
inside a longer value, tolerates small typos). All three are served by the GIN trigram
indexes (``LIKE`` and ``<%``, see ``ep.matching.sql``).

Rank: word similarity + overall similarity (closer to the whole value) + a bonus for a
prefix match (more for the start of the value than for the start of a later word).

A query of several words is also matched as "artist title" against release groups and
recordings, so that "nina simone sinnerman" finds the recording: for each way of
splitting the query into an artist part and a title part (in either order, or the
whole query as the artist), a row is a hit if the artist part is word-similar to its
artist credit and the title part to its title. Each split is a separate index-backed
select, so that Postgres starts from whichever side is more selective. Such a hit ranks
by word similarity + similarity of the query to "<artist credit> <title>" or "<title>
<artist credit>", without prefix bonus.

Release groups and recordings are loaded with their artist credits (for the hit's
subtitle).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, case, func, union
from sqlalchemy.orm import Mapped, QueryableAttribute, contains_eager, joinedload
from sqlmodel import Session, col, select
from sqlmodel.sql.expression import SelectOfScalar

from ep.matching.sql import trigram_queries, word_similar
from ep.models import Artist, ArtistCredit, Base, EntityType, Recording, ReleaseGroup, rel

# Word similarity (0-1) a value needs to be a hit when no word starts with the query.
SEARCH_MIN_SIMILARITY = 0.5
PREFIX_BONUS = 1.0
WORD_PREFIX_BONUS = 0.5
# Longer queries are not split into artist and title (2 * words - 1 selects).
COMBINED_MAX_WORDS = 6

# Searched (normalised) column, display column (to order ties) and, for titles, the
# artist credit relationship (joined and loaded).
_COLUMNS: dict[
    EntityType, tuple[type[Base], Mapped[str], Mapped[str], QueryableAttribute[Any] | None]
] = {
    EntityType.artist: (Artist, col(Artist.name_normalized), col(Artist.name), None),
    EntityType.release_group: (
        ReleaseGroup,
        col(ReleaseGroup.title_normalized),
        col(ReleaseGroup.title),
        rel(ReleaseGroup.artist_credit),
    ),
    EntityType.recording: (
        Recording,
        col(Recording.title_normalized),
        col(Recording.title),
        rel(Recording.artist_credit),
    ),
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
    [normalized] = trigram_queries(session, SEARCH_MIN_SIMILARITY, query)
    words = normalized.split()
    if not words:
        return []
    best: dict[tuple[EntityType, uuid.UUID], SearchHit] = {}
    for entity_type in dict.fromkeys(types):
        hits = _search_type(session, entity_type, words, limit)
        hits += _search_combined(session, entity_type, words, limit)
        for hit in hits:
            key = (hit.type, hit.entity.id)
            if key not in best or hit.score > best[key].score:
                best[key] = hit
    return sorted(best.values(), key=lambda hit: hit.score, reverse=True)[:limit]


def _hit_and_rank(
    query: str, target: Mapped[str]
) -> tuple[ColumnElement[bool], ColumnElement[float]]:
    # The normalised query has no LIKE wildcards ("%", "_") or escape characters left.
    starts = target.startswith(query)
    word_starts = target.contains(" " + query)
    hit = starts | word_starts | word_similar(query, target)
    rank: ColumnElement[float] = (
        func.word_similarity(query, target)
        + func.similarity(query, target)
        + case((starts, PREFIX_BONUS), (word_starts, WORD_PREFIX_BONUS), else_=0.0)
    )
    return hit, rank


def _search_type(
    session: Session, entity_type: EntityType, words: list[str], limit: int
) -> list[SearchHit]:
    model, target, display, credit = _COLUMNS[entity_type]
    hit, rank = _hit_and_rank(" ".join(words), target)
    statement = select(model, rank).where(hit).order_by(rank.desc(), display).limit(limit)
    if credit is not None:
        statement = statement.options(joinedload(credit))
    rows = session.exec(statement)
    return [SearchHit(entity_type, entity, float(score)) for entity, score in rows]


def _search_combined(
    session: Session, entity_type: EntityType, words: list[str], limit: int
) -> list[SearchHit]:
    model, title, display, credit_relationship = _COLUMNS[entity_type]
    if credit_relationship is None or not 2 <= len(words) <= COMBINED_MAX_WORDS:
        return []
    credit = col(ArtistCredit.name_normalized)
    splits: list[SelectOfScalar[uuid.UUID]] = []
    for i in range(1, len(words) + 1):
        for artist_part, title_part in (words[:i], words[i:]), (words[i:], words[:i]):
            if not artist_part:
                continue
            split = (
                select(col(model.id))
                .join(credit_relationship)
                .where(word_similar(" ".join(artist_part), credit))
            )
            if title_part:
                split = split.where(word_similar(" ".join(title_part), title))
            splits.append(split)

    query = " ".join(words)
    _, rank = _hit_and_rank(query, title)
    for combined in func.concat_ws(" ", credit, title), func.concat_ws(" ", title, credit):
        rank = func.greatest(
            rank, func.word_similarity(query, combined) + func.similarity(query, combined)
        )
    rows = session.exec(
        select(model, rank)
        .join(credit_relationship)
        .options(contains_eager(credit_relationship))
        .where(col(model.id).in_(union(*splits)))
        .order_by(rank.desc(), display)
        .limit(limit)
    )
    return [SearchHit(entity_type, entity, float(score)) for entity, score in rows]
