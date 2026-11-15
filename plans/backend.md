# EP — backend plan (draft 2)

EP is a music library manager with Obsidian-style notes and links. This plan covers the
data model, tech stack and backend stages. The macOS app (Swift) lives alongside in its
own directory and is planned separately.

## Principles

- Everything backend lives in `server/`.
- Never modify audio files. All edits live in the database. Watched directories are
  mounted read-only.
- Keep the schema plain: no cache tables, no denormalised copies. Err on the side of the
  ORM. Indexes and custom SQL are allowed where they make sense (a query that is
  clearly better expressed or much faster in SQL, an index for an obvious lookup
  path); do not add them speculatively.
- Entities may exist without a MusicBrainz ID. `mbid` is always nullable.
- One release per release group. Importing a second, different release for an existing
  release group is an error.
- No realtime push, no auth (localhost/LAN) for now.
- Use the latest stable versions and current conventions of every tool and library
  (check the docs rather than relying on memory). No backward-compatibility shims,
  no support for older versions, no deprecated APIs.

## Tech stack

- Python 3.15, uv, FastAPI, SQLModel, Alembic, Postgres 18 (`pg_trgm` for fuzzy
  matching and search, on generated normalised columns with GIN trigram indexes; see
  stage 4b).
- Procrastinate (Postgres-backed job queue) for import jobs; no Redis.
- `httpx` (MusicBrainz at 1 req/s with a descriptive User-Agent, NTS, Bandcamp pages),
  `mutagen` + `ffprobe` (tags, duration), `rapidfuzz` (match scoring),
  `watchfiles` in polling mode (Docker bind mounts on macOS drop fs events),
  `yt-dlp -x` as a subprocess, pytest.
- `server/docker-compose.yml`: `db`, `api`, `worker`.
  Mounts: `~/Music/ep-Library` read-write, each watched directory read-only.
- REST + OpenAPI (the Swift client is generated from it).
  Audio via `GET /sources/{id}/stream` with HTTP Range support.

```
server/
  docker-compose.yml  Dockerfile  pyproject.toml  alembic/
  ep/
    models/      # SQLModel tables
    api/         # FastAPI routers
    matching/    # normalisation, candidate search, scoring
    metadata/    # musicbrainz.py, bandcamp.py, nts.py, ytdlp.py
    importers/   # one module per import kind
    jobs/        # Procrastinate tasks, watcher
  tests/
plans/
```

## Data model

All tables have a `uuid4` primary key `id` plus `created_at` / `updated_at`.

### Catalogue (simplified MusicBrainz)

| Table | Columns |
|---|---|
| `artist` | `mbid?`, `name`, `sort_name`, `disambiguation`, `type?` (person, group, other) |
| `artist_credit` | `name` (rendered credit string) |
| `artist_credit_name` | `artist_credit_id`, `artist_id`, `position`, `credited_name`, `join_phrase` |
| `release_group` | `mbid?`, `release_mbid?`, `title`, `artist_credit_id`, `type` (album, ep, single, compilation, other), `date?` (partial dates as text `YYYY[-MM[-DD]]`), `cover_path?`, `barcode?` |
| `recording` | `mbid?`, `title`, `artist_credit_id`, `length_ms?` |
| `track` | `release_group_id`, `recording_id`, `disc`, `position`, `title?` (override) |
| `external_id` | `entity_type`, `entity_id`, `scheme` (isrc, deezer, spotify, bandcamp, youtube, nts, acr, …), `value` |

Omitted from MusicBrainz: releases and media as separate entities, works, labels, areas,
series, relationships, aliases.

A recording with no `track` row is a standalone recording (a loose YouTube track, or a
recording only known from a radio tracklist).

### Sources

One table. A source is anything that locates audio; a source may be derived from another.

