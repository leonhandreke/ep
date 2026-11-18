---
name: import-release
description: Import a release (album, EP, single) into the EP library from a link or text, and fix up its artists, release group, tracks and recordings through the running server's HTTP API — e.g. an import that came in without tracks, a tracklist only given in a description, or a loose track (YouTube etc.) that belongs on an album.
---

# Import and fix up a release via the EP API

The server runs at `http://localhost:8000` (check `GET /health`). All bodies are JSON
(`-H 'Content-Type: application/json'`). The full contract is `server/openapi.json`;
the schemas live in `server/ep/api/schemas.py`. These calls change the user's library:
say exactly what you will create/change and get a yes before writing.

## Model in one paragraph

An **artist credit** (`artist_credit`: a list of `{artist_id | new_artist, credited_name?,
join_phrase}`) is attached to release groups and recordings. A **release group** (the
album; `type`: `album`, `ep`, `single`, `compilation`, `other`; `date` `YYYY[-MM[-DD]]`)
has **tracks** (`disc`, `position`, optional `title` override), each pointing at a
**recording**. A recording plays from **sources** (`file`, `slice` of a file,
`youtube`, `bandcamp`, `soundcloud`, …). A recording that is no track of any release
group is a **loose recording**. Recordings may exist without any audio (a tracklist
entry waiting for a file).

## 1. Look first: what is already there

```sh
curl -s "localhost:8000/search?q=<artist or 'artist title'>"            # all types; &types=artist&types=release_group&types=recording
curl -s "localhost:8000/imports?limit=10"                               # recent jobs (title, state)
curl -s localhost:8000/artists/$ARTIST                                  # counts: release_groups, loose_recordings, ...
curl -s localhost:8000/artists/$ARTIST/release-groups
curl -s "localhost:8000/artists/$ARTIST/recordings?standalone=true"     # loose recordings only (false: only tracks)
curl -s localhost:8000/artists/$ARTIST/appearances                      # albums they are on without the album credit
curl -s localhost:8000/release-groups/$RG                               # with track count, discs, length
curl -s localhost:8000/release-groups/$RG/tracks                        # disc/position order, recording + playability
curl -s localhost:8000/recordings/$REC                                  # its tracks (release groups) + playability
curl -s localhost:8000/recordings/$REC/sources
curl -s localhost:8000/resolve/<type>/<mbid-or-id>                      # find an entity by MBID
```

Search is fuzzy (typos, prefixes, artist+title together). Don't create duplicates:
reuse an existing artist id in credits and an existing recording id in tracks.

## 2. Import from a link (preferred when there is one)

```sh
curl -s "localhost:8000/links/classify?url=<url>"                       # what kind of link, which import kind
curl -s -X POST localhost:8000/imports -d '{"kind":"<kind>","input":"<url or folder path>"}'
curl -s localhost:8000/imports/$JOB                                     # state, log, proposal, issues
```

Kinds: `folder` (a directory under the media roots, in place), `bandcamp` (album or
track page), `youtube` (video: a loose recording, or a single-file album if it has
chapters; playlist: one release group), `nts` (radio episode). Downloads land in the
managed library. States: `queued`, `running`, `needs_review`, `applying`, `done`,
`failed`, `dismissed`.

A job in `needs_review` has issues in its proposal; answer them by id:

```sh
curl -s -X POST localhost:8000/imports/$JOB/review \
  -d '{"answers":[{"issue_id":"<id>","choice":"<choice>","candidate_id":null}]}'
```

`POST /imports/$JOB/retry` re-runs a `needs_review`/`failed`/`done` job (re-importing an
already-imported source completes it: missing MusicBrainz match, cover, new tracks);
`/dismiss` and `/cancel` take a job away. Imports resolve against MusicBrainz
automatically; not being in MusicBrainz is fine.

### From MusicBrainz directly

```sh
curl -s "localhost:8000/musicbrainz/search?type=release&q=<artist title>"   # type: artist | release | recording
curl -s -X POST localhost:8000/musicbrainz/import -d '{"type":"release","mbid":"<mbid>"}'
```

Metadata only (no audio, no cover); a release becomes a release group with its tracks.

