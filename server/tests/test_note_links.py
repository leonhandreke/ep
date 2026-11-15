"""The link parser: syntax, code blocks and spans, escapes, ranges."""

import uuid

import pytest

from ep.models import EntityType
from ep.notes.links import escape_display, format_link, parse_links, utf16_offset

A = uuid.UUID("0f9a5a3e-1111-4222-8333-444455556666")
B = uuid.UUID("7c3218d7-75e0-4e8c-971f-f097b6c308c5")


def refs(body: str) -> list[tuple[EntityType, uuid.UUID, str | None]]:
    return [(link.type, link.ref, link.display) for link in parse_links(body)]


def test_links_with_and_without_display_text() -> None:
    body = f"See [[artist:{A}|Björk]] and [[release_group:{B}]]."
    [first, second] = parse_links(body)
    assert (first.type, first.ref, first.display) == (EntityType.artist, A, "Björk")
    assert body[first.start : first.end] == first.raw == f"[[artist:{A}|Björk]]"
    assert (second.type, second.ref, second.display) == (EntityType.release_group, B, None)
    assert body[second.start : second.end] == f"[[release_group:{B}]]"


def test_every_entity_type_and_uppercase_uuids() -> None:
    body = " ".join(f"[[{t.value}:{str(A).upper()}|{t.value}]]" for t in EntityType)
    assert [(t, r) for t, r, _ in refs(body)] == [(t, A) for t in EntityType]


@pytest.mark.parametrize(
    "body",
    [
        f"[[track:{A}|x]]",  # not an entity type
        f"[[Artist:{A}|x]]",
        "[[artist:not-a-uuid|x]]",
        f"[[artist:{str(A)[:-1]}|x]]",
        f"[artist:{A}|x]",
        f"[[artist:{A}|x]",
        f"[[artist:{A}|two\nlines]]",
        f"\\[[artist:{A}|escaped]]",
        "[[]]",
        "",
    ],
)
def test_not_links(body: str) -> None:
    assert parse_links(body) == []


def test_escaped_pipes_and_brackets_in_display_text() -> None:
    display = r"A \| B \[live\] \\ C"
    [link] = parse_links(f"x [[recording:{A}|{display}]] y")
    assert link.display == r"A | B [live] \ C"
    # A backslash escaping the backslash does not escape the link.
    assert refs(f"\\\\[[artist:{A}|x]]") == [(EntityType.artist, A, "x")]


def test_format_link_escapes_and_round_trips() -> None:
    title = r"Live [at] the | Bar \ Grill"
    link = format_link(EntityType.release_group, A, title)
    assert link == rf"[[release_group:{A}|Live \[at\] the \| Bar \\ Grill]]"
    assert refs(link) == [(EntityType.release_group, A, title)]
    assert format_link(EntityType.artist, A) == f"[[artist:{A}]]"
    assert escape_display("two\nlines") == "two lines"


def test_fenced_code_blocks_are_not_parsed() -> None:
    link = f"[[artist:{A}|x]]"
    body = "\n".join(
        [
            link,
            "```python",
            link,
            "```",
            link,
            "  ~~~~",
            link,
            "~~~",  # too short to close
            link,
            "~~~~~",
            link,
            "```",  # never closed: code to the end
            link,
        ]
    )
    starts = [link.start for link in parse_links(body)]
    lines = body.split("\n")
    offsets = [sum(len(line) + 1 for line in lines[:i]) for i in range(len(lines))]
    assert starts == [offsets[0], offsets[4], offsets[10]]


def test_backtick_fence_info_with_backticks_is_not_a_fence() -> None:
    body = f"``` `a` ```\n[[artist:{A}|x]]"
    assert len(parse_links(body)) == 1


def test_inline_code_spans_are_not_parsed() -> None:
    link = f"[[artist:{A}|x]]"
    assert parse_links(f"`{link}`") == []
    assert parse_links(f"``a ` {link}``") == []  # closed by a run of the same length
    assert len(parse_links(f"`` {link} `")) == 1  # no closing run of two: literal
    assert len(parse_links(f"\\`{link}`")) == 1  # an escaped backtick opens nothing
    # A code span spans lines within a paragraph, not across a blank line.
    assert parse_links(f"`a\n{link}\nb`") == []
    assert len(parse_links(f"`a\n\n{link}\n\nb`")) == 1
    # After a span, links count again.
    assert len(parse_links(f"`code` {link} `more`")) == 1


def test_ranges_are_code_points_and_convert_to_utf16() -> None:
    body = f"🎷 Ünïcode — [[artist:{A}|Sigur Rós]] 🎺 [[recording:{B}]]"
    first, second = parse_links(body)
    assert body[first.start : first.end] == first.raw
    assert body[second.start : second.end] == second.raw
    assert first.start == 12
    # The saxophone is outside the Basic Multilingual Plane: two UTF-16 code units.
    assert utf16_offset(body, first.start) == 13
    assert utf16_offset(body, second.start) == second.start + 2
    encoded = body.encode("utf-16-le")
    start, end = utf16_offset(body, second.start), utf16_offset(body, second.end)
    assert encoded[2 * start : 2 * end].decode("utf-16-le") == second.raw
