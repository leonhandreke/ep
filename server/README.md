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
| `EP_RUN_WORKER` | `false` | run the import worker and the watcher inside the API process (development: one `uvicorn --reload`) |
| `EP_WATCH_POLL_SECONDS` | `30` | watcher: seconds between polls of the watched dirs |
| `EP_WATCH_SETTLE_SECONDS` | `60` | watcher: quiet time before a changed folder is imported |
| `EP_WATCH_RESCAN_SECONDS` | `900` | watcher: seconds between full rescans |
| `EP_HOST_PATHS` | `{}` | JSON object: server directory → its path on the host (`ep.host_paths`; compose sets the two mounts). Empty: the server runs on the host |

## Start, migrate

```sh
cd server
docker compose up -d --build
docker compose run --rm api alembic upgrade head
curl localhost:8000/health        # {"status":"ok","database":"ok"}
docker compose down               # stop (data stays in the db-data volume)
```

On the host instead, with only Postgres in Docker (one process: API, import worker and
watcher; a reload waits for a running import to finish):

```sh
cd server
docker compose up -d db
uv run alembic upgrade head
EP_RUN_WORKER=1 EP_LIBRARY_DIR="$HOME/Music/ep-Library" \
EP_MEDIA_ROOTS="[\"$HOME/Music/ep-Library\", \"$HOME/Music/Library\"]" \
    uv run uvicorn ep.main:app --port 8000 --reload
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

- Paged lists take `limit` (1–1000, default 100) and `offset` (`ep.api.deps.PageDep`)
  and return a bare JSON array; every one reports the length of the whole list (with
  its filters) in the `X-Total-Count` header (`Paging.apply`; `responses=PAGED`
  documents it, `tests/test_openapi.py` checks that every endpoint with `offset` does).
- The OpenAPI document is committed as `openapi.json` (clients are generated from it):
  `uv run python -m ep.openapi` rewrites it, `--check` tells whether it is current, and
  `tests/test_openapi.py` fails when it is stale. Operation IDs are the endpoint function
  names (`list_artists`, ...): keep them unique. Request and response bodies are named
  schemas (no generated `Body_...`); uploads are the raw file
  (`PUT /covers/release-group/{id}` with `Content-Type: image/png`, ...).
- `PATCH` bodies default every field to `pydantic.MISSING`: an omitted field is left
  alone, an explicit `null` clears a nullable field (as in JSON Merge Patch), and a
  non-nullable field rejects `null` (422). The schema says which fields are nullable
  (`anyOf` with `null`); clients must be able to send an explicit `null`.
- Timestamps in responses (`ep.models.base.Timestamp`) are RFC 3339 in UTC with exactly
  three fractional digits (`2026-11-15T21:02:07.123Z`): configure the client's date
  decoding for fractional seconds (e.g. `.iso8601WithFractionalSeconds` in
  swift-openapi-runtime). Timestamps in requests may have any precision and offset.
  Partial dates (release dates) are strings `YYYY[-MM[-DD]]`.
- Constraint violations (`IntegrityError` on flush or commit) become `409` through an app
  exception handler (`ep.api.errors`); endpoints just call `session.commit()`. Document
  expected errors per route with `responses=NOT_FOUND | CONFLICT` (body `ErrorRead`).
  A value the database cannot store (`DataError`: a NUL character, a number out of
  range) is a `422` in the `HTTPValidationError` shape, never a 500.
- Request bodies have no required nullable fields: a field that may be null has a
  default, so clients may leave it out (`NoteWrite.base_updated_at`).
- Text ranges in responses (`TextRange`: note links, snippet highlights) are in UTF-16
  code units, `end` exclusive, as NSString/NSRange count; the schema says so.
- A body that references a missing row is a `422` in FastAPI's `HTTPValidationError`
  shape (`ep.api.deps.get_or_422`), never a bare string detail.
- Artist credits are shared and never edited in place; use `ep.credits`
  (`get_or_create_artist_credit`, `delete_artist_credit_if_unused`).
- `external_id`, `note` and `entity_tag` have no foreign key to the entity: deleting an
  entity must call `ep.external_ids.delete_external_ids` and
  `ep.notes.store.delete_annotations` (note and tags deleted, links to it unresolved).
  Deleting as a side effect (radio clean-up) must spare annotated entities
  (`ep.notes.store.annotated`).
- Entities named in a path by "MBID or ID" (`/notes`, `/backlinks`, `/entity-tags`,
  `/entity-details`, `/resolve/{type}/{id}`) go through `ep.entities.resolve_ref`; mixed-type lists render entities
  with `ep.api.entities.entity_reads` (`EntityRead`: title, subtitle, cover, link).

## What clients read (the app's stage 0, `plans/frontend.md`)

- `GET /server`: version, library counts (artists shown by default and radio-only ones,
  release groups, recordings, tracks, shows, episodes), the library dir and media roots
  with their host paths.
- Host paths (`ep.host_paths`, `EP_HOST_PATHS`): responses carry `host_path` next to
  server paths (file sources, `FileRead`, watch dirs, folder job inputs) for a file
  manager; inputs (folder imports, watch dirs and their scans, a file source's `uri` on
  create and patch: "Locate…") accept a host path in a mapped directory and store the
  server path.
- Play states (`ep.playback.playabilities`, `PlayabilityRead` on tracks and recordings):
  `file`, `slice` (range of a shared file), `radio_excerpt`, `file_missing` (not at its
  path: `missing_file_source_id` to locate, a radio excerpt as `playback` if there is
  one; also a radio-only recording whose episode file is gone), `link_only` (`link` to open), `no_source`. What plays: the recording's
  `preferred_source_id` if it plays, else the oldest library source whose file is
  there, else the oldest radio excerpt. One query for a whole list, plus a `stat` per
  file tried. `PlaybackRead.file_duration_ms` is a scrubber's scale; `media_type` (the
  stream's `Content-Type`) and `codec` say which decoder the file needs, before any of it
  is fetched.
- `GET /recordings/{id}/sources` (`RecordingSourceRead`): each source's role, its
  ancestors (where a file came from), the file it plays from (codec, sample rate, bit
  depth, bit rate, channels, duration, size, host path, whether it is there), the import
  that added that file (`imported_by`, from `Source.meta.import_job_id`; null for one
  added by hand), the radio entry of an excerpt,
  whether it can be chosen (`playable`), whether it is preferred and whether it is the
  one that will play (`will_play`: the preferred source if its file is there, else the
  default choice). The changes made from that list answer with the whole list, so a
  client needs no second request: `PUT /recordings/{id}/preferred-source`
  `{"source_id": ...}` chooses (null: the default), `POST /recordings/{id}/sources` adds
  a source, `PATCH /recordings/{id}/sources/{source_id}` changes one the recording plays
  from (also an ancestor: "Locate…" on a shared album or episode file). The generic
  `/sources` endpoints answer with the one source. `SourceRead` has typed stream facts;
  the extractor leftovers (`source.meta`) are not in the API.
- Details with counts: `GET /artists/{id}` (release groups, loose recordings, release
  groups they appear on, radio episodes, radio-only), `GET /release-groups/{id}` (tracks, discs, length, tracks per
  play state, kinds of link of link-only tracks), `GET /radio/shows/{id}` (episodes).
  `GET /artists/{id}/appearances`: release groups not credited to the artist with a
  track whose recording is ("Bonobo feat. Rhye" on Bonobo's album lists it for Rhye).
  `GET /artists?q=` filters by name (index-backed `LIKE` on the normalised columns, in
  sort order; `/search` ranks). `GET /artists/position` (same `include_radio_only` and
  `q`) gives the offset of a row in that list, for loading the page that has it: of an
  artist (`id`; navigate-to) or of the first artist sorting at or after a text
  (`sort_name`; type-to-select, in the database collation's order, the list's own).
- Edits: `PUT /release-groups/{id}/track-order` (every track's disc and position in
  one request), `POST /tracks` with `new_recording` (credited like the release group
  unless given) and an optional position (default: last on its disc), credits with
  `new_artist` parts (an artist created with the credit), `link` (the note link) on
  every entity read, `POST /musicbrainz/import` for an artist, release or recording
  (returns an `EntityRead`).
- `/search`: hits carry typed facts (release group type and date, recording length and
  release group, episode date) and counts (tracks of a release group; release groups
  and loose recordings of an artist); `per_type` caps each type (for results grouped
  by type), `tag_id` keeps tagged entities, and without `q` lists everything with the
  tag (by type, then name; `score` null), paged with `offset` and `X-Total-Count`
  (a ranked search with `q` is not paged); episodes that played a recording hit (or a
  track of a release group hit) are hits with `radio_match` (entry, recording, time).
- Radio entries have `state` (`in_library`, `excerpt_only`, `not_matched`); radio
  appearances have `playback`; `GET /entity-details` of a release group lists the radio
  entries that played its tracks.
- Imports: job summaries carry `title` and `subtitle` (what is imported and its credit
  or show), `cover_url`, `link_kind`, `watch_dir_id`, `input_host_path`, `first_issue`
  (id, kind, message; a blocking one first), `counts` (tracks;
  failures a retry tries again), `created` (rows the last apply created), `existing`
  (a release conflict's release group), `waiting_for` (the job ahead in the queue) and
  `progress` with the user-facing `step` (metadata, matching, downloading, applying)
  and `fraction` (a running download counted partly: `import_job.progress_item`).
  `GET /imports?attention=true` lists the jobs that wait for the user (`needs_review`,
  `failed`: `ep.importers.framework.ATTENTION`), `attention=false` the others.
  `GET /imports/summary` is the cheap poll (counts per state, running fraction, watched
  dirs, `changed_at`). `POST /imports/{id}/cancel` stops a queued or running job (the
  worker checks on every commit of the job's progress, killing a running download; an
  `applying` job finishes): it becomes `dismissed`. The worker changes a job's state only
  with a conditional update (`JobContext.set_state`), so it never overwrites a cancel. Failed downloads do not hold up a
  job when something else was downloaded: the rest is applied and the job stays in
  `needs_review` (a retry adds the others; their partial downloads are kept).
  `GET /imports/{id}/stream?track_key=` serves an incoming track's file, to preview it
  in review (once the file is there). An uncertain artist, release group or recording
  may be answered with any library row of that type (`pick_candidate`), not only a
  candidate. `GET /links/classify?url=` says what a link is (kind, source kind, import
  kind, canonical URL) without fetching it.
- `GET /artists/{id}/links` and `GET /release-groups/{id}/links` (`ServiceLinkRead`:
  `service`, `url`, `label`): links to other services, asked from MusicBrainz when
  wanted (`inc=url-rels`; a release group with its release's), never stored
  (`ep.service_links`): Wikipedia (from a `wikipedia` relationship, else the Wikidata
  item's sitelinks: English, else another language; `label` is the language), Bandcamp,
  Spotify (both mostly on the release) and MusicBrainz (from the MBID), in that order,
  one each, classified by URL host. Cached in the
  process for a day (MusicBrainz relationships per entity, Wikidata sitelinks per item;
  failures are not cached). Empty without MBID; 502 when MusicBrainz cannot be asked;
  without Wikipedia when only Wikidata cannot (`ep.metadata.wikidata`: one request a
  second, an app User-Agent without contact details).
- Watch dirs have `status` (`paused`, `importing`, `idle`) and `last_import_at` (jobs
  record the watch dir whose watcher queued them).

## Audio formats and AVPlayer

`/sources/{id}/stream` is a `FileResponse`: `Content-Type` is the file's container as
probed at import (`source.media_type`: `audio/mpeg`, `audio/mp4`, `audio/flac`,
`audio/ogg`, `audio/webm`, ...; the extension for a file not probed), the same as
`PlaybackRead.media_type`, which comes with `codec` (ffprobe's name: `mp3`, `aac`,
`flac`, `opus`, `vorbis`, ...: a container does not say its codec), `Content-Length`,
`Accept-Ranges: bytes`, and `206` with `Content-Range` for ranges, including AVPlayer's
first `bytes=0-1` probe (`tests/test_app_fit.py`); AVPlayer sends no `HEAD`.
AVFoundation plays MP3, AAC/ALAC in MP4/M4A, FLAC, WAV and AIFF. It does **not** play
Ogg (Opus or Vorbis) or WebM: that is what yt-dlp keeps from YouTube (`-x` keeps the
original codec), and SoundCloud/NTS downloads may be Opus too. EP does not transcode.

## Demo data

```sh
uv run python -m ep.seed --database-url postgresql+psycopg://ep:ep@localhost:5432/ep_demo \
    --library-dir /tmp/ep-demo/library      # the database must exist and be empty