## 3. Build or fix by hand

When the import has no (or wrong) tracks — e.g. a pre-order page whose tracklist only
appears in the description text — edit the catalogue directly.

**Artists**

```sh
curl -s -X POST localhost:8000/artists -d '{"name":"X","sort_name":"X"}'
curl -s -X PATCH localhost:8000/artists/$ARTIST -d '{"name":"X"}'          # name, sort_name, disambiguation, type, mbid
curl -s -X DELETE localhost:8000/artists/$ARTIST                          # only if credited on nothing
```

**Release groups**

```sh
curl -s -X POST localhost:8000/release-groups \
  -d '{"title":"T","artist_credit":[{"artist_id":"'$ARTIST'"}],"type":"ep","date":"2026-10-10"}'
curl -s -X PATCH localhost:8000/release-groups/$RG -d '{"type":"ep"}'      # title, artist_credit, type, date, barcode, mbid, release_mbid
curl -s -X DELETE localhost:8000/release-groups/$RG                       # with its tracks and cover; recordings stay
curl -s -X PUT localhost:8000/covers/release-group/$RG --data-binary @cover.jpg -H 'Content-Type: image/jpeg'
```

**Tracks** (exactly one of `recording_id` / `new_recording`; `position` defaults to
after the last track of `disc`, `disc` defaults to 1):

```sh
# a new recording without audio, credited like the release group
curl -s -X POST localhost:8000/tracks \
  -d '{"release_group_id":"'$RG'","position":1,"new_recording":{"title":"First","length_ms":null}}'
# an existing (e.g. loose) recording becomes a track: it is then no longer loose
curl -s -X POST localhost:8000/tracks \
  -d '{"release_group_id":"'$RG'","position":4,"recording_id":"'$REC'"}'
curl -s -X PATCH localhost:8000/tracks/$TRACK -d '{"title":null}'         # recording_id, disc, position, title (null: use recording's)
curl -s -X DELETE localhost:8000/tracks/$TRACK
# reorder: every track of the release group with its new place
curl -s -X PUT localhost:8000/release-groups/$RG/track-order \
  -d '{"tracks":[{"id":"<track>","disc":1,"position":1}, ...]}'
```

Positions are integers: vinyl sides (A1, A2, B1, …) become 1, 2, 3, …

**Recordings**

```sh
curl -s -X POST localhost:8000/recordings -d '{"title":"T","artist_credit":[{"artist_id":"'$ARTIST'"}]}'
curl -s -X PATCH localhost:8000/recordings/$REC \
  -d '{"artist_credit":[{"artist_id":"'$ARTIST'"}]}'                     # title, artist_credit, length_ms, mbid
curl -s -X DELETE localhost:8000/recordings/$REC                          # only if no track and no sources
curl -s -X POST localhost:8000/recordings/$REC/sources -d '{"kind":"youtube","uri":"https://www.youtube.com/watch?v=..."}'
curl -s -X PUT localhost:8000/recordings/$REC/preferred-source -d '{"source_id":"<source>"}'   # null: default choice
```

A loose import's credit often differs in spelling from the album's (e.g. a channel's
lowercase `aedis` vs `Aedis`); patching the credit to just `artist_id` uses the artist's
name.

**External IDs, tags, notes** (`type`: `artist`, `release_group`, `recording`)

```sh
curl -s -X POST localhost:8000/release-groups/$RG/external-ids -d '{"scheme":"<scheme>","value":"<value>"}'
curl -s -X POST localhost:8000/entity-tags/release_group/$RG -d '{"name":"techno"}'
```

## 4. Verify

```sh
curl -s localhost:8000/release-groups/$RG/tracks | python3 -c "import json,sys;[print(t['disc'],t['position'],t.get('title') or t['recording']['title'],t['recording']['artist_credit']['name']) for t in json.load(sys.stdin)]"
curl -s localhost:8000/recordings/$REC | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['playability']['state'],[t['release_group']['title'] for t in d['tracks']])"
curl -s localhost:8000/artists/$ARTIST | python3 -c "import json,sys;print(json.load(sys.stdin)['counts'])"
```

Report what was created/changed, with titles and positions, and anything left without
audio.
