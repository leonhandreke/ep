"""Links in note bodies: ``[[type:uuid|Display text]]`` (Obsidian style).

- ``type`` is an entity type (``artist``, ``release_group``, ``recording``,
  ``radio_show``, ``radio_episode``); ``uuid`` is the entity's MBID if it has one, else
  its internal ID (the resolver accepts either, ``ep.entities.resolve_refs``).
- The display text is optional (``[[artist:uuid]]``). In it, ``\\|``, ``\\[``, ``\\]``
  and ``\\\\`` stand for ``|``, ``[``, ``]`` and ``\\`` (``escape_display``); it cannot
  span lines.
- Not links: anything inside a fenced code block (```` ``` ```` or ``~~~``, up to three
  spaces indented, closed by a fence of the same character at least as long, or open
  to the end) or an inline code span (a run of backticks closed by a run of the same
  length, within a paragraph; CommonMark), a link escaped with a backslash
  (``\\[[...]]``), and anything that does not fit the syntax (an unknown type, a
  malformed UUID). Indented code blocks are not recognised: in notes, indentation is
  mostly nested lists.

Ranges are offsets into the body in Unicode code points (Python string indices);
``utf16_offset`` converts them for clients that count UTF-16 code units (NSString).
"""

import re
import uuid
from dataclasses import dataclass

from ep.models import EntityType

_TYPES = "|".join(re.escape(t.value) for t in EntityType)
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
LINK = re.compile(
    rf"\[\[(?P<type>{_TYPES}):(?P<uuid>{_UUID})(?:\|(?P<display>(?:\\.|[^\\\]\n])*))?\]\]"
)
_FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_BACKTICKS = re.compile(r"`+")
# A line and its ending (CommonMark: \n, \r\n or \r; not str.splitlines' others).
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)?")
_UNESCAPE = re.compile(r"\\([\\|\[\]])")
_ESCAPE = re.compile(r"([\\|\[\]])")


@dataclass(frozen=True)
class Link:
    type: EntityType
    ref: uuid.UUID
    # Unescaped; None without display text.
    display: str | None
    start: int
    end: int
    raw: str


def escape_display(text: str) -> str:
    """Display text as written in a link (``|``, ``[``, ``]`` and ``\\`` escaped; line
    breaks as spaces)."""
    return _ESCAPE.sub(r"\\\1", " ".join(text.splitlines()))


def format_link(type: EntityType, ref: uuid.UUID, display: str | None = None) -> str:
    """The link markup for an entity (``ref``: its MBID if it has one, else its id)."""
    text = f"|{escape_display(display)}" if display else ""
    return f"[[{type.value}:{ref}{text}]]"


def parse_links(body: str) -> list[Link]:
    """The links in a note body, in order."""
    excluded = _code_ranges(body)
    links = []
    for match in LINK.finditer(body):
        start, end = match.span()
        if _escaped(body, start) or any(s < end and start < e for s, e in excluded):
            continue
        display = match["display"]
        links.append(
            Link(
                type=EntityType(match["type"]),
                ref=uuid.UUID(match["uuid"]),
                display=_UNESCAPE.sub(r"\1", display) if display else None,
                start=start,
                end=end,
                raw=match[0],
            )
        )
    return links


def utf16_offset(text: str, offset: int) -> int:
    """A code point offset into ``text`` as a UTF-16 code unit offset."""
    return offset + sum(1 for char in text[:offset] if ord(char) > 0xFFFF)


def _escaped(text: str, index: int) -> bool:
    """The character at ``index`` is preceded by an odd number of backslashes."""
    count = 0
    while index - count > 0 and text[index - count - 1] == "\\":
        count += 1
    return count % 2 == 1


def _code_ranges(body: str) -> list[tuple[int, int]]:
    """Ranges of fenced code blocks and inline code spans."""
    ranges: list[tuple[int, int]] = []
    paragraph: list[tuple[int, int]] = []  # lines of the current paragraph

    def end_paragraph() -> None:
        if paragraph:
            ranges.extend(_code_spans(body, paragraph[0][0], paragraph[-1][1]))
            paragraph.clear()

    fence: tuple[str, int, int] | None = None  # character, length, start
    position = 0
    for line_match in _LINE.finditer(body):
        line = line_match[0]
        if not line:
            break
        start, position = position, position + len(line)
        text = line.rstrip("\r\n")
        match = _FENCE.match(text)
        if fence is not None:
            char, length, fence_start = fence
            closes = match and match["fence"][0] == char and len(match["fence"]) >= length
            if closes and not match["info"].strip(" \t"):
                ranges.append((fence_start, position))
                fence = None
            continue
        if match and not (match["fence"][0] == "`" and "`" in match["info"]):
            end_paragraph()
            fence = (match["fence"][0], len(match["fence"]), start)
        elif not text.strip(" \t"):
            end_paragraph()
        else:
            paragraph.append((start, start + len(text)))
    if fence is not None:
        ranges.append((fence[2], len(body)))
    end_paragraph()
    return ranges


def _code_spans(body: str, start: int, end: int) -> list[tuple[int, int]]:
    """Inline code spans in ``body[start:end]``: a backtick run opens a span closed by
    the next run of the same length; an opener without one is literal text."""
    runs = [match.span() for match in _BACKTICKS.finditer(body, start, end)]
    spans = []
    i = 0
    while i < len(runs):
        open_start, open_end = runs[i]
        # Outside code, "\`" is a literal backtick (inside, backslashes are literal).
        if _escaped(body, open_start):
            open_start += 1
        length = open_end - open_start
        close = next(
            (j for j in range(i + 1, len(runs)) if runs[j][1] - runs[j][0] == length), None
        )
        if length == 0 or close is None:
            i += 1
            continue
        spans.append((open_start, runs[close][1]))
        i = close + 1
    return spans