```

`ep/seed.py` migrates, refuses a database with data and a non-empty directory or one
under `~/Music`, and writes a small fixed library (every play state, a multi-disc and
a single-file album, a radio show with two episodes, notes with links and a broken
one, tags, jobs in each state, among them an NTS review with a 34-entry tracklist and
three uncertain entries) with generated audio and covers. The audio covers every
decoder path: FLAC, MP3, AAC in M4A, Ogg Vorbis (Sigur Rós), WebM/Opus (the YouTube loose
track "Loose Tone") and Ogg Opus (the episode "Homage to the night shift"; the other
episode is M4A). It prints the
`EP_LIBRARY_DIR` and `EP_MEDIA_ROOTS` to run the server with. Run no worker against it
(the demo jobs are rows only). `tests/test_seed.py` seeds a scratch database.

A private seeded backend (own database container and ports, nothing under `~/Music`),
e.g. for the app's UI tests; from `server/`:

```sh
D=$(mktemp -d)
docker run -d --rm --name ep-harness-db -e POSTGRES_USER=ep -e POSTGRES_PASSWORD=ep \
    -e POSTGRES_DB=ep -p 127.0.0.1:55432:5432 postgres:18
until docker exec ep-harness-db pg_isready -q -h 127.0.0.1 -U ep -d ep; do sleep 1; done
uv run python -m ep.seed --database-url postgresql+psycopg://ep:ep@127.0.0.1:55432/ep \
    --library-dir "$D/library" --watch-dir "$D/watch"
