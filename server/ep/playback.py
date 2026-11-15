"""Playback resolution: which file plays a source, and which part of it."""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import ColumnElement, exists
from sqlmodel import Session, col, select

from ep.models import RadioEpisodeTrack, Source, SourceKind


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
    return Playback(source.id, node.id, Path(node.uri), start, end)


def resolve_where(
    session: Session, where: ColumnElement[bool]
) -> list[tuple[Source, Playback | None]]:
    """The sources matching ``where``, oldest first, each resolved (None if not playable).

    Loads the sources and all their ancestors in one query (a recursive CTE over
    ``parent_id``; ``UNION`` stops at cycles), so that ``resolve`` finds every parent in
    the session instead of loading them one at a time. The ancestors are held until all
    are resolved: the session only keeps weak references.
    """
    return [(source, playback) for source, playback, _ in resolve_where_flagged(session, where)]


def resolve_where_flagged(
    session: Session, where: ColumnElement[bool]
) -> list[tuple[Source, Playback | None, bool]]:
    """``resolve_where``, each source also flagged if it is a radio tracklist entry's slice."""
    chain = select(col(Source.id), col(Source.parent_id)).where(where).cte(recursive=True)
    chain = chain.union(
        select(col(Source.id), col(Source.parent_id)).join(
            chain, col(Source.id) == chain.c.parent_id
        )
    )
    radio = exists().where(col(RadioEpisodeTrack.source_id) == col(Source.id))
    rows = session.exec(
        select(Source, where, radio)
        .where(col(Source.id).in_(select(chain.c.id)))
        .order_by(col(Source.created_at), col(Source.id))
    ).all()
    return [
        (source, try_resolve(source), is_radio) for source, matches, is_radio in rows if matches
    ]


def try_resolve(source: Source) -> Playback | None:
    """``resolve``, or None if the source is not playable."""
    try:
        return resolve(source)
    except NotPlayable:
        return None


@dataclass(frozen=True)
class RecordingPlayback:
    """The ways to play a recording: from the library proper (a file, or a slice of an
    album file), and from a radio episode (a tracklist entry's slice)."""

    library: Playback | None
    radio: Playback | None

    @property
    def best(self) -> Playback:
        """The library's, if there is one: a proper file beats a radio recording."""
        best = self.library or self.radio
        assert best is not None
        return best


def recording_playback_choices(
    session: Session, recording_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, RecordingPlayback]:
    """For each recording, its oldest playable library source and its oldest playable
    radio slice (one query). Recordings without a playable source are left out."""
    if not recording_ids:
        return {}
    library: dict[uuid.UUID, Playback] = {}
    radio: dict[uuid.UUID, Playback] = {}
    where = col(Source.recording_id).in_(recording_ids)
    for source, playback, is_radio in resolve_where_flagged(session, where):
        if playback is not None and source.recording_id is not None:
            (radio if is_radio else library).setdefault(source.recording_id, playback)
    return {
        id: RecordingPlayback(library.get(id), radio.get(id))
        for id in library.keys() | radio.keys()
    }


def recording_playbacks(
    session: Session, recording_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, Playback]:
    """How to play each recording: its oldest source that resolves to a file, preferring
    the library proper to radio slices (``RecordingPlayback.best``).

    Recordings without a playable source are left out.
    """
    choices = recording_playback_choices(session, recording_ids)
    return {id: choice.best for id, choice in choices.items()}
