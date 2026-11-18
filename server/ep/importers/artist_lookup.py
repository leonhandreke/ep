"""Finding the MusicBrainz artist of an incoming artist that came without an MBID.

Artist names alone say little: many artists share a name, and look-alike names are
other artists. So the matcher (``ep.matching.match_artist``) is strict about names, and
the import framework first asks MusicBrainz who the artist is: with an MBID the library
row is found by MBID (or a row without MBID by its name, which gains the MBID), and a
homonym with another MBID is never taken for it.

``find_mb_artist`` asks, in order, until one answers:

1. A release search for a title the artist is credited on, with the artist's name
   (``release:"…" AND artist:"…"``); then a recording search likewise. Hits of
   MusicBrainz relevance ``MIN_SEARCH_SCORE`` or more credit artists; those whose name
   (as credited, the artist's name or sort name) fits the incoming name are the
   answer: equal after ``normalize_artist``, or within the matcher's edit budget
   (``names_fit``; the title confirms it). One artist is the answer; several (two
   artists of that name with a release of that title) leave the artist unresolved.
2. An artist search for the name: without a title to confirm it, a hit counts only if
   its name, sort name or an alias is *equal* after normalisation, and only a single
   such hit is the answer (one artist of that name in MusicBrainz).

Nothing found is a normal outcome: the artist is matched by name. MusicBrainz errors
are raised (``MusicBrainzError``) for the caller to log and go on without.
"""

import uuid
from collections.abc import Iterable, Iterator, Sequence

from ep.matching.match import names_fit
from ep.matching.normalize import normalize_artist
from ep.metadata.musicbrainz import MBArtist, MBArtistCreditPart, MusicBrainz, lucene_query

# MusicBrainz search hits below this relevance (0-100) are not considered.
MIN_SEARCH_SCORE = 90
# Hits asked for per search.
SEARCH_LIMIT = 10


def find_mb_artist(
    musicbrainz: MusicBrainz,
    name: str,
    *,
    release_titles: Sequence[str] = (),
    recording_titles: Sequence[str] = (),
) -> MBArtist | None:
    """The MusicBrainz artist of this name, confirmed by a release or recording title it
    is credited on (the first of each is searched), or the only artist of exactly this
    name; None if unsure. See the module docstring."""
    query = normalize_artist(name)
    if not query:
        return None
    for credits in _context_credits(musicbrainz, name, release_titles, recording_titles):
        credited = _credited(query, credits)
        if len(credited) == 1:
            return credited[0]
        if credited:
            return None  # homonyms with a release of that title
    hits = musicbrainz.search_artists(lucene_query(artist=name), limit=SEARCH_LIMIT)
    named = {
        hit.id: hit
        for hit in hits
        if (hit.score or 0) >= MIN_SEARCH_SCORE
        and query
        in {
            normalize_artist(text)
            for text in (
                hit.name,
                hit.sort_name,
                *(a.name for a in hit.aliases),
                *(a.sort_name for a in hit.aliases if a.sort_name),
            )
        }
    }
    return next(iter(named.values())) if len(named) == 1 else None


def _context_credits(
    musicbrainz: MusicBrainz,
    name: str,
    release_titles: Sequence[str],
    recording_titles: Sequence[str],
) -> Iterator[list[list[MBArtistCreditPart]]]:
    """The credits of good release hits, then of good recording hits, searched lazily
    (the next search only if the previous one did not answer)."""
    for title in release_titles[:1]:
        query = lucene_query(release=title, artist=name)
        hits = musicbrainz.search_releases(query, limit=SEARCH_LIMIT)
        yield [hit.artist_credit for hit in hits if (hit.score or 0) >= MIN_SEARCH_SCORE]
    for title in recording_titles[:1]:
        query = lucene_query(recording=title, artist=name)
        hits = musicbrainz.search_recordings(query, limit=SEARCH_LIMIT)
        yield [hit.artist_credit for hit in hits if (hit.score or 0) >= MIN_SEARCH_SCORE]


def _credited(query: str, credits: Iterable[list[MBArtistCreditPart]]) -> list[MBArtist]:
    """The distinct artists of these credits whose name fits the query."""
    found: dict[uuid.UUID, MBArtist] = {}
    for credit in credits:
        for part in credit:
            artist = part.artist
            names = (part.name, artist.name, artist.sort_name)
            if any(names_fit(query, normalize_artist(text)) for text in names):
                found.setdefault(artist.id, artist)
    return list(found.values())
