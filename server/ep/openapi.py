"""Write the OpenAPI document of the API to ``server/openapi.json`` (the contract the
clients are generated from): ``uv run python -m ep.openapi``; ``--check`` only tells
whether the file is up to date (exit status 1 if not)."""

import argparse
import json
import sys
from pathlib import Path

from ep.main import app

PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def document() -> str:
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only check that it is current")
    args = parser.parse_args()
    current = document()
    if args.check:
        if not PATH.is_file() or PATH.read_text() != current:
            print(f"{PATH} is out of date: run `uv run python -m ep.openapi`", file=sys.stderr)
            return 1
        return 0
    PATH.write_text(current)
    print(f"wrote {PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
