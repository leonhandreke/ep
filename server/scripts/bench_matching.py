"""Benchmark the matcher and ``/search`` on a synthetic library.

Generates a library (default 50k artists, 500k recordings) into a scratch database on
the compose Postgres (never the dev database), then times matcher calls and searches
and prints ``EXPLAIN (ANALYZE)`` of their SQL::

    uv run python scripts/bench_matching.py load      # (re)create ep_bench, migrate, fill
    uv run python scripts/bench_matching.py bench     # timings and query plans
    uv run python scripts/bench_matching.py drop      # drop ep_bench

``load --revision 0003`` migrates to an older revision, to compare implementations.
The data is random but seeded, so every load is the same.
"""

import argparse
import io
import random
import statistics
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, make_url, text
from sqlmodel import Session

from ep.matching import match_artist, match_recording, match_release_group
from ep.matching.search import search
from ep.settings import get_settings

SERVER_DIR = Path(__file__).resolve().parent.parent
DATABASE = "ep_bench"

SYLLABLES = [
    "ba",
    "be",
    "bi",
    "bo",
    "bu",
    "ka",
    "ke",
    "ki",
    "ko",
    "ku",
    "la",
    "le",
    "li",
    "lo",
    "lu",
    "ma",
    "me",
    "mi",
    "mo",
    "mu",
    "na",
    "ne",
    "ni",
    "no",
    "nu",
    "ra",
    "re",
    "ri",
    "ro",
    "ru",
    "sa",
    "se",
    "si",
    "so",
    "su",
    "ta",
    "te",
    "ti",
    "to",
    "tu",
    "va",
    "ve",
    "vi",
    "vo",
    "da",
    "de",
    "di",
    "do",
    "du",
    "ga",
    "ge",
    "go",
    "gu",
    "fa",
    "fe",
    "fi",
    "fo",
    "sha",
    "sho",
    "chi",
    "cha",
    "ten",
    "mar",
    "lin",
    "son",
    "ber",
    "gar",
    "dor",
    "fen",
    "wil",
    "ston",
]
COMMON = [
    "love",
    "the",
    "you",
    "me",
    "night",
    "day",
    "heart",
    "time",
    "go",
    "home",
    "light",
    "dark",
    "blue",
    "rain",
    "fire",
    "water",
    "dream",
    "song",
    "dance",
    "man",
    "woman",
    "girl",
    "boy",
    "world",
    "life",
    "sun",
    "moon",
    "star",
    "sky",
    "river",
    "road",
    "city",
    "baby",
    "little",
    "old",
    "new",
    "black",
    "white",
    "red",
    "gold",
    "summer",
    "winter",
    "morning",
    "true",
    "lost",
    "free",
    "wild",
]
ACCENTS = str.maketrans("aeiouc", "áéíöüç")
SUFFIXES = (
    " (Remastered 2011)",
    " - 2009 Remaster",
    " (Live)",
    " (Radio Edit)",
    " [Deluxe Edition]",
    " Part II",
    " (feat. Somebody)",
)


def word(rng: random.Random) -> str:
    return "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(1, 3)))


def title(rng: random.Random, vocabulary: list[str]) -> str:
    words = [
        rng.choice(COMMON) if rng.random() < 0.35 else rng.choice(vocabulary)
        for _ in range(rng.choices((1, 2, 3, 4, 5), (25, 35, 25, 10, 5))[0])
    ]
    text = " ".join(words).title()
    if rng.random() < 0.08:
        text += rng.choice(SUFFIXES)
    return text


def artist_name(rng: random.Random, names: list[str]) -> str:
    name = " ".join(rng.choice(names) for _ in range(rng.choices((1, 2, 3), (30, 55, 15))[0]))
    name = name.title()
    if rng.random() < 0.1:
        name = name.translate(ACCENTS)
    if rng.random() < 0.08:
        name = "The " + name
    return name


