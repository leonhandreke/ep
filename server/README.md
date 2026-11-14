# EP server

FastAPI + SQLModel + Alembic on Postgres 17. Design: [`../plans/backend.md`](../plans/backend.md).

The database layer is **synchronous** (SQLModel `Session`, psycopg 3, plain `def`
endpoints run in FastAPI's thread pool). Keep it that way.

Configuration is in `pyproject.toml` wherever the tool allows it (uv, Alembic, pytest,
ruff, ty). Git hooks are configured in `prek.toml` (here and at the repo root).

## Services and mounts

`docker compose` (run in `server/`) starts:

| Service | What | Host port |
|---|---|---|
| `db` | Postgres 17, user/password/db `ep`/`ep`/`ep` | `127.0.0.1:5432` |
| `api` | `uvicorn ep.main:app` | `8000` |
| `worker` | placeholder until stage 5 (Procrastinate) | none |

`api` and `worker` share one image (Python 3.13, `ffmpeg`, `yt-dlp`) and these mounts:

| Host | Container | Mode |
|---|---|---|
| `~/Music/ep-Library` | `/library` | read-write (managed library) |
| `~/Music/Library` | `/watch/Library` | **read-only** (watched directory) |

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `EP_DATABASE_URL` | `postgresql+psycopg://ep:ep@localhost:5432/ep` | SQLAlchemy URL |
| `EP_LIBRARY_DIR` | `/library` | managed library directory |
| `EP_WATCH_DIRS` | `[]` | JSON list of watched directories, e.g. `["/watch/Library"]` |
| `EP_TEST_DATABASE_URL` | `EP_DATABASE_URL` with database `ep_test` | used by tests only |

## Start, migrate

```sh
cd server
docker compose up -d --build
docker compose run --rm api alembic upgrade head
curl localhost:8000/health        # {"status":"ok","database":"ok"}
docker compose down               # stop (data stays in the db-data volume)
```

New migration after changing models (models must be imported in `ep/models/__init__.py`):

```sh
uv run alembic revision --autogenerate -m "add catalogue"
```

## Tests

Tests run against a real Postgres: the `db` service must be up. The test session drops
and recreates the `ep_test` database, migrates it to head, and wraps each test in a
transaction that is rolled back.

```sh
cd server
uv sync
docker compose up -d db
uv run pytest                     # network tests are deselected by default
uv run pytest -m network          # opt-in: tests that hit the network or download audio
```

Inside the container instead: `docker compose run --rm api pytest`.

## Lint, types, hooks

```sh
uv run ruff check --fix . && uv run ruff format . && uv run ty check
```

prek hooks (builtins at the repo root and in `server/`, plus ruff, ty and
`uv lock --check` for `server/`). Run from the **repo root** so both projects run:

```sh
uv run --project server prek install          # once: installs the git pre-commit hook
uv run --project server prek run --all-files
```
