# EP — design handover for the macOS app (draft 1)

## What EP is

EP is a personal music library manager for one user, with Obsidian-style notes built in.
Every artist, album and track can carry a Markdown note, and notes link to other artists,
albums, tracks and radio shows. The links are the point: the library becomes a personal
wiki of music you can listen to while you read and write.

The app is a native macOS app (SwiftUI). It talks to a backend on the same machine or
LAN. Several windows or Macs may use the same backend; there is no live sync, so views
refresh on navigation and after the user's own actions.

Design for one experienced user with a large library (thousands of artists), who lives
on the keyboard and wants density over decoration.

## What we need from design

1. The main window: layout, proportions, resizing behaviour, light and dark.
2. The note editor, including link autocomplete and link navigation.
3. The player bar, including playback inside a long file (radio show, single-file album).
4. The import wizard, including the review step for uncertain matches.
5. The radio view.
6. Search, editing metadata, and settings.
7. Empty, loading, error and "not playable" states for all of the above.

## Vocabulary

| Term | Meaning |
|---|---|
| Artist | A person or group. |
| Release group | An album, EP or single. Shown to the user as "album" or by its type. One edition only; no separate editions. |
| Track | A position on a release group. |
| Recording | The piece of music a track plays. The same recording can appear on an album and in several radio shows. Notes and tags on a track belong to the recording, so they follow it everywhere. |
| Source | Where audio comes from: a file, or a link (YouTube, Bandcamp, SoundCloud, Spotify, torrent). A source can be a time range inside another source. |
| Radio show / episode | A show (e.g. on NTS) and its episodes. An episode is one long audio file with a tracklist; each entry points at a time range and, where matched, at a recording in the library. |
| Loose track | A recording that is on no release group (e.g. one YouTube video). |

## Main window

Top to bottom:

```
┌──────────────────────────────────────────────────────────────┐
│ ‹ back   ＋ import        [ Library | Radio ]        search  │  toolbar
├───────────────┬──────────────────┬───────────────────────────┤
│ Artists       │ Release groups   │ Tracks                    │  three columns
│               │                  │                           │
├───────────────┴──────────────────┴───────────────────────────┤
│ [ Note | Tags | Linked from ]                                │  detail panel
│                                                              │  (tabbed)
├──────────────────────────────────────────────────────────────┤
│ ▶ player                                                     │  player bar
└──────────────────────────────────────────────────────────────┘
```

### Toolbar

- **Back** (top left), and forward. History covers every navigation: clicks in the
  columns, link clicks, search results. Standard shortcuts (⌘[ and ⌘]).
- **＋** (top left) opens the import wizard. It should also show that imports are
  running or waiting for review (a badge or progress ring).
- **Library | Radio** switch.
- **Search** field (also ⌘K or ⌘F).

### Three columns

Selection flows left to right: artist → release groups → tracks.

- **Artists**: name, sorted by sort name. Thousands of rows; needs a quick filter and
  type-to-select. Artists known only from radio tracklists are hidden by default, with a
  toggle to show them.
- **Release groups** of the selected artist: cover, title, year, type (album, EP,
  single). Sorted by date. A pseudo-entry for the artist's loose tracks.
- **Tracks** of the selected release group: number, title, artist credit when it differs
  from the album's, length, and a playability indicator (see "Track states"). Disc
  headings for multi-disc releases. Double-click or Return plays.

Column widths are adjustable. The divider between the columns and the detail panel is
draggable, and the panel can be collapsed.

### Track states

A track row must show which of these it is:

- Playable from its own file.
- Playable as a range inside a single-file album.
- Playable only as an excerpt inside a radio episode (it may be talked over or mixed).
- Has only a link (e.g. Spotify, YouTube not downloaded): not playable in the app, but
  the link can be opened.
- No source at all (metadata only).

A recording can have several sources. There should be a way to see them (kind, path or
link, codec, where it was downloaded from) and to choose which one plays.

### Detail panel

It shows the selected thing: the artist, release group or track most recently selected
in the columns (or the radio show or episode in the radio view). The panel header says
what it is showing.

**Note tab** — see "Note editor".

**Tags tab** — the tags on this entity. Add (with autocomplete from existing tags),
remove. Clicking a tag should lead somewhere useful (a list of everything with that tag).

**Linked from tab** — two lists:
- Notes that link to this entity, each shown as the entity the note belongs to plus the
  line of text around the link. Clicking navigates there.
- Radio episodes this appears in (for a recording: episodes that played it; for an
  artist: episodes that played anything by them), with show, date and time position.
  Clicking navigates to the episode; a play affordance starts the episode at that point.

### Player bar

- Transport (previous, play/pause, next), scrubber with elapsed and remaining time,
  volume, cover, title and artist. Clicking the title navigates to the track.
- A queue: playing a track queues the rest of the album after it.
- **Long files.** For a radio episode or a single-file album the scrubber covers the
  whole file and shows the track boundaries as marks. The title area shows the current
  tracklist entry and changes as playback crosses a boundary. Some radio timestamps are
  estimates; show those marks differently. Previous/next jump between entries.
