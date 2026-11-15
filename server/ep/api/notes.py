"""Notes on entities, their links, backlinks and full-text search over notes.

An entity is named in the path by its MBID or its internal ID. Every entity has a note:
one never written reads as empty. Saves are checked against the ``updated_at`` the
client loaded (several clients may edit the same note; there is no push): a stale save
is a 409 with the current note.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, literal_column
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import contains_eager, selectinload
from sqlmodel import Session, col, select

from ep.api.deps import PageDep, SessionDep
from ep.api.entities import entity_reads, get_entity_or_404
from ep.api.errors import NOT_FOUND
from ep.api.schemas import (
    BacklinkRead,
    EntityRead,
    NoteConflictRead,
    NoteLinkRead,
    NoteRead,
    NoteSearchHitRead,
    NoteWrite,
    SnippetRead,
    TextRange,
)
from ep.entities import load_entities, mbid_of, refers_to, resolve_refs, title_of
from ep.models import NOTE_SEARCH_CONFIG, Base, EntityType, Note, NoteLink, note_document, rel
from ep.notes import store
from ep.notes.links import Link, parse_links, utf16_offset

router = APIRouter(tags=["notes"])

NOTE_CONFLICT: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": NoteConflictRead,
        "description": "The note changed since the edit started",
    }
}
# Longest snippet line; longer lines are cut around the link.
SNIPPET_CONTEXT = 80
# ts_headline marks matches with these (not in Markdown text); turned into ranges.
_START, _STOP = "\x02", "\x03"
_HEADLINE_OPTIONS = (
    f"StartSel={_START}, StopSel={_STOP}, MinWords=12, MaxWords=30, "
    'MaxFragments=2, FragmentDelimiter=" … "'
)


def _range(text: str, start: int, end: int) -> TextRange:
    return TextRange(start=utf16_offset(text, start), end=utf16_offset(text, end))


def note_read(
    session: Session, entity_type: EntityType, entity_id: uuid.UUID, note: Note | None
) -> NoteRead:
    """The note with its links resolved now: by the UUID written, else (the UUID names
    nothing now, e.g. an MBID the entity lost) the entity it resolved to on save."""
    body = note.body if note is not None else ""
    links = parse_links(body)
    targets = resolve_refs(session, [(link.type, link.ref) for link in links])
    saved = {saved.start: saved.target_id for saved in note.links} if note is not None else {}
    fallback_keys = {
        (link.type, target_id)
        for link in links
        if (link.type, link.ref) not in targets and (target_id := saved.get(link.start))
    }
    fallback = load_entities(session, fallback_keys) if fallback_keys else {}

    def target(link: Link) -> Base | None:
        found = targets.get((link.type, link.ref))
        if found is None and (target_id := saved.get(link.start)) is not None:
            found = fallback.get((link.type, target_id))
        return found

    resolved = [target(link) for link in links]
    reads = entity_reads(session, {e.id: e for e in resolved if e is not None}.values())
    return NoteRead(
        entity_type=entity_type,
        entity_id=entity_id,
        body=body,
        updated_at=note.updated_at if note is not None else None,
        links=[
            NoteLinkRead(
                range=_range(body, link.start, link.end),
                raw=link.raw,
                target_type=link.type,
                ref=link.ref,
                display_text=link.display,
                target=reads[entity.id] if entity is not None else None,
            )
            for link, entity in zip(links, resolved, strict=True)
        ],
    )


@router.get("/notes/search")
def search_notes(
    session: SessionDep,
    page: PageDep,
    q: Annotated[str, Query(min_length=1)],
    types: Annotated[list[EntityType], Query(default_factory=list)],
) -> list[NoteSearchHitRead]:
    """Notes matching ``q`` (Postgres full-text search, web search syntax: words,
    ``"a phrase"``, ``or``, ``-word``), best first, with the entity each belongs to and
    a snippet. Case- and accent-insensitive, no stemming (notes mix languages); links
    count as their display text. ``types`` (repeatable) limits the entity types."""
    config = literal_column(f"'{NOTE_SEARCH_CONFIG}'::regconfig")
    query = func.websearch_to_tsquery(config, q)
    document = note_document(col(Note.body))
    rank = func.ts_rank(document, query)
    headline = func.ts_headline(config, func.ep_note_text(Note.body), query, _HEADLINE_OPTIONS)
    statement = (
        select(Note, rank, headline)
        .where(document.op("@@")(query))
        .order_by(rank.desc(), col(Note.updated_at).desc(), col(Note.id))
        .limit(page.limit)
        .offset(page.offset)
    )
    if types:
        statement = statement.where(col(Note.entity_type).in_(types))
    rows = session.exec(statement).all()
    sources = _sources(session, [note for note, _, _ in rows])
    return [
        NoteSearchHitRead(
            source=sources[note.entity_id],
            note_updated_at=note.updated_at,
            snippet=_headline_snippet(text),
            rank=score,
        )
        for note, score, text in rows
        if note.entity_id in sources
    ]


@router.get("/notes/{type}/{id}", responses=NOT_FOUND)
def get_note(type: EntityType, id: uuid.UUID, session: SessionDep) -> NoteRead:
    """The note of an entity (``id``: its MBID or internal ID; a track's note is its
    recording's). An entity without a note has an empty one. Links come with their
    range in the body and the entity they point at (null: broken), so the editor can
    show them without further requests."""
    entity = get_entity_or_404(session, type, id)
    return current_note_read(session, type, entity.id)


def current_note_read(session: Session, type: EntityType, entity_id: uuid.UUID) -> NoteRead:
    """The note of an entity as it is now (``get_note``)."""
    note = session.exec(
        select(Note)
        .where(Note.entity_type == type, Note.entity_id == entity_id)
        .options(selectinload(rel(Note.links)))
    ).first()
    return note_read(session, type, entity_id, note)


@router.put("/notes/{type}/{id}", response_model=NoteRead, responses=NOT_FOUND | NOTE_CONFLICT)
def save_note(
    type: EntityType, id: uuid.UUID, body: NoteWrite, session: SessionDep
) -> NoteRead | JSONResponse:
    """Save the note of an entity; an empty body deletes it. ``base_updated_at`` is the
    ``updated_at`` the edit started from: if the note changed since (and the new body
    differs from the current one), nothing is saved and the answer is a 409 with the
    current note. Links are parsed and resolved again on every save."""
    entity = get_entity_or_404(session, type, id)
    current = store.get_note(session, type, entity.id, lock=True)
    current_body = current.body if current is not None else ""
    current_at = current.updated_at if current is not None else None
    if not _same_version(current_at, body.base_updated_at) and body.body != current_body:
        return _conflict(session, type, entity.id, current)
    try:
        with session.begin_nested():
            note = store.save_note(session, type, entity.id, body.body)
    except IntegrityError:  # another client created the note meanwhile
        return _conflict(session, type, entity.id, store.get_note(session, type, entity.id))
    session.commit()
    return note_read(session, type, entity.id, note)


# Clients may send ``updated_at`` back at millisecond precision (NSDate and
# ISO8601DateFormatter do not keep microseconds): within this, it is the same version.
VERSION_TOLERANCE = timedelta(milliseconds=1)


def _same_version(current: datetime | None, base: datetime | None) -> bool:
    if current is None or base is None:
        return current is base
    return abs(current - base) < VERSION_TOLERANCE


def _conflict(
    session: Session, entity_type: EntityType, entity_id: uuid.UUID, note: Note | None
) -> JSONResponse:
    conflict = NoteConflictRead(
        detail="the note changed since the edit started",
        current=note_read(session, entity_type, entity_id, note),
    )
    return JSONResponse(conflict.model_dump(mode="json"), status_code=status.HTTP_409_CONFLICT)


@router.get("/backlinks/{type}/{id}", responses=NOT_FOUND)
def list_backlinks(type: EntityType, id: uuid.UUID, session: SessionDep) -> list[BacklinkRead]:
    """Notes that link to this entity (exactly this entity: a link to a recording is
    not a backlink of its release group), most recently edited first, each with the
    entity it belongs to and the line around each link."""
    return backlink_reads(session, type, get_entity_or_404(session, type, id))


def backlink_reads(session: Session, type: EntityType, entity: Base) -> list[BacklinkRead]:
    """The backlinks of an entity (``list_backlinks``)."""
    refs = [entity.id, *filter(None, (mbid_of(entity), getattr(entity, "release_mbid", None)))]
    links = session.exec(
        select(NoteLink)
        .join(rel(NoteLink.note))
        .where(store.links_to(type, entity.id, refs))
        .order_by(col(Note.updated_at).desc(), col(Note.id), col(NoteLink.start))
        .options(contains_eager(rel(NoteLink.note)))
    ).all()
    by_note: dict[uuid.UUID, list[NoteLink]] = defaultdict(list)
    notes: dict[uuid.UUID, Note] = {}
    for link in links:
        by_note[link.note_id].append(link)
        notes[link.note_id] = link.note
    sources = _sources(session, notes.values())
    parsed = {note_id: parse_links(note.body) for note_id, note in notes.items()}
    # Links without display text show their target's title (this entity's is known).
    untitled = {
        (link.type, link.ref)
        for links in parsed.values()
        for link in links
        if not link.display and not (link.type is type and refers_to(entity, link.ref))
    }
    titles = {
        key: title_of(target)
        for key, target in resolve_refs(session, untitled, summary=False).items()
    }
    titles.update({(type, ref): title_of(entity) for ref in refs})
    backlinks = []
    for note_id, note_links in by_note.items():
        note = notes[note_id]
        if note.entity_id not in sources:
            continue
        backlinks.append(
            BacklinkRead(
                source=sources[note.entity_id],
                note_updated_at=note.updated_at,
                snippets=[
                    _line_snippet(note.body, parsed[note_id], link, titles) for link in note_links
                ],
            )
        )
    return backlinks


def _sources(session: Session, notes: Iterable[Note]) -> dict[uuid.UUID, EntityRead]:
    """The entities the notes belong to, by ID."""
    entities = load_entities(session, {(note.entity_type, note.entity_id) for note in notes})
    return entity_reads(session, entities.values())


def _line_snippet(
    body: str,
    parsed: list[Link],
    link: NoteLink,
    titles: dict[tuple[EntityType, uuid.UUID], str],
) -> SnippetRead:
    """The line of the body around a link, with links as their display text (without
    one: the target's title from ``titles``; the highlighted link's target is under its
    internal ID too), cut to about ``SNIPPET_CONTEXT`` characters on each side of the
    link."""
    line_start = max(body.rfind("\n", 0, link.start), body.rfind("\r", 0, link.start)) + 1
    line_end = next((i for i in range(link.end, len(body)) if body[i] in "\r\n"), len(body))
    text, position = "", line_start
    highlight = (0, 0)
    for other in parsed:
        if other.end <= line_start or other.start >= line_end:
            continue
        text += body[position : other.start]
        highlighted = other.start == link.start
        shown = (
            other.display
            or titles.get((other.type, other.ref))
            or (highlighted and titles.get((link.target_type, link.target_id)))
            or other.raw
        )
        if highlighted:
            highlight = (len(text), len(text) + len(shown))
        text += shown
        position = other.end
    text += body[position:line_end]
    start, end = highlight
    cut_start = max(0, start - SNIPPET_CONTEXT)
    cut_end = min(len(text), end + SNIPPET_CONTEXT)
    prefix = "…" if cut_start > 0 else ""
    suffix = "…" if cut_end < len(text) else ""
    text = prefix + text[cut_start:cut_end] + suffix
    start, end = start - cut_start + len(prefix), end - cut_start + len(prefix)
    return SnippetRead(text=text, highlights=[_range(text, start, end)])


def _headline_snippet(headline: str) -> SnippetRead:
    """A ``ts_headline`` result as text (line breaks as spaces) and highlight ranges."""
    text, highlights, start = "", [], 0
    for char in headline:
        if char == _START:
            start = len(text)
        elif char == _STOP:
            highlights.append((start, len(text)))
        else:
            text += " " if char in "\r\n" else char
    return SnippetRead(text=text, highlights=[_range(text, s, e) for s, e in highlights])
