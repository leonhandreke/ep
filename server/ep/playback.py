"""Playback resolution: which file plays a source, and which part of it; and how a
recording plays (``playability``)."""

import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import ColumnElement
from sqlalchemy import true as sa_true
from sqlmodel import Session, col, select

from ep.models import RadioEpisodeTrack, Recording, Source, SourceKind

# Source kinds that are links to somewhere else (not playable here yet).
LINK_KINDS = frozenset(set(SourceKind) - {SourceKind.file, SourceKind.slice})


class NotPlayable(Exception):
    """The source has no file to play (a remote-only link, or a broken chain)."""


@dataclass(frozen=True)
class Playback:
    source_id: uuid.UUID
    file_source_id: uuid.UUID
    path: Path
    # Absolute range within the file; ``end_ms`` None plays to the end of the file.
    start_ms: int
    end_ms: int | None
    # Length of the whole file, if known.
    file_duration_ms: int | None = None


def resolve(source: Source) -> Playback:
    """Walk ``parent_id`` up to the nearest ``file`` source, summing slice offsets.

    Nested slices are relative to their parent and cannot extend beyond it. Loads each
    parent that is not in the session yet (``resolve_where`` loads them up front).
    """
    slices: list[Source] = []
    seen: set[uuid.UUID] = set()
    node: Source | None = source
    while node is not None and node.kind != SourceKind.file:
        if node.id in seen:
            raise NotPlayable(f"source {source.id}: cycle in parent chain")
        seen.add(node.id)
        if node.kind == SourceKind.slice:
            slices.append(node)
        node = node.parent
    if node is None:
        raise NotPlayable(f"source {source.id} ({source.kind}) has no file to play")
    if not node.uri:
        raise NotPlayable(f"file source {node.id} has no path")

    # Apply ranges from the outermost slice (nearest the file) inwards.
    start, end = 0, None
    for piece in reversed(slices):
        piece_start = start + (piece.start_ms or 0)
        piece_end = None if piece.end_ms is None else start + piece.end_ms
        if end is not None:
            piece_end = end if piece_end is None else min(piece_end, end)
        start, end = piece_start, piece_end
        if end is not None and start >= end:
            raise NotPlayable(f"slice {piece.id} lies outside its parent")
    return Playback(source.id, node.id, Path(node.uri), start, end, node.duration_ms)


def resolve_where(
    session: Session, where: ColumnElement[bool]
) -> list[tuple[Source, Playback | None]]:
    """The sources matching ``where``, oldest first, each resolved (None if not playable).

    Loads the sources and all their ancestors in one query (a recursive CTE over
    ``parent_id``; ``UNION`` stops at cycles), so that ``resolve`` finds every parent in
    the session instead of loading them one at a time. The ancestors are held until all
    are resolved: the session only keeps weak references.
    """
    return [(r.source, r.playback) for r in resolve_where_flagged(session, where)]


@dataclass(frozen=True)
class RadioEntryRef:
    """The radio tracklist entry a slice plays."""

    track_id: uuid.UUID
    episode_id: uuid.UUID


@dataclass(frozen=True)
class Resolved:
    source: Source
    playback: Playback | None
    # Set if the source is a radio tracklist entry's slice.
    radio: RadioEntryRef | None
    # The source's ancestors, nearest first (loaded with it).
    ancestors: tuple[Source, ...]


def resolve_where_flagged(session: Session, where: ColumnElement[bool]) -> list[Resolved]:
    """``resolve_where``, each source also with the radio entry it is the slice of and
    its ancestors."""
    chain = select(col(Source.id), col(Source.parent_id)).where(where).cte(recursive=True)
    chain = chain.union(
        select(col(Source.id), col(Source.parent_id)).join(
            chain, col(Source.id) == chain.c.parent_id
        )
    )
    entry = (
        select(RadioEpisodeTrack.id, RadioEpisodeTrack.episode_id)
        .where(col(RadioEpisodeTrack.source_id) == col(Source.id))
        .order_by(col(RadioEpisodeTrack.id))
        .limit(1)
        .lateral()
    )
    rows = session.exec(
        select(Source, where, entry.c.id, entry.c.episode_id)
        .outerjoin(entry, sa_true())
        .where(col(Source.id).in_(select(chain.c.id)))
        .order_by(col(Source.created_at), col(Source.id))
    ).all()
    result = []
    for source, matches, track_id, episode_id in rows:
        if not matches:
            continue
        ancestors: list[Source] = []
        seen = {source.id}
        node = source.parent
        while node is not None and node.id not in seen:
            seen.add(node.id)
            ancestors.append(node)
            node = node.parent
        radio = RadioEntryRef(track_id, episode_id) if track_id is not None else None
        result.append(Resolved(source, try_resolve(source), radio, tuple(ancestors)))
    return result


