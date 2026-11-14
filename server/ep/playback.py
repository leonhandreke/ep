"""Playback resolution: which file plays a source, and which part of it."""

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, col, select

from ep.models import Source, SourceKind


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

    Nested slices are relative to their parent and cannot extend beyond it.
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


def recording_playback(session: Session, recording_id: uuid.UUID) -> Playback | None:
    """How to play a recording: its oldest source that resolves to a file, if any."""
    sources = session.exec(
        select(Source)
        .where(Source.recording_id == recording_id)
        .order_by(col(Source.created_at), col(Source.id))
    )
    for source in sources:
        try:
            return resolve(source)
        except NotPlayable:
            continue
    return None
