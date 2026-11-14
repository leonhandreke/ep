"""The matcher's and search's trigram queries can use the trigram indexes.

The test database is tiny, so Postgres would scan the tables anyway; with sequential
and plain index scans disabled, a query whose conditions the index cannot serve (a
function instead of an operator, an expression instead of the indexed column) still
falls back to a sequential scan.
"""

import re
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlmodel import Session

from ep.matching import match_artist, match_recording, match_release_group
from ep.matching.search import search
from tests.test_matching import artist, recording, release_group

CASES: list[tuple[str, Callable[[Session], object]]] = [
    ("match_artist", lambda s: match_artist(s, "Nina Simone")),
    ("match_recording", lambda s: match_recording(s, "Sinnerman", artist="Nina Simone")),
    ("match_recording_no_artist", lambda s: match_recording(s, "Sinnerman")),
    ("match_release_group", lambda s: match_release_group(s, "Pastel Blues", artist="Nina")),
    ("search_one_word", lambda s: search(s, "sinner")),
    ("search_artist_title", lambda s: search(s, "nina simone sinnerman")),
]


@pytest.mark.parametrize(("name", "case"), CASES, ids=[name for name, _ in CASES])
def test_trigram_conditions_use_indexes(
    session: Session, name: str, case: Callable[[Session], object]
) -> None:
    nina = artist(session, "Nina Simone")
    recording(session, "Sinnerman", nina)
    release_group(session, "Pastel Blues", nina)
    statements: list[tuple[str, Any]] = []

    def capture(conn: Any, cursor: Any, statement: str, parameters: Any, *args: Any) -> None:
        if "<%" in statement:
            statements.append((statement, parameters))

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", capture)
    try:
        case(session)
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert statements

    # Leaves bitmap scans, the only kind a GIN index supports, which need a condition.
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    connection.execute(text("SET LOCAL enable_indexscan = off"))
    for statement, parameters in statements:
        plan = "\n".join(
            line for (line,) in connection.exec_driver_sql("EXPLAIN " + statement, parameters)
        )
        searched = set(re.findall(r"(\w+)\.\w+_normalized\b", statement))
        assert searched
        for table in searched:
            assert not re.search(rf"Seq Scan on {table}\b", plan), plan
        assert "_normalized_trgm" in plan, plan
