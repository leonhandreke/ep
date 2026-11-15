"""Resolving an incoming release to a MusicBrainz release, for every importer.

An importer describes what it found as an ``IncomingRelease`` (tracks with positions
and lengths) and asks MusicBrainz which release that is:

- ``search_release``: a release search (title, artist, track count); up to
  ``MAX_RELEASE_LOOKUPS`` candidates are fetched in full and the first whose tracks fit
  (``align`` and ``durations_fit``) is used. Candidates that are otherwise exact but do
  not fit raise an ``uncertain_metadata`` issue. Nothing found is a normal outcome: the
  release is imported as entered;
- ``first_fitting``: the first of some releases (from a URL or barcode lookup) whose
  tracks fit;
- ``use_musicbrainz``: takes a fitting release's metadata (titles, credits, MBIDs) into
  the incoming release.

Fitting means: every incoming track has a MusicBrainz track (by tagged track MBID,
recording MBID, else disc and position), no two the same, and lengths within the
matcher's tolerance where both are known. ``align_by_title`` instead pairs tracks by
title, for inputs whose order means nothing (a playlist).
"""

import uuid
from collections import Counter
from collections.abc import Callable, Iterable

from sqlmodel import col, select

from ep.importers.framework import ImportFailed, JobContext, answer_fits, mb_credit, release_label
from ep.importers.musicbrainz import release_group_type
from ep.importers.proposal import (
    SKIP,
    UNDECIDED,
    CandidateType,
    Choice,
    IncomingRelease,
    Issue,
    IssueCandidate,
    IssueKind,
    LogLevel,
    Proposal,
    Subject,
    issue_id,
)
from ep.matching.match import AUTO_MATCH_SCORE, DURATION_TOLERANCE_MS, text_score
from ep.matching.normalize import normalize_title
from ep.metadata.musicbrainz import (
    MBRelease,
    MBTrack,
    MusicBrainzError,
    MusicBrainzNotFound,
    lucene_query,
    render_artist_credit,
)
from ep.models import ReleaseGroup

# MusicBrainz search hits below this relevance (0-100) are not considered.
MIN_SEARCH_SCORE = 90
# Search candidates fetched in full to compare durations (one request each).
MAX_RELEASE_LOOKUPS = 3
# Candidates shown in an uncertain_metadata issue.
MAX_METADATA_CANDIDATES = 5


def skip_release(release: IncomingRelease) -> None:
    release.target = SKIP
    for track in release.tracks:
        track.target = SKIP


def search_release(
    ctx: JobContext,
    release: IncomingRelease,
    subject: Subject,
    *,
    artist: str | None,
    track_count: bool = True,
    fits: Callable[[IncomingRelease, MBRelease], bool] | None = None,
    entered: str = "tags",
) -> MBRelease | None:
    """The MusicBrainz release this one is, by search (see the module docstring), or
    None (also when the user answered "keep as entered", or skipped the release).
    Raises ``ImportFailed`` if MusicBrainz cannot be reached.

    ``artist``: the credit to search for (None: any). ``track_count``: require the
    same number of tracks. ``fits``: decides whether a fetched candidate fits (default:
    ``align`` and ``durations_fit``). ``entered``: what "as entered" is, for messages.
    """
    fits = fits or _fits_by_position
    count = len(release.tracks)
    query = lucene_query(
        release=release.title, artist=artist, tracks=str(count) if track_count else None
    )
    try:
        hits = ctx.musicbrainz.search_releases(query, limit=25)
    except MusicBrainzError as e:
        raise ImportFailed(f"MusicBrainz search failed: {e}") from e
    known = set(
        ctx.session.exec(
            select(ReleaseGroup.release_mbid).where(
                col(ReleaseGroup.release_mbid).in_([hit.id for hit in hits])
            )
        )
    )
    candidates = sorted(
        (
            hit
            for hit in hits
            if (hit.score or 0) >= MIN_SEARCH_SCORE
            and (not track_count or sum(m.track_count for m in hit.media) == count)
        ),
        key=lambda hit: (
            hit.id in known,
            hit.score or 0,
            any(m.format == "Digital Media" for m in hit.media),
            hit.country == "XW",
            hit.date is not None,
        ),
        reverse=True,
    )
    issue = Issue(
        id=issue_id(IssueKind.uncertain_metadata, release.key),
        kind=IssueKind.uncertain_metadata,
        message=(
            f"MusicBrainz has releases like {release_label(release)!r}, but their tracks "
            f"do not fit. Use one, or import the {entered} as entered."
        ),
        subject=subject,
        candidates=[mb_candidate(hit) for hit in candidates[:MAX_METADATA_CANDIDATES]],
        choices=[Choice.pick_candidate, Choice.keep_as_entered, Choice.skip],
    )
    answer = ctx.answers.get(issue.id)
    if answer is not None and answer_fits(issue, answer):
        ctx.raise_issue(issue)
        match answer.choice:
            case Choice.pick_candidate:
                assert answer.candidate_id is not None
                return _lookup(ctx, answer.candidate_id)
            case Choice.skip:
                skip_release(release)
        return None
    for hit in candidates[:MAX_RELEASE_LOOKUPS]:
        try:
            mb_release = _lookup(ctx, hit.id)
        except MusicBrainzNotFound:
            continue
        if fits(release, mb_release):
            ctx.log(f"found in MusicBrainz: {mb_label(mb_release)} ({mb_release.id})")
            return mb_release
    if candidates and candidates[0].score == 100:
        ctx.raise_issue(issue)  # open: matching waits for the answer
        return None
    ctx.log(
        f"{release_label(release)!r} not found in MusicBrainz; importing the {entered} as entered"
    )
    return None


