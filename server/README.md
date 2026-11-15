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
| `worker` | `python -m ep.jobs.worker`: Procrastinate worker (import queue) + watcher | none |

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
| `EP_MEDIA_ROOTS` | `["<EP_LIBRARY_DIR>", "/watch"]` | JSON list: the only directories file sources may point into |
| `EP_TEST_DATABASE_URL` | `EP_DATABASE_URL` with database `ep_test` | used by tests only |
| `EP_WATCH_POLL_SECONDS` | `30` | watcher: seconds between polls of the watched dirs |
| `EP_WATCH_SETTLE_SECONDS` | `60` | watcher: quiet time before a changed folder is imported |
| `EP_WATCH_RESCAN_SECONDS` | `900` | watcher: seconds between full rescans |

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

- List endpoints load what they render in a fixed number of queries, however many rows:
  eager-load relationships (`ep.credits.with_credit` for artist credits, `joinedload`/
  `selectinload`/`contains_eager`), never a lazy load per row.
  `tests/test_query_counts.py` counts the statements. Index a column a query filters or
  joins on (Postgres does not index foreign keys) unless a unique constraint starts
  with it.

- OpenAPI operation IDs are the endpoint function names (`list_artists`, ...), for the
  generated Swift client. Keep endpoint function names unique.
- `PATCH` bodies default every field to `pydantic.MISSING`: omitted fields are left
  alone; only nullable columns accept `null`.
- Constraint violations (`IntegrityError` on flush or commit) become `409` through an app
  exception handler (`ep.api.errors`); endpoints just call `session.commit()`. Document
  expected errors per route with `responses=NOT_FOUND | CONFLICT` (body `ErrorRead`).
- A body that references a missing row is a `422` in FastAPI's `HTTPValidationError`
  shape (`ep.api.deps.get_or_422`), never a bare string detail.
- Artist credits are shared and never edited in place; use `ep.credits`
  (`get_or_create_artist_credit`, `delete_artist_credit_if_unused`).
- `external_id` has no foreign key: deleting an entity must call
  `ep.external_ids.delete_external_ids`.

## Sources, playback, covers

- `source` rows locate audio (`ep.models.source`): `file` (absolute path as the server
  sees it, e.g. `/library/...` or `/watch/Library/...`), `slice` (parent + `start_ms`/
  `end_ms`, relative to the parent) or a remote link. Deleting a source row never
  touches the file; a source with derived sources, or a recording with sources, is a 409.
- A `file` source's path must lie under one of `EP_MEDIA_ROOTS`, symlinks and `..`
  resolved (`ep.media_roots.is_within_media_roots`): checked on create/patch (422) and
  again on every stream (403). The API is unauthenticated; this keeps `/stream` from
  serving arbitrary files.
- `ep.playback.resolve` walks `parent_id` up to the nearest `file`, summing slice offsets
  (nested slices are clipped to their parent). `playback` on `SourceRead` and `TrackRead`
  is the result: stream `GET /sources/{id}/stream` (whole file, HTTP Range) and play
  `start_ms`..`end_ms`. Remote-only sources have `playback: null` and stream as a 409.
  For lists, `ep.playback.resolve_where` / `recording_playbacks` load the sources and
  all their ancestors in one recursive-CTE query and resolve them in memory.
- `ep.probe.probe(path)` reads codec/duration (`ffprobe`, in the image) and tags plus the
  embedded cover (`mutagen`). It never writes. `POST /sources` uses it to fill in a new
  file source's codec, duration and size.
- Covers are uploaded with `PUT /covers/release-group/{id}` and stored in
  `EP_LIBRARY_DIR/_covers/` (`ep.covers`), never next to files in watched directories.

## MusicBrainz, matching, search

- `ep.metadata.musicbrainz.MusicBrainz`: lookups by MBID, URL (a Bandcamp page resolves
  to its release), barcode and ISRC, searches (Lucene syntax, `lucene_query` builds
  one), and Cover Art Archive front covers. Typed Pydantic results. One request per
  second per process (`LIMITER`; the `api` and `worker` processes each have one),
  User-Agent `EP/<version> ( <contact email> )` (MusicBrainz asks for contact details),
  retries on 503. `get_release` fails rather than return a partial track list.