| Column | Meaning |
|---|---|
| `kind` | `file`, `slice`, `youtube`, `bandcamp`, `soundcloud`, `nts`, `spotify`, `torrent`, … |
| `uri` | absolute path for `file`; URL / URI for remote kinds; null for `slice` |
| `parent_id?` | the source this one was obtained from |
| `start_ms?`, `end_ms?` | time range within the parent (only for `slice`) |
| `recording_id?` | the recording this source plays; null for containers (whole-album file, episode file) |
| `managed` | true if EP downloaded it into the library; false for watched/in-place files |
| `codec?`, `duration_ms?`, `size?`, `sha256?`, `meta` (JSON) | file facts and extractor leftovers |

Examples:

- Track downloaded from YouTube: `youtube` source → `file` source (parent = the link,
  `recording_id` set).
- Single-file album: `file` source (no recording) → one `slice` per track
  (parent = the file, start/end, `recording_id` set). YouTube full-album videos get
  slices from yt-dlp chapters.
- Radio episode: `nts` source → `soundcloud` source → `file` source → one `slice` per
  tracklist entry.

Playback resolves a source by walking `parent_id` up to the nearest `file`, summing
offsets. Remote-only sources (a Spotify link with no file) are stored and shown but not
playable yet.

### Radio (separate from the catalogue)

| Table | Columns |
|---|---|
| `radio_show` | `station` (nts), `slug`, `name`, `description`, `image_path?` |
| `radio_episode` | `show_id`, `slug`, `name`, `description`, `broadcast_at`, `location?`, `image_path?`, `source_id?` (the episode audio file), `url` |
| `radio_episode_track` | `episode_id`, `position`, `artist_text`, `title_text` (as given by the station), `recording_id?`, `source_id?` (the slice), `offset_is_estimate` |

Each tracklist entry is matched against the library; if nothing matches, a standalone
recording (and artist, if needed) is created. Either way the recording gets a `slice`
source pointing into the episode file, so it is playable like a track of a single-file
album.

**Hiding radio-only material by default:** browse endpoints take
`include_radio_only=false`. A recording is radio-only when it has no `track` row and
every one of its sources is referenced by a `radio_episode_track`. An artist is
radio-only when it has no release groups and all its recordings are radio-only. This is
computed in the query, not stored.

### Notes, links, tags

| Table | Columns |
|---|---|
| `note` | `entity_type`, `entity_id` (unique together), `body` (Markdown) |
| `note_link` | `note_id`, `target_type`, `target_ref` (the UUID written), `target_id?` (null if unresolved), `raw`, `start`/`end` |
| `tag` | `name` (unique) |
| `entity_tag` | `tag_id`, `entity_type`, `entity_id` |

- `entity_type` is one of `artist`, `release_group`, `recording`, `radio_show`,
  `radio_episode`. Notes on a track attach to its recording.
- Link syntax: `[[artist:<uuid>|Display text]]`. The UUID is the MBID when the entity has
  one, otherwise the internal ID; the resolver checks both columns.
- `note_link` rows are re-parsed from the body on every save. Backlinks are a query on
  `note_link`; "appears in radio shows" is a query on `radio_episode_track`.
- NTS genres and MusicBrainz genres are imported as tags.

### Operations

| Table | Columns |
|---|---|
| `import_job` | `kind` (bandcamp, youtube, nts, folder), `input`, `state` (queued, running, needs_review, applying, done, failed), `proposal` (JSON), `log`, `error?` |
| `watch_dir` | `path`, `enabled` |

## Matching

One module, used by every importer, by NTS tracklist dedupe and by search/autocomplete.

1. Exact: MBID, then ISRC or any other `external_id`.
2. Fuzzy: normalise artist and title (lowercase, unaccent, strip "feat. …",
   "(Remastered 2011)", punctuation), fetch candidates with trigram word similarity
   (index-backed, in Postgres), re-score with rapidfuzz, and require the duration to be
   within a tolerance when both are known.
3. Decision by score: auto-match, ask the user (goes into the job proposal), or create new.

Example: a loose YouTube track exists; the Bandcamp album containing it is imported. The
recording matches, gains a `track` row on the new release group and a second source.

## Metadata providers (Python ports, no Harmony service)