def first_fitting(
    ctx: JobContext, release: IncomingRelease, mbids: Iterable[uuid.UUID], what: str
) -> MBRelease | None:
    """The first of these releases whose tracks fit, or None (logged)."""
    for mbid in mbids:
        try:
            mb_release = _lookup(ctx, mbid)
        except MusicBrainzNotFound:
            continue
        if _fits_by_position(release, mb_release):
            ctx.log(f"found in MusicBrainz by {what}: {mb_label(mb_release)} ({mb_release.id})")
            return mb_release
        ctx.log(
            f"MusicBrainz release {mb_release.id} ({what}) does not fit the tracks of "
            f"{release_label(release)!r}",
            LogLevel.warning,
        )
    return None


def _lookup(ctx: JobContext, mbid: uuid.UUID) -> MBRelease:
    try:
        return ctx.mb_release(mbid)
    except MusicBrainzNotFound:
        raise
    except MusicBrainzError as e:
        raise ImportFailed(f"MusicBrainz lookup failed: {e}") from e


def _fits_by_position(release: IncomingRelease, mb_release: MBRelease) -> bool:
    aligned = align(release, mb_release)
    return aligned is not None and durations_fit(release, aligned)


def _mb_tracks(mb_release: MBRelease) -> list[MBTrack]:
    return [track for medium in mb_release.media for track in medium.tracks]


def track_positions(mb_release: MBRelease) -> dict[uuid.UUID, tuple[int, int]]:
    """Track MBID -> (disc, position)."""
    return {
        track.id: (medium.position or index, track.position)
        for index, medium in enumerate(mb_release.media, start=1)
        for track in medium.tracks
    }


def align(release: IncomingRelease, mb_release: MBRelease) -> dict[str, MBTrack] | None:
    """Incoming track key -> MusicBrainz track, or None if the tracks do not fit the
    release: by tagged release track MBID, else recording MBID, else disc and position."""
    mb_tracks = _mb_tracks(mb_release)
    by_id = {track.id: track for track in mb_tracks}
    recording_counts = Counter(track.recording.id for track in mb_tracks)
    by_recording = {t.recording.id: t for t in mb_tracks if recording_counts[t.recording.id] == 1}
    positions = track_positions(mb_release)
    by_position = {positions[track.id]: track for track in mb_tracks}
    aligned: dict[str, MBTrack] = {}
    for track in release.tracks:
        mb_track = (
            (by_id.get(track.mb_track_id) if track.mb_track_id else None)
            or (by_recording.get(track.mbid) if track.mbid else None)
            or by_position.get((track.disc, track.position))
        )
        if mb_track is None:
            return None
        aligned[track.key] = mb_track
    if len({t.id for t in aligned.values()}) != len(aligned):
        return None
    return aligned