def copy(conn: Any, table: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    with (
        conn.cursor() as cursor,
        cursor.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp,
    ):
        for row in rows:
            cp.write_row(row)


def generate(engine: Engine, artists: int, recordings: int, seed: int = 4) -> None:
    rng = random.Random(seed)
    vocabulary = sorted({word(rng) for _ in range(6000)})
    names = sorted({word(rng) for _ in range(4000)})
    now = datetime.now(UTC)
    stamp = (now, now)

    def uid() -> uuid.UUID:
        return uuid.UUID(int=rng.getrandbits(128), version=4)

    artist_rows = []
    for index in range(artists):
        name = artist_name(rng, names)
        sort_name = name.removeprefix("The ") + (", The" if name.startswith("The ") else "")
        mbid = uid() if rng.random() < 0.6 else None
        artist_rows.append((uid(), *stamp, mbid, name, sort_name, "", None, index))
    planted = ["Björk", "Nina Simone", "Floating Points", "Pharoah Sanders", "The Beatles"]
    for name in planted:
        artist_rows.append((uid(), *stamp, None, name, name, "", None, -1))

    credit_rows: list[tuple[Any, ...]] = []
    credit_name_rows: list[tuple[Any, ...]] = []

    def add_credit(parts: list[tuple[Any, ...]], joins: list[str]) -> uuid.UUID:
        credit_id = uid()
        name = "".join(part[4] + join for part, join in zip(parts, joins, strict=True))
        credit_rows.append((credit_id, *stamp, name))
        for position, (part, join) in enumerate(zip(parts, joins, strict=True)):
            credit_name_rows.append((uid(), *stamp, credit_id, part[0], position, part[4], join))
        return credit_id

    credits = [add_credit([row], [""]) for row in artist_rows]
    for _ in range(artists // 5):
        parts = rng.sample(artist_rows, rng.randint(2, 3))
        joins = [rng.choice((" & ", ", ", " feat. ")) for _ in parts[:-1]] + [""]
        credits.append(add_credit(parts, joins))
    by_name = {row[4]: credits[i] for i, row in enumerate(artist_rows)}

    release_group_rows = []
    for _ in range(artists):
        release_group_rows.append(
            (uid(), *stamp, None, None, title(rng, vocabulary), rng.choice(credits), "album")
        )
    recording_rows = []
    for _ in range(recordings):
        recording_rows.append(
            (
                uid(),
                *stamp,
                uid() if rng.random() < 0.6 else None,
                title(rng, vocabulary),
                rng.choice(credits),
                rng.randint(60_000, 600_000),
            )
        )
    for name, song in (
        ("Nina Simone", "Sinnerman"),
        ("Nina Simone", "Feeling Good"),
        ("Björk", "Jóga"),
        ("The Beatles", "Here Comes the Sun"),
        ("Floating Points", "Promises: Movement 1"),
    ):
        recording_rows.append((uid(), *stamp, None, song, by_name[name], 300_000))
    release_group_rows.append(
        (uid(), *stamp, None, None, "Pastel Blues", by_name["Nina Simone"], "album")
    )

    track_rows = []
    for index, row in enumerate(recording_rows):
        release_group = release_group_rows[index % len(release_group_rows)]
        position = index // len(release_group_rows) + 1
        track_rows.append((uid(), *stamp, release_group[0], row[0], 1, position))
    external_rows = [
        (uid(), *stamp, "recording", row[0], "isrc", f"XX{rng.randrange(10**10):010d}")
        for row in recording_rows
    ]

    base = ["id", "created_at", "updated_at"]
    raw = engine.raw_connection()
    try:
        conn = raw.driver_connection
        assert conn is not None
        copy(
            conn,
            "artist",
            [*base, "mbid", "name", "sort_name", "disambiguation", "type"],
            [row[:-1] for row in artist_rows],
        )
        copy(conn, "artist_credit", [*base, "name"], credit_rows)
        copy(
            conn,
            "artist_credit_name",
            [*base, "artist_credit_id", "artist_id", "position", "credited_name", "join_phrase"],
            credit_name_rows,
        )
        copy(
            conn,
            "release_group",
            [*base, "mbid", "release_mbid", "title", "artist_credit_id", "type"],
            release_group_rows,
        )
        copy(
            conn,
            "recording",
            [*base, "mbid", "title", "artist_credit_id", "length_ms"],
            recording_rows,
        )
        copy(
            conn,
            "track",
            [*base, "release_group_id", "recording_id", "disc", "position"],
            track_rows,
        )
        copy(
            conn,
            "external_id",
            [*base, "entity_type", "entity_id", "scheme", "value"],
            external_rows,
        )
        conn.commit()
        conn.autocommit = True
        conn.execute("VACUUM ANALYZE")
    finally:
        raw.close()
    print(
        f"{len(artist_rows)} artists, {len(credit_rows)} credits, "
        f"{len(release_group_rows)} release groups, {len(recording_rows)} recordings, "
        f"{len(track_rows)} tracks, {len(external_rows)} external IDs"
    )


def bench_url() -> str:
    url = make_url(get_settings().database_url).set(database=DATABASE)
    return url.render_as_string(hide_password=False)


def recreate(revision: str) -> Engine:
    url = make_url(bench_url())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{DATABASE}"'))
    admin.dispose()
    upgrade(revision)
    return create_engine(url)


def upgrade(revision: str) -> None:
    config = Config(toml_file=SERVER_DIR / "pyproject.toml")
    config.attributes["database_url"] = bench_url()
    command.upgrade(config, revision)


@contextmanager
def captured(engine: Engine) -> Generator[list[tuple[str, Any]]]:
    statements: list[tuple[str, Any]] = []

    def capture(conn: Any, cursor: Any, statement: str, parameters: Any, *args: Any) -> None:
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


CASES: list[tuple[str, Callable[[Session], object]]] = [
    ("match_artist('Bjork')", lambda s: match_artist(s, "Bjork")),
    ("match_artist('Nina Simone')", lambda s: match_artist(s, "Nina Simone")),
    (
        "match_recording('Sinnerman', 'Nina Simone')",
        lambda s: match_recording(s, "Sinnerman", artist="Nina Simone", length_ms=301_000),
    ),
    (
        "match_recording('Here Comes the Sun - 2009 Remaster', 'Beatles')",
        lambda s: match_recording(s, "Here Comes the Sun - 2009 Remaster", artist="Beatles"),
    ),
    ("match_recording('Love', no artist)", lambda s: match_recording(s, "Love")),
    (
        "match_release_group('Pastel Blues', 'Nina Simone')",
        lambda s: match_release_group(s, "Pastel Blues", artist="Nina Simone"),
    ),
    ("match_recording(isrc=...)", lambda s: match_recording(s, isrc="XX0000012345")),
    ("search('bjo')", lambda s: search(s, "bjo")),
    ("search('sinnerman')", lambda s: search(s, "sinnerman")),
    ("search('nina simone sinnerman')", lambda s: search(s, "nina simone sinnerman")),
    ("search('the beatles here comes')", lambda s: search(s, "the beatles here comes")),
    ("search('love')", lambda s: search(s, "love")),
    ("search('b')", lambda s: search(s, "b")),
]


def bench(runs: int, explain: bool) -> None:
    engine = create_engine(bench_url())
    with Session(engine) as session:
        session.connection()  # warm up the pool
        for name, case in CASES:
            timings = []
            for _ in range(runs):
                start = time.perf_counter()
                case(session)
                timings.append((time.perf_counter() - start) * 1000)
                session.rollback()
            print(f"{statistics.median(timings):9.1f} ms  {name}")
            if not explain:
                continue
            with captured(engine) as statements:
                case(session)
            for statement, parameters in statements:
                if not statement.lstrip().upper().startswith("SELECT"):
                    continue
                if "set_config" in statement:
                    session.connection().exec_driver_sql(statement, parameters)
                    continue
                plan = session.connection().exec_driver_sql(
                    "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON) " + statement, parameters
                )
                out = io.StringIO()
                for (line,) in plan:
                    print("      " + line, file=out)
                print(out.getvalue())
            session.rollback()
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load")
    load.add_argument("--artists", type=int, default=50_000)
    load.add_argument("--recordings", type=int, default=500_000)
    load.add_argument("--revision", default="head")
    migrate = sub.add_parser("upgrade")
    migrate.add_argument("--revision", default="head")
    run = sub.add_parser("bench")
    run.add_argument("--runs", type=int, default=5)
    run.add_argument("--explain", action="store_true")
    sub.add_parser("drop")
    args = parser.parse_args()
    match args.command:
        case "load":
            engine = recreate(args.revision)
            start = time.perf_counter()
            generate(engine, args.artists, args.recordings)
            print(f"loaded in {time.perf_counter() - start:.0f} s")
            engine.dispose()
        case "upgrade":
            start = time.perf_counter()
            upgrade(args.revision)
            print(f"upgraded in {time.perf_counter() - start:.0f} s")
        case "bench":
            bench(args.runs, args.explain)
        case "drop":
            admin = create_engine(
                make_url(bench_url()).set(database="postgres"), isolation_level="AUTOCOMMIT"
            )
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))
            admin.dispose()


if __name__ == "__main__":
    main()