- `musicbrainz.py`: lookups by MBID, URL (`/ws/2/url?resource=`), barcode and ISRC, plus
  search. This covers Harmony's "resolve to MBIDs" step.
- `bandcamp.py`: port of Harmony's Bandcamp provider. Parses the embedded `tralbum` and
  JSON-LD from the album page into title, artist credits, tracks, lengths, date, barcode,
  cover URL.
- `ytdlp.py`: `yt-dlp --dump-json` for title, artist, album, track, chapters, thumbnails.
- `nts.py`: `GET /api/v2/shows/{show}/episodes/{episode}` (public, includes the tracklist
  with offsets, ISRCs, Deezer and MusicBrainz IDs). Use exact `offset`/`duration`, fall
  back to `offset_estimate`/`duration_estimate`. Undocumented API: fail loudly on shape
  changes.

## Import pipeline

All importers share the stages fetch → resolve metadata → match → proposal → apply.
A job stops at `needs_review` when any match is uncertain; the wizard shows the proposal
and posts the user's decisions back. Confident jobs apply directly.

The import queue is also the user's decision inbox. A job with any problem (uncertain
match, release-group edition conflict, download failure, unreadable file, metadata not
found) is never dropped or silently skipped: it stays in the queue in `needs_review` or
`failed` with a structured list of issues, each with a kind, a human-readable message
and the choices available (pick a candidate, create new, retry, skip this item, dismiss
the job). Jobs leave the queue only by being completed or explicitly dismissed.

- **Bandcamp**: `bandcamp.py` + MusicBrainz URL lookup, then `yt-dlp -x` into
  `~/Music/ep-Library/Artist/Release Group/NN Title.ext`.
- **YouTube**: `ytdlp.py` metadata, MusicBrainz search, then `yt-dlp -x`. A video with
  chapters becomes a single-file album.
- **NTS**: `nts.py`, `yt-dlp -x` of the episode into
  `~/Music/ep-Library/_radio/NTS/Show/Episode.ext`, then tracklist matching.
- **Folder**: read tags, MusicBrainz search, index files in place (`managed=false`).
- **Watcher**: a new folder in a `watch_dir` that has been quiet for a settle period
  creates a folder job. A periodic rescan catches anything missed.

## API surface (sketch)

- Browse: `/artists`, `/artists/{id}/release-groups`, `/release-groups/{id}/tracks`,
  `/recordings/{id}`, `/radio/shows`, `/radio/shows/{id}/episodes`, `/radio/episodes/{id}`.
- Edit: `PATCH` on each entity; `POST` to create entities by hand.
- Search: `/search?q=&types=` (fuzzy, returns cover URLs; powers `[[` autocomplete).
- Resolve: `/resolve/{type}/{uuid}` (MBID or internal ID → entity; powers link clicks).
- Notes and tags: `/notes/{type}/{id}`, `/backlinks/{type}/{id}`, `/tags`, entity tag
  add/remove.
- Media: `/sources/{id}/stream`, `/covers/{type}/{id}`.
- Imports: `POST /imports`, `GET /imports`, `GET /imports/{id}`,
  `POST /imports/{id}/review`; `/watch-dirs` CRUD.

## Stages

1. **Scaffold**: `server/` layout, compose, uv project, FastAPI app, Alembic, pytest
   against a real Postgres.
2. **Catalogue**: catalogue tables, browse and edit endpoints, manual entity creation.
3. **Sources**: source table, tag/duration reading, stream endpoint with slice
   resolution, cover art.