def _length_fits(length_ms: int | None, mb_track: MBTrack) -> bool:
    mb_length = mb_track.length or mb_track.recording.length
    if length_ms is None or mb_length is None:
        return True
    return abs(length_ms - mb_length) <= DURATION_TOLERANCE_MS


def durations_fit(release: IncomingRelease, aligned: dict[str, MBTrack]) -> bool:
    """Every aligned track as long as its MusicBrainz track (where both are known)."""
    return all(
        _length_fits(track.length_ms, aligned[track.key])
        for track in release.tracks
        if track.key in aligned
    )


def align_by_title(release: IncomingRelease, mb_release: MBRelease) -> dict[str, MBTrack]:
    """Incoming track key -> the MusicBrainz track with the same title (normalised,
    at least ``AUTO_MATCH_SCORE``) and a fitting length; each track used once, best
    pairs first. Tracks without such a partner are left out."""
    pairs = []
    for track in release.tracks:
        ours = normalize_title(track.title)
        for mb_track in _mb_tracks(mb_release):
            if not _length_fits(track.length_ms, mb_track):
                continue
            score = max(
                text_score(ours, normalize_title(theirs))
                for theirs in (mb_track.title, mb_track.recording.title)
            )
            if score >= AUTO_MATCH_SCORE:
                pairs.append((score, track.key, mb_track))
    aligned: dict[str, MBTrack] = {}
    used: set[uuid.UUID] = set()
    for _score, key, mb_track in sorted(pairs, key=lambda p: p[0], reverse=True):
        if key not in aligned and mb_track.id not in used:
            aligned[key] = mb_track
            used.add(mb_track.id)
    return aligned


def use_musicbrainz(
    ctx: JobContext,
    proposal: Proposal,
    release: IncomingRelease,
    mb_release: MBRelease,
    aligned: dict[str, MBTrack] | None = None,
) -> bool:
    """Take the release's metadata from MusicBrainz; False (kept as entered) if the
    tracks do not fit its track list. ``aligned`` (e.g. from ``align_by_title``) must
    cover every track of ``release``; default: ``align``."""
    aligned = aligned if aligned is not None else align(release, mb_release)
    if aligned is None or mb_release.release_group is None:
        ctx.log(
            f"the tracks of {release_label(release)!r} do not fit MusicBrainz release "
            f"{mb_release.id}; importing them as entered",
            LogLevel.warning,
        )
        return False
    positions = track_positions(mb_release)
    mb_group = mb_release.release_group
    release.title = mb_group.title
    release.credit = mb_credit(proposal, mb_group.artist_credit or mb_release.artist_credit)
    release.type = release_group_type(mb_group)
    release.date = mb_group.first_release_date or mb_release.date
    release.barcode = mb_release.barcode
    release.mbid, release.release_mbid = mb_group.id, mb_release.id
    release.target = UNDECIDED  # the framework matches it, by MBID or else
    for track in release.tracks:
        mb_track = aligned[track.key]
        track.disc, track.position = positions[mb_track.id]
        track.title = mb_track.title
        track.credit = mb_credit(
            proposal, mb_track.artist_credit or mb_track.recording.artist_credit
        )
        track.mbid, track.mb_track_id = mb_track.recording.id, mb_track.id
        track.isrcs = sorted({*track.isrcs, *mb_track.recording.isrcs})
    return True


def mb_label(mb_release: MBRelease) -> str:
    return f"{mb_release.title} — {render_artist_credit(mb_release.artist_credit)}"


def mb_candidate(hit: MBRelease) -> IssueCandidate:
    facts = [
        hit.date,
        hit.country,
        ", ".join(sorted({m.format for m in hit.media if m.format})) or None,
        f"{sum(m.track_count for m in hit.media)} tracks",
        hit.disambiguation or None,
    ]
    return IssueCandidate(
        type=CandidateType.musicbrainz_release,
        id=hit.id,
        title=hit.title,
        subtitle=render_artist_credit(hit.artist_credit),
        detail=" · ".join(fact for fact in facts if fact),
        score=float(hit.score or 0),
    )


def prune_artists(proposal: Proposal) -> None:
    """Drop incoming artists no credit refers to any more (replaced by MusicBrainz)."""
    used = {
        part.artist_key
        for item in [*proposal.releases, *proposal.all_tracks()]
        for part in item.credit
    }
    proposal.artists = [artist for artist in proposal.artists if artist.key in used]