- A way to jump the columns and detail panel to what is playing, so the note for the
  current track is one keystroke away while listening.

## Note editor

One note per artist, release group, recording, radio show and radio episode. Plain
Markdown stored as text. The editor behaves like Obsidian's live preview: Markdown
syntax is styled in place (headings, bold, italic, lists, quotes, code, links) and the
raw syntax of a construct is revealed when the cursor is in it. There is no separate
preview mode. Notes save automatically.

### Links

- Stored form: `[[artist:<uuid>|Björk]]`. The type is `artist`, `release_group`,
  `recording`, `radio_show` or `radio_episode`. The user never types or sees the UUID.
- Displayed form: the display text as a link, optionally with a small type icon or
  cover thumbnail.
- **Autocomplete.** Typing `[[` opens a popup that searches the whole library as the
  user types. Each row shows cover art (or a placeholder per type), the name, a second
  line (artist credit, year, type), and the entity type. Arrow keys and Return select;
  Escape closes. Results are fuzzy and tolerate typos and missing accents.
  Open question for design: what the popup offers when nothing matches (create a new
  artist, or search MusicBrainz).
- **Following links.** Holding ⌘ turns links into click targets (cursor and hover
  style change); ⌘-click navigates the whole app to that entity: columns select it, the
  detail panel shows it, and the back button returns.
- A link whose target no longer exists is shown as broken, not hidden.

## Radio view

The same three-column frame, with different columns: shows → episodes → tracklist.

- **Shows**: name, station, image.
- **Episodes** of the selected show: date, title, image, location.
- **Tracklist** of the selected episode: time position, artist, title. Entries matched
  to a library recording link to it (and show whether the library has it as a proper
  file, not only as this excerpt). Unmatched entries are plain text. Estimated
  timestamps are marked. Double-click plays the episode from that entry.

The detail panel works the same way for shows and episodes (note, tags, linked from).

## Import wizard

Opened with ＋. Imports run in the background on the server; the wizard can be closed
and reopened without losing them.

1. **Start.** One field that accepts a pasted link (Bandcamp album, YouTube video or
   playlist, NTS episode) and recognises which it is; or choose a folder on disk.
2. **Progress.** Stages: fetching metadata, matching, downloading, done. A log for
   details. Failures show the reason and offer retry.
3. **Review**, only when the server is unsure. The server proposes how the incoming
   release maps onto the library and asks about uncertain items. Typical questions:
   - Is this artist the same as an existing one? (candidates with scores)
   - Is this incoming track the same recording as one already in the library?
     (side by side: title, artist, length, where the existing one came from)
   - Which MusicBrainz release is this? (candidates, or "none, keep as entered")
   - This album already exists in a different edition (an error for now, shown clearly).
   The user confirms, picks another candidate, or chooses "new". Radio tracklists can
   have 30 or more entries, so review must be fast: sensible defaults preselected,
   keyboard driven, only the uncertain rows needing attention.
4. **Import queue.** All imports, including those started automatically when a new
   folder appears in a watched directory: state, source, time. The queue doubles as a
   decision inbox: an import with any problem (uncertain match, edition conflict,
   failed download, unreadable file, nothing found) stays there as a decision for the
   user until they resolve, retry or dismiss it. Nothing is skipped silently.

## Search

A global search over artists, release groups, recordings, radio shows and episodes,
with the same result rows as link autocomplete (cover, name, second line, type).
Return navigates; a modifier plays.

## Editing

All metadata is editable in the app and never written to the audio files: names,
titles, artist credits (several artists with joining text such as " feat. "), dates,
types, track order, covers (drop an image), tags. Entities can be created by hand and
may have no MusicBrainz ID. Design a lightweight way to edit in place or in a sheet.

## Settings

Server address, watched directories (add, remove, enable), library location (read-only
display).

## States to cover

- Empty library (first launch): lead the user to import or add a watched directory.
- Server unreachable.
- Loading for lists and for the note.
- Artist with no release groups; release group with no playable tracks.
- Track not playable (link only, or file missing on disk).
- Import failed; import waiting for review.
- Broken link in a note.

## Platform expectations

- Standard macOS window behaviour, toolbar, menus and shortcuts; light and dark
  appearance; follows the system accent colour unless design proposes otherwise.
- Full keyboard operation: move between columns with arrow keys, Space toggles
  playback (except while typing in the note), media keys work.
- Comfortable from a 13-inch laptop to a large display.
- VoiceOver labels and Dynamic Type-friendly text sizing where macOS supports it.

## Out of scope for now

iOS, multiple users or accounts, live sync between windows, lyrics, playlists,
streaming-service playback, writing tags to files, classical-music metadata.

## Open questions

1. Visual direction: close to stock macOS (Music, Finder, Notes), or a distinct look?
2. Should the detail panel follow the selection (as written above), follow what is
   playing, or be pinnable to either?
3. How prominent is cover art: small thumbnails in dense lists, or a cover grid option
   for release groups?
4. Is a queue enough, or are playlists wanted soon?
5. Standalone notes that belong to no entity (e.g. a listening diary)?