- `ep.importers.musicbrainz.import_release(session, release, recordings=None)` adds a
  release fetched with `get_release` to the catalogue (artists, credits, release group
  with `release_mbid`, recordings, tracks with `disc`, ISRCs), reusing rows by MBID and
  never overwriting them. Library artists and release groups without an MBID are reused
  (and gain it) when given (`artists=`, `release_group=`: the import framework passes
  what matching and the user decided) or, without that, on a confident match; recordings
  already at a track position gain the MBID of the track there if the titles agree.
  A release group already imported from another release raises `ReleaseConflict`. `recordings` maps track MBIDs to existing recordings (e.g. a loose
  YouTube track) to attach instead of creating duplicates: they gain the MBID and a
  track and keep their sources and external IDs (`RecordingConflict` if one has another
  MBID). `propose_recordings(session, release)` proposes that mapping with the matcher.
  `POST /musicbrainz/import` wraps the import (metadata only, no cover);
  `GET /musicbrainz/search` searches MusicBrainz.
- `ep.matching`: `match_artist`, `match_release_group`, `match_recording` take what the
  caller knows (text, MBID, ISRC/barcode/other external IDs, duration, the credit's
  individual artist names) and return candidates, best first, with a decision
  (`auto_match`, `needs_review`, `new`). Order: MBID, external IDs, then fuzzy (trigram
  candidates, rapidfuzz scores on normalised text, duration tolerance). Rows with a
  different MBID than the caller's are never candidates; titles whose numbers differ
  ("Movement 1"/"Movement 2") never auto-match. Thresholds are constants in
  `ep.matching.match`.