EP_DATABASE_URL=postgresql+psycopg://ep:ep@127.0.0.1:55432/ep EP_LIBRARY_DIR="$D/library" \
    EP_MEDIA_ROOTS="[\"$D/library\", \"$D/watch\"]" \
    uv run uvicorn ep.main:app --host 127.0.0.1 --port 18000
docker rm -f ep-harness-db && rm -rf "$D"     # tear down
```

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
  (nested slices are clipped to their parent). `playback` on `SourceRead` (and in
  `TrackRead.playability`) is the result: stream `GET /sources/{id}/stream` (whole file, HTTP Range) and play
  `start_ms`..`end_ms`. Remote-only sources have `playback: null` and stream as a 409.
  For lists, `ep.playback.resolve_where` / `recording_playbacks` load the sources and
  all their ancestors in one recursive-CTE query and resolve them in memory.
- `ep.probe.probe(path)` reads container (as a media type), codec and duration
  (`ffprobe`, in the image) and tags plus the embedded cover (`mutagen`). It never
  writes. `POST /sources` uses it to fill in a new file source's facts, and
  `PATCH /sources/{id}` again when a file's `uri` changes to a file that is there.
- Covers are uploaded with `PUT /covers/release-group/{id}` (the image file as the
  body) and stored in
  `EP_LIBRARY_DIR/_covers/` (`ep.covers`), never next to files in watched directories.
  A stored image's file name has a hash of its content, and its URL (`cover_url`,
  `image_url`, `EntityRead.cover_url`) a version `?v=` derived from the path: a new
  image is a new URL. `GET /covers/...` answers `Cache-Control: immutable` (a year) for
  the current `v`, else `no-cache`, with the version as `ETag` (`If-None-Match` → 304).

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
  A new artist whose MusicBrainz name is not in Latin script ("跡部進一") is named by
  its primary English alias, else another primary Latin-script alias ("Shinichi
  Atobe"; `latin_alias`), and keeps the MusicBrainz name as `original_name` (shown as a
  secondary line; matched, filtered and searched like the name): one `inc=aliases`
  lookup for such an artist only (import jobs and `import_release(..., musicbrainz=)`;
  a failure keeps the MusicBrainz name), and its credits take the alias where they
  have the MusicBrainz name. A re-import renames a library artist that still has the
  MusicBrainz name in another script and no original name (imported before), like a
  missing cover filled in: `import_release`/`import_artist` for the release's artists
  found by MBID, the import framework for the proposal's (named while matching), and
  the folder importer for the albums imported from the folder before
  (`fill_latin_names_of`); one renamed in EP is left alone, and existing credits keep
  their credited names.
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
  simone sinnerman"). A one-word query of 4+ characters also finds a word it starts
  with letters dropped, one at most between two of its letters ("homgen" finds
  "Homogenic"): strict word similarity ≥ 0.25 (`<<%`, index-backed) and a regular
  expression for precision. `GET /resolve/{type}/{id}` finds an entity by internal ID or
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
  answers by id (and at once on the stored proposal's issues, `Issue.answer`, so a job
  queued again reads with what was answered) and re-runs the job from the start, each
  stage applying the stored answer to the issue it would raise. An answer that no longer fits (its candidate is not
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
  dismissed, and at worker start for such jobs (and deleted ones) if a worker stopped
  first (`discard_stale_work`). A medium whose origin is already the parent of a file source is not
  downloaded again.
- A new importer: subclass `Importer`, register it in `ep.jobs.tasks.IMPORTERS`, describe
  everything as `Proposal` items (single-file albums: tracks with `start_ms`/`end_ms` on
  one medium; MusicBrainz credits through `framework.mb_credit`). Rows outside the
  catalogue (radio) go in further `Proposal` fields and `Importer.apply`, which gets the
  recordings and sources the tracks became (`Applied`), in the apply transaction.
- Folder importer (`ep.importers.folder`): see its docstring. Files are indexed in place
  (`managed=false`, with `sha256`); covers go through `ep.covers`. An unreadable file
  is a non-blocking `unreadable_file` issue: the rest of the folder is imported.
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
  Not in the data, so not modelled: the channel an episode aired on (NTS 1/2 is only in
  the live schedule, not in show or episode responses) and a kind of tracklist entry
  (talk, jingle: every entry is an artist and a title).
- NTS import (input: an episode page URL): the audio is downloaded once with `yt-dlp -x`
  from the episode's SoundCloud upload (else the episode page) into
  `_radio/NTS/<Show>/<YYYY-MM-DD Episode>.<ext>` (6 h timeout); sources `nts` (page) →
  `soundcloud` → `file` → one `slice` per timed entry. Entries are optional loose tracks
  matched by MBID, ISRC/Deezer/ACRCloud/NTS uid, then fuzzy artist + title (the
  detected duration only as a lower bound): confident ones are used, clear misses become
  new standalone recordings and artists, the uncertain ones are non-blocking issues (the
  episode applies with them unmatched). A track played twice is one recording (radio
  entries do not "take" library rows from each other; new ones are shared within the job
  by MBID, ISRC/external ID or normalised text). A library recording matched by text
  keeps its metadata, but gains NTS's recording MBID if it has none and nothing else has
  it (framework-wide, as artists and release groups do; not when two tracks of the job
  with different MBIDs matched it). Images go to `_covers/radio-show/`, `_covers/radio-episode/`
  (`GET /covers/radio-show/{id}`, `/covers/radio-episode/{id}`). Re-importing updates
  the rows; entries in the library (found by their text) keep their recording and range
  (manual edits stand); slices and radio-only recordings no entry plays any more go.
  Not kept: an entry un-matched by hand (`recording_id: null`) is matched again.
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
  recording or `null`, change the range; the slice follows; the previous recording is
  deleted if it was radio-only and no entry plays it any more),
  `GET /recordings/{id}/radio-appearances`, `/artists/{id}/radio-appearances`.
  `DELETE` an episode removes its tracklist, slices, file source and link sources, and
  the recordings (and then uncredited artists) only it knew; the audio file only with
  `delete_audio=true`: a `managed` regular file whose real path (symlinks, `..`
  resolved) is in the library and in no watched directory or other media root. The
  rule for recordings radio created (`ep.radio.delete_unused_recordings`): a radio-only
  recording lives as long as a tracklist entry points at it; a recording in no
  tracklist is never hidden. `GET /radio/episodes/{id}` has `duration_ms` of the file
  (the scrubber's scale; the last entry may be open-ended).
- Cost of the radio-only filter (synthetic 70k artists, 560k recordings, 110k
  tracklist entries): `/artists` first page 3 ms (index on the browse order), but
  ~1 s at offset 40000 (each skipped artist is tested); `/search` adds up to ~1 s when
  a query hits tens of thousands of rows.

## Notes, links, tags

- Tables (`ep.models.notes`): `note` (one per entity: `entity_type` + `entity_id`
  unique, Markdown `body`), `note_link` (derived from the body, rebuilt on every save:
  target type, the UUID as written `target_ref`, the entity it resolved to on save
  `target_id` or null, `raw`, code point range), `tag` (`name` unique ignoring case:
  index on `lower(name)`), `entity_tag`. A track's note is its recording's.
- Links (`ep.notes.links`): `[[type:uuid|Display text]]`, display text optional, `\|`
  `\[` `\]` `\\` escaped in it (`format_link` writes them). Not links: fenced code
  blocks, inline code spans (CommonMark rules), `\[[...]]`, unknown types, malformed
  UUIDs; indented code blocks are not recognised (nested lists). The UUID is the MBID if
  the entity has one, else its ID; `/search` hits carry `link_ref` and the whole `link`.
- Resolution: on save into `target_id`, and again on every read by the UUID written
  (`GET /notes` re-resolves; backlinks match `target_id` or `target_ref` against the
  entity's ID, MBID and release MBID). So a link keeps working when its target gains an
  MBID, and starts working when a target with its UUID is created later; the text is
  never rewritten. Deleting a target nulls `target_id`: the link reads as broken
  (`target: null`), the text stays.
- `GET /notes/{type}/{id}` (MBID or ID; no note reads as an empty one, 404 only for a
  missing entity): body, `updated_at`, links with their range in **UTF-16 code units**
  (NSRange) and the target as `EntityRead` (title, subtitle, cover URL, MBID). `PUT`
  saves (blank deletes) with `base_updated_at`, the `updated_at` the edit started from
  (omitted or null: from an empty note): if the note changed since and the body
  differs, 409 `NoteConflictRead` with the current note (the row is locked; of two
  concurrent first saves one wins, the other is a 409: `tests/test_note_concurrency.py`).
- `ArtistRead`, `ReleaseGroupRead` and `RecordingRead` (and what extends them: every
  artist, album and track row) have `has_note`: a column property of the models (a
  correlated `EXISTS` on `note`, in the statement that loads the row: no query more).
- `GET /backlinks/{type}/{id}`: notes linking to exactly this entity, newest edit first,
  each with its entity and the line around each link (links shown as display text or
  title, long lines cut, highlight range).
- `GET /notes/search?q=`: Postgres full-text search, `websearch_to_tsquery` on an
  expression GIN index over `to_tsvector('ep_notes', ep_note_text(body))` (migration
  0010): `ep_note_text` shows links as their display text (links in code left as
  written: one-line single-backtick spans and fences without the fence character inside,
  an approximation of the parser's CommonMark rules), `ep_notes` is `simple` (no
  stemming) with `unaccent` (which migration 0004 had dropped for matching; 0010 brings
  it back for this dictionary only); snippets from `ts_headline` with highlight ranges.
  The query goes to `websearch_to_tsquery` as a bound parameter: any text is a valid
  query.
- `GET /entity-details/{type}/{id}` is the detail panel in one request (`ep.api.details`):
  the entity (`EntityRead`), its note, tags, backlinks and (recordings, artists) radio
  appearances, the same reads as the single endpoints, which stay for refreshing one
  part after an edit (`PUT /notes` answers with the note, tag changes with the tags).
- Tags (`ep.notes.tags`, `ep.api.tags`): `GET /tags` (counts, `prefix`, `sort=name`
  or `count`: most used first), `GET/PATCH/DELETE /tags/{id}` (renaming onto an
  existing name merges), `GET/POST /entity-tags/{type}/{id}` (add by name: trimmed,
  whitespace collapsed, find-or-create ignoring case),
  `DELETE /entity-tags/{type}/{id}/{tag_id}`; adding and removing both answer with the
  entity's tags. Everything with a tag: `GET /search?tag_id=` without `q`. Hashtags in
  note bodies are not parsed.
- Imported tags: Bandcamp tags and MusicBrainz genres (release group and release,
  `inc=genres`) on a release group, NTS genres and moods on an episode, **only when the
  import creates the entity**: re-importing never brings back a removed tag. YouTube
  video tags are not used (keyword noise).
- Never deleted as a side effect: a recording radio created that has a note, a tag or
  an inbound link outlives its tracklist entries (`delete_unused_recordings`), and so
  does an annotated artist left uncredited. No code path merges two entities (attaching
  an incoming track to a library recording moves nothing), so no note is ever merged.

## Tests

Testing policy: each feature gets its happy path and its most important failure cases,
not every edge case. Always kept: guards against false auto-matches, the
never-modify and path-confinement checks, notes surviving deletes, query counts, the
OpenAPI staleness test and the Python/SQL normaliser agreement. Prefer extending an
existing test over a new one that builds the same data again.

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
