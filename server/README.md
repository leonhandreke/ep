# EP server

FastAPI + SQLModel + Alembic on Postgres 18, Python 3.15. Design: [`../plans/backend.md`](../plans/backend.md).

The database layer is **synchronous** (SQLModel `Session`, psycopg 3, plain `def`
endpoints run in FastAPI's thread pool). Keep it that way.

Configuration is in `pyproject.toml` wherever the tool allows it (uv, Alembic, pytest,
ruff, ty). Git hooks are configured in `prek.toml` (repo root: builtin checks on all
files; `server/`: ruff, ty, `uv lock --check`).

## Services and mounts

`docker compose` (run in `server/`) starts:

| Service | What | Host port |
|---|---|---|
| `db` | Postgres 18, user/password/db `ep`/`ep`/`ep` | `127.0.0.1:5432` |
| `api` | `uvicorn ep.main:app` | `8000` |
| `worker` | placeholder until stage 5 (Procrastinate) | none |

`api` and `worker` share one image and these mounts:

| Host | Container | Mode |
|---|---|---|
| `~/Music/ep-Library` | `/library` | read-write (managed library) |
| `~/Music/Library` | `/watch/Library` | **read-only** (watched directory) |

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `EP_DATABASE_URL` | `postgresql+psycopg://ep:ep@localhost:5432/ep` | SQLAlchemy URL |
| `EP_LIBRARY_DIR` | `/library` | managed library directory |
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

## API conventions

- OpenAPI operation IDs are the endpoint function names (`list_artists`, ...), for the
  generated Swift client. Keep endpoint function names unique.
- `PATCH` bodies default every field to `pydantic.MISSING`: omitted fields are left
  alone; only nullable columns accept `null`.
- Constraint violations on commit (`ep.api.deps.commit`) become `409`.
- Artist credits are shared and never edited in place; use `ep.credits`
  (`get_or_create_artist_credit`, `delete_artist_credit_if_unused`).
- `external_id` has no foreign key: deleting an entity must call
  `ep.api.external_ids.delete_external_ids`.

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

prek hooks: run from the **repo root** so both the root and the `server/` project run:

```sh
uv run --project server prek install          # once: installs the git pre-commit hook
uv run --project server prek run --all-files
```