- `GET /search?q=` (autocomplete) ranks by trigram word similarity of the normalised
  text plus a prefix bonus, so short prefixes work ("bjo" finds "Björk"); a query of
  several words also matches "artist title" of recordings and release groups ("nina
  simone sinnerman"). `GET /resolve/{type}/{uuid}` finds an entity by internal ID or
  MBID (or a release group by its release MBID).
- Matching and search run in Postgres on indexes: `artist.name`, `artist.sort_name`, `artist_credit.name`,
  `release_group.title` and `recording.title` each have a stored generated
  `*_normalized` column (`ep_normalize(<column>)`, never set by code) with a GIN
  `gin_trgm_ops` index. `ep_normalize` (migration 0004) is the SQL twin of
  `ep.matching.normalize.normalize`; change both together, `tests/test_normalize_sql.py`
  checks that they agree on every character (except non-"Alphabetic" combining marks
  such as viramas: separators in Postgres, kept by Python). Queries are normalised by
  `ep_normalize` as well, in the statement that sets the threshold for the transaction
  (`ep.matching.sql.trigram_queries`), and use the pg_trgm operator `<%` (word
  similarity at least the threshold) and `LIKE` (index-backed), never the bare `word_similarity()`
  function as a filter; `tests/test_matching_indexes.py` checks that the plans can use
  the indexes. rapidfuzz still makes the decision: featured-artist/edition stripping,
  word order, number guard and per-artist credit scores are Python only.
- `scripts/bench_matching.py` loads a synthetic library (50k artists, 500k recordings)
  into a scratch database `ep_bench` and times matcher calls and searches, with
  `--explain` for query plans. Before stage 4b (no indexes): 0.8–11 s per call; after:
  4–200 ms for the matcher, 8–150 ms for search (400 ms for a one-letter query).
- MusicBrainz test fixtures are real responses in `tests/fixtures/musicbrainz/`, served
  by `tests/mock_musicbrainz.py`. Bandcamp pages (`tests/fixtures/bandcamp/`) and yt-dlp
  output (`tests/fixtures/ytdlp/`, formats and captions left out) are served by the
  fakes in `tests/fakes.py`; fake downloads are generated silent files. NTS API
  responses (`tests/fixtures/nts/`, the episode of the plan's fixture URL and its show)
  are served by `tests.fakes.nts_transport`.

## Jobs, imports, watcher

- Job queue: Procrastinate (`ep.jobs.queue`) in EP's database. Its schema is Alembic
  migration 0006, a frozen copy of the package's `schema.sql` in `alembic/procrastinate/`;
  `alembic/env.py` keeps its tables out of autogenerate. Upgrading Procrastinate: one
  revision per new script in `procrastinate schema --migrations-path`.
- Jobs are deferred on the caller's own connection, in the transaction that creates or
  re-queues the `import_job` row (`enqueue_import`, a queueing lock per job): a row and
  its queue entry commit together. The queue carries only "run import job <id>"; state,
  log, progress, proposal, answers and error live on `import_job`.
- The worker runs one job at a time (MusicBrainz is called only there, at 1 request/s),
  and the watcher in the same event loop. On start it re-queues jobs left `running` or
  `applying`, and queues `queued` jobs whose queue entry is missing.
- Import pipeline (`ep.importers.framework`): an importer's `build` fetches and resolves
  metadata into an `ep.importers.proposal.Proposal`; the framework matches it against the
  library (releases resolved to MusicBrainz and their artists too: by MBID, else against
  rows without MBID, which gain it), stops at `needs_review` when any issue is open,
  downloads, then applies it in one transaction (rows reused by MBID, sources by
  location: re-running creates nothing new). Errors make the job `failed` with `error`
  and a traceback in the log; nothing is ever dropped.
- The proposal is the API contract of the review UI: typed models only (no free-form
  JSON); where an item goes (`Target`) and covers (`IncomingCover`) are unions
  discriminated by `kind`.
- Issues have stable ids (`<kind>:<subject key>`); `POST /imports/{id}/review` stores
  answers by id and re-runs the job from the start, each stage applying the stored
  answer to the issue it would raise. An answer that no longer fits (its candidate is not
  offered any more) is ignored and the issue asked again. An item with an open issue is
  not matched further. The review, retry and dismiss endpoints lock the job row.
- Non-blocking issues (`Issue.blocking: false`): an issue about an `optional` track
  (radio tracklist entries), or about an artist only optional tracks need, does not hold
  up the job. It applies without those tracks (a timed one still gets its slice, without
  a recording) and ends in `needs_review` instead of `done`; answering and re-running
  adds them, reusing everything else. Tracks that map to a library recording keep its
  credit: their incoming artists are not matched. `IncomingTrack.external_ids` (Deezer,
  ACRCloud, NTS, ...) are matched like ISRCs and stored on the recording;
  `min_length_ms` (a radio play) drops shorter fuzzy candidates without being taken as
  the recording's length.
- Downloads: an importer only describes them (`IncomingMedium.download` with an
  `origin`); after review the framework calls `Importer.download(ctx, medium, directory)`
  with a per-medium work directory under `EP_LIBRARY_DIR/.downloads/<job id>/`, kept
  across failures and re-runs (a finished download is reused, a partial one handed to the
  importer again), reads the file's facts, and on apply links (or moves) it to
  `Importer.library_path` in the library (default `Artist/Release Group/NN Title.ext`),
  undone if the transaction fails. The work directory is deleted when the job is done or
  dismissed. A medium whose origin is already the parent of a file source is not
  downloaded again.
- A new importer: subclass `Importer`, register it in `ep.jobs.tasks.IMPORTERS`, describe
  everything as `Proposal` items (single-file albums: tracks with `start_ms`/`end_ms` on
  one medium; MusicBrainz credits through `framework.mb_credit`). Rows outside the
  catalogue (radio) go in further `Proposal` fields and `Importer.apply`, which gets the
  recordings and sources the tracks became (`Applied`), in the apply transaction.
- Folder importer (`ep.importers.folder`): see its docstring. Files are indexed in place
  (`managed=false`, with `sha256`); covers go through `ep.covers`.
- Resolving to MusicBrainz is shared (`ep.importers.resolve`): `search_release` (title,
  artist, track count; a few candidates fetched and checked by position and length;
  otherwise-exact candidates that do not fit are an `uncertain_metadata` issue),
  `first_fitting` (releases from a URL or barcode lookup), `align_by_title` (playlists),
  `use_musicbrainz`. Not in MusicBrainz is a normal outcome: imported as entered.
- Cover URLs (`UrlCover`) are fetched by the framework before downloading
  (`Importer.fetch_cover`), into the job's work directory; a cover that cannot be
  fetched is logged, not an issue.
- Bandcamp importer (`ep.importers.bandcamp`, metadata `ep.metadata.bandcamp`, a port of
  Harmony's Bandcamp provider): input an album or track page URL. The artist is the
  release's artist field, never the page owner (a label page: `label` in the proposal);
  Bandcamp tags are kept in the proposal (`tags`). MusicBrainz by page URL, barcode,
  then search. One `bandcamp` source per track page, parent of the downloaded file.
  Tracks that cannot be streamed (pre-orders) are imported without audio (logged).
- YouTube importer (`ep.importers.youtube`): a video is a loose recording, or with
  chapters a single-file album (slices); a playlist is one release group, one file per
  entry. yt-dlp's artist/track/album fields, else "Artist - Title" from the title
  without video noise (`clean_title`; YouTube only, not matching), else the channel (or
  the playlist's artist). MusicBrainz: recording search (one fitting hit, else an
  issue), release search (chapters by position; playlist entries by title, entries not
  on the release are an issue each). A video whose metadata cannot be read is a
  `metadata_not_found` issue for that entry only.
- yt-dlp (`ep.metadata.ytdlp`) runs as `python -m yt_dlp --ignore-config` of the
  project environment: the `yt-dlp[default,deno]` dependency brings the EJS scripts and
  Deno, the JavaScript runtime YouTube needs (no system packages). `--dump-single-json`
  (with `--flat-playlist` for playlists) for metadata; downloads with `-x` and no
  `--audio-format`, so the original codec is kept (opus, m4a, mp3), resuming `.part`
  files; progress goes to the job log. URLs are passed after `--` and must be `http(s)`
  (`YtDlp`); importers download only canonical YouTube video, Bandcamp track, SoundCloud
  track and NTS episode URLs (`ep.importers.download.is_downloadable`). Metadata calls time out after 5 minutes, downloads after 3
  hours (a watchdog kills a silent yt-dlp). `youtube_url` / `bandcamp.canonical_url`
  canonicalise input URLs (the job input and the source `uri`).
- Web requests other than MusicBrainz (Bandcamp pages, covers: `ep.metadata.web`) send
  `User-Agent: EP/<version>`, without contact details.
- Known limitation (2026-11): Bandcamp's CDN answers requests from the Docker image
  (Debian's OpenSSL) with a bot check instead of the page, and yt-dlp's Bandcamp
  extractor then wants browser impersonation (`curl_cffi`). From macOS both work. The
  job stops at `needs_review` with a `fetch_failed` issue ("Bandcamp answered with a bot
  check", `BandcampBlocked`) whose only choice is retry; EP does not try to get around
  the check.
- NTS importer (`ep.importers.nts`, metadata `ep.metadata.nts`): see "Radio" below.
- Watcher (`ep.jobs.watcher`): `watchfiles` polling plus periodic rescans; a watch dir's
  `baseline_at` keeps what was there at registration from being imported automatically
  (`POST /watch-dirs/{id}/scan` does that on request). "Changed" is the newest mtime or
  ctime in the folder (hidden entries left out); through the macOS Docker bind mount both
  are the host's and changes are seen at once. ctime catches folders moved or copied in
  with old mtimes, but also moves on extended-attribute changes (opening a file from the
  Finder) and on `.DS_Store` creation: that re-offers a folder only if some of its files
  are not sources.

## Radio

- Tables (`ep.models.radio`), apart from the catalogue: `radio_show` (station + slug
  unique), `radio_episode` (URL unique; `source_id` is the episode's audio file),
  `radio_episode_track` (position, the station's raw artist/title text, the matched
  `recording_id` or null, the `slice` source that plays the entry or null, whether the
  time is an estimate). The entry's time range is its slice's `start_ms`/`end_ms`.
  Show and episode names have `*_normalized` trigram columns for `/search`.
- `ep.metadata.nts`: NTS's undocumented public API (`/api/v2/shows/{show}/episodes/
  {episode}` with the tracklist embedded, `/api/v2/shows/{show}`), validated against
  typed models; any other shape (or a truncated tracklist) is `NtsUnexpectedResponse`,
  an `unexpected_response` issue with retry. A missing episode is `metadata_not_found`,
  network/server errors `fetch_failed`. Times: the detected `offset`/`duration`, else
  `offset_estimate`/`duration_estimate` (`offset_is_estimate`), else no slice.
  `musicbrainz_track_id` is a **recording** MBID (checked against MusicBrainz).
- NTS import (input: an episode page URL): the audio is downloaded once with `yt-dlp -x`
  from the episode's SoundCloud upload (else the episode page) into
  `_radio/NTS/<Show>/<YYYY-MM-DD Episode>.<ext>` (6 h timeout); sources `nts` (page) →
  `soundcloud` → `file` → one `slice` per timed entry. Entries are optional loose tracks
  matched by MBID, ISRC/Deezer/ACRCloud/NTS uid, then fuzzy artist + title (the
  detected duration only as a lower bound): confident ones are used, clear misses become
  new standalone recordings and artists, the uncertain ones are non-blocking issues (the
  episode applies with them unmatched). A library recording matched by text keeps its
  metadata (no MBID from NTS). Images go to `_covers/radio-show/`, `_covers/radio-episode/`
  (`GET /covers/radio-show/{id}`, `/covers/radio-episode/{id}`). Re-importing updates
  the rows; entries in the library keep their recording and range (manual edits stand).
  Genres and moods stay in the proposal for tags. From the Docker image (2026-11) the
  NTS API and the SoundCloud download both work; a bot check would be a
  `fetch_failed`/`download_failed` issue with retry, never worked around.
- Radio-only (`ep.radio`): a recording that appears in a tracklist, is no track, and
  whose every source is an entry's slice; an artist on no release group whose (at least
  one) recordings are all radio-only. Correlated `EXISTS` in the query
  (`visible_artists`, `visible_recordings`); `GET /artists`, `/artists/{id}/recordings`
  and `/search` hide them unless `include_radio_only=true`. Playback
  (`recording_playbacks`) prefers a library source to a radio slice.
- Endpoints: `GET /radio/shows`, `/radio/shows/{id}`, `/radio/shows/{id}/episodes`,
  `/radio/episodes/{id}` (tracklist with raw text, range, estimate flag, recording,
  `in_library`/`library_playback`, the slice's and the episode's `playback`), `PATCH`
  and `DELETE` on shows and episodes, `PATCH /radio/episode-tracks/{id}` (re-point to a
  recording or `null`, change the range; the slice follows),
  `GET /recordings/{id}/radio-appearances`, `/artists/{id}/radio-appearances`.
  `DELETE` an episode removes its tracklist, slices, file source and link sources, and
  the recordings (and then uncredited artists) only it knew; the audio file only with
  `delete_audio=true` (managed files under the library only).

## Tests

Tests run against a real Postgres: the `db` service must be up. The test session drops
and recreates the `ep_test` database, migrates it to head, and wraps each test in a
transaction that is rolled back. The job queue is Procrastinate's in-memory connector
(`job_queue` fixture); `tests.imports.run_queued` runs queued jobs as the worker would.
Audio fixtures are generated with `ffmpeg` (must be on
the `PATH`) into temp dirs; the managed library is a temp dir too.

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