4. **Matching and MusicBrainz**: `musicbrainz.py`, matching module, `/search`,
   `/resolve`.
   **4b. Postgres-native matching**: investigate how the matcher and `/search` can make
   the best use of Postgres and its bundled extensions (`pg_trgm` operators and
   GIN/GiST indexes, `unaccent`, `fuzzystrmatch`, full-text search, ICU collations,
   generated columns), and change the implementation where that is a real improvement.
   This is the one place where indexes and Postgres-specific SQL are welcome.
   Outcome: an IMMUTABLE SQL function `ep_normalize`, the twin of Python's
   `normalize` (Postgres 18 `casefold` with the builtin `pg_unicode_fast` collation and
   `normalize()`, replacing `unaccent`, whose rules Python cannot reproduce), stored
   generated `*_normalized` columns with GIN trigram indexes, queries (also normalised
   by `ep_normalize`, so that retrieval does not depend on the twins agreeing on rare
   combining marks) on the `<%` and `LIKE` operators with per-transaction thresholds, "artist title" search as one
   index-backed select per split of the query, and btree indexes for external IDs,
   barcodes, artist credits and tracks by recording. Rejected: GiST/KNN (the rank is
   not a single trigram distance), full-text search (no typo tolerance, cannot span the
   recording-credit join), `fuzzystrmatch` (rapidfuzz scores better; phonetic codes are
   English-centric and collide), ICU nondeterministic collations and `citext` (no
   punctuation/feat. handling, no trigram support), `btree_gin` (nothing to combine).
   **4c. Query clean-up**: go through the existing codebase with the relaxed rule
   above: add indexes for obvious lookup paths, replace awkward multi-query code with
   a join or custom SQL where that is clearly better, and leave ORM code alone where
   it is fine.
   Outcome: list endpoints eager-load what they render (artist credits with their
   names, a track's recording, a recording's tracks in release-group order), with
   statement counts asserted in tests; playback for lists loads sources and all their
   ancestors in one recursive CTE (cycle-safe through `UNION`) instead of a lazy load
   per parent; "is this credit still used" is one `EXISTS` statement; btree indexes on
   `source.recording_id`, `source.parent_id`, `artist_credit_name.artist_id` and
   `artist_credit.name`. Left as they are: single-row parent walks (stream, source
   validation), delete checks whose loaded collection the delete reuses, the
   per-entity lookups of the MusicBrainz import.
5. **Jobs and folder import**: Procrastinate, `import_job`, proposal/review flow, folder
   importer, watcher.
6. **Bandcamp and YouTube**: `bandcamp.py`, `ytdlp.py`, downloads, chapter slices.
7. **Radio**: radio tables, `nts.py`, NTS importer, radio-only filtering.
8. **Notes and tags**: notes, link parsing, backlinks, tags.
   Outcome: links resolved on save and again on read by the UUID written (an MBID
   gained later, a target created later); optimistic concurrency on `updated_at`;
   full-text search over notes (`ep_notes`: `simple` + `unaccent`); imported tags only
   on entities the import creates; annotated entities are never deleted as a side
   effect.

Each stage ends with passing tests and a migration. Stages 6, 7 and 8 are independent of
each other once 5 is done.

## Test fixtures and local setup

- Bandcamp album: https://knotwilg.bandcamp.com/album/leitmotiv-2
  The artist is Maxine Funke; Knotwilg is the label whose Bandcamp page hosts it.
  The importer must take the artist from the album's artist field, never from the
  Bandcamp subdomain / page owner. This must be a test case.
- YouTube single video: https://www.youtube.com/watch?v=GSWtPDAN3PE
- YouTube playlist (import as one release group, one file per entry):
  https://www.youtube.com/watch?v=-i8dwSGgZjE&list=PLpCe82icWpaIvy78b4vyPWy3h8WThV5pW
- NTS episode:
  https://www.nts.live/shows/early-bird-show-w-pam/episodes/early-bird-show-w-pam-12th-november-2026
- Watched directory: `~/Music/Library`. Mounted read-only; never written to, renamed,
  or deleted from, by any code or test.
- Managed library: `~/Music/ep-Library`.
- Tests that hit the network or download audio are marked `network` and are opt-in.
  Unit tests use saved response fixtures under `server/tests/fixtures/`.

## Open points

- Multi-disc releases: `track.disc` is kept, but the folder layout ignores discs.
- AcoustID fingerprint matching is left out; it would slot into the matching module.
- Standalone notes (not attached to an entity) are left out.