def try_resolve(source: Source) -> Playback | None:
    """``resolve``, or None if the source is not playable."""
    try:
        return resolve(source)
    except NotPlayable:
        return None


class PlayState(StrEnum):
    """How a recording plays here (the first that applies)."""

    # From a file of its own.
    file = "file"
    # From a time range of a file it shares (a single-file album, chapters).
    slice = "slice"
    # Only as an excerpt of a radio episode (or the user chose that excerpt).
    radio_excerpt = "radio_excerpt"
    # Its library file (or the file its range is in) is not where the source says;
    # ``playback`` is a radio excerpt to play instead, if there is one.
    file_missing = "file_missing"
    # Not playable; only links to elsewhere (open in the browser, or import).
    link_only = "link_only"
    # Metadata only.
    no_source = "no_source"


@dataclass(frozen=True)
class Playability:
    state: PlayState
    # What plays: the preferred source if it plays, else the oldest library source whose
    # file is there, else the oldest radio excerpt.
    playback: Playback | None
    # The radio entry ``playback`` plays (a radio excerpt).
    radio: RadioEntryRef | None
    # The recording's oldest link source (any state).
    link: Source | None
    # ``file_missing``: the file source that was not found.
    missing_file_source_id: uuid.UUID | None


def playabilities(
    session: Session, recordings: Iterable[Recording]
) -> dict[uuid.UUID, Playability]:
    """How each recording plays (one query for all their sources and ancestors; a file
    system check per file tried)."""
    recordings = list(recordings)
    by_recording: dict[uuid.UUID, list[Resolved]] = {r.id: [] for r in recordings}
    if by_recording:
        where = col(Source.recording_id).in_(by_recording)
        for resolved in resolve_where_flagged(session, where):
            recording_id = resolved.source.recording_id
            if recording_id is not None and recording_id in by_recording:
                by_recording[recording_id].append(resolved)
    return {r.id: _playability(by_recording[r.id], r.preferred_source_id) for r in recordings}


def _playability(sources: list[Resolved], preferred: uuid.UUID | None) -> Playability:
    link = next((r.source for r in sources if r.source.kind in LINK_KINDS), None)
    playable = [r for r in sources if r.playback is not None]
    playable.sort(key=lambda r: (r.source.id != preferred, r.radio is not None))
    missing: Resolved | None = None
    for candidate in playable:
        assert candidate.playback is not None
        if not candidate.playback.path.is_file():
            if missing is None and candidate.radio is None:
                missing = candidate
            continue
        if missing is not None and candidate.radio is not None:
            state = PlayState.file_missing
        elif candidate.radio is not None:
            state = PlayState.radio_excerpt
        elif candidate.source.kind is SourceKind.slice:
            state = PlayState.slice
        else:
            state = PlayState.file
        return Playability(
            state,
            candidate.playback,
            candidate.radio,
            link,
            missing.playback.file_source_id if missing and missing.playback else None,
        )
    if missing is not None and missing.playback is not None:
        return Playability(
            PlayState.file_missing, None, None, link, missing.playback.file_source_id
        )
    state = PlayState.link_only if link is not None else PlayState.no_source
    return Playability(state, None, None, link, None)


def recording_playbacks(
    session: Session, recording_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, Playback]:
    """How to play each recording: its oldest source that resolves to a file, preferring
    the library proper to radio slices. Recordings without a playable source are left
    out. (No file system check: ``playabilities`` for that.)"""
    if not recording_ids:
        return {}
    found: dict[uuid.UUID, tuple[bool, Playback]] = {}
    where = col(Source.recording_id).in_(recording_ids)
    for resolved in resolve_where_flagged(session, where):
        recording_id = resolved.source.recording_id
        if resolved.playback is None or recording_id is None:
            continue
        is_radio = resolved.radio is not None
        if recording_id not in found or (found[recording_id][0] and not is_radio):
            found[recording_id] = (is_radio, resolved.playback)
    return {id: playback for id, (_, playback) in found.items()}
