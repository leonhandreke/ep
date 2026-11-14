"""Matching against the local library (``match``) and fuzzy search (``search``)."""

from ep.matching.match import (
    Candidate,
    Decision,
    MatchMethod,
    MatchResult,
    match_artist,
    match_recording,
    match_release_group,
)

__all__ = [
    "Candidate",
    "Decision",
    "MatchMethod",
    "MatchResult",
    "match_artist",
    "match_recording",
    "match_release_group",
]
