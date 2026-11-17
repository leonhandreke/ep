# EP — macOS app design, distilled from the Claude Design artifact

Source: https://claude.ai/artifact/C6tVMAHQfLeTh7ZF86c1Nf (artifact type "Design", version
1794694954-2500, read 2026-11-15). Brief it was made from: `plans/frontend-design-handover.md`
("the brief" below). This file is for SwiftUI developers who will not see the artifact.

The artifact is a canvas of 11 static HTML boards (`project/*.dc.html`, laid out by
`project/canvas.json`). Every board except the main window has a `dark` boolean property that
swaps the colour tokens. The URL fragment `#page-dcb81aee8d83` does not resolve to anything in
the published files (`canvas.json` has `"pages": []`); it is presumably the canvas itself.
Everything else in the artifact (`artifact-type/*`, `SKILL.md`, `index.html`) is the Design
tool's own runtime, not EP design.

There are **no image assets** worth keeping: all covers, show images and avatars are flat
colour placeholders (e.g. Homogenic `#6d7f99`), and all icons are inline SVGs described below.

Boards (canvas title → file, board size in CSS px = macOS points):

| # | Board title | File | Size |
|---|---|---|---|
| 1 | Main window — Library | `project/Main.dc.html` | 1800×1120 (light only) |
| 2 | Note editor — autocomplete, ⌘-links, broken link | `project/NoteEditor.dc.html` | 1120×720 |
| 3 | Player bar — track, long file, not playable | `project/PlayerBar.dc.html` | 1440×600 |
| 4 | Radio view | `project/Radio.dc.html` | 1440×900 |
| 5 | Track states & sources | `project/TrackStates.dc.html` | 1000×760 |
| 6 | Import — review uncertain matches | `project/ImportReview.dc.html` | 1100×780 |
| 7 | Import — start, progress, jobs | `project/ImportJobs.dc.html` | 1100×780 |
| 8 | Search (⌘K) | `project/Search.dc.html` | 1000×640 |
| 9 | Edit release group | `project/EditSheet.dc.html` | 900×740 |
| 10 | Settings | `project/Settings.dc.html` | 720×560 |
| 11 | Empty, loading, error states | `project/States.dc.html` | 1440×1000 |

Canvas section titles: "Main window", "Note editor & player", "Radio & playability", "Import",
"Search, editing, settings", "States".

---

## 1. Visual direction

Close to stock macOS (Finder/Music/Mail density), not a distinct brand look: system font, a
light grey sidebar and toolbar, white content, macOS-blue selection, hairline 1 px separators,
small radii, no gradients, no decoration. Colour is reserved for meaning (accent for links and
progress, warn/amber for "excerpt"/"needs review", danger/red for broken/missing/failed), and the
design states explicitly that **icon shape, not colour, carries playability**. Very dense lists
(22 pt artist rows, 26 pt track rows). Cover art is small thumbnails only (32–40 pt); there is no
cover grid. This answers the brief's open questions 1 and 3.

## 2. Design tokens

### 2.1 Colours (CSS custom properties, quoted exactly)

Defined on `.ep` (light) and `.ep.dark` (dark) in every board:

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--bg` | `#ffffff` | `#1e1e20` | content background, cards, buttons |
| `--side` | `#f6f6f8` | `#252527` | artist/show column, sheet footers, side panes, canvas backdrop |
| `--bar` | `#ececef` | `#2c2c2f` | toolbar, player bar, settings title bar |
| `--text` | `#1d1d1f` | `#f2f2f4` | primary text; also play button fill |
| `--text2` | `#55555a` | `#c0c0c6` | secondary text, scrubber progress, scrubber marks |
| `--text3` | `#6b6b70` | `#a0a0a7` | tertiary text, section labels, numbers, icons, dimmed (link-only/no-source) titles |
| `--line` | `#dcdce0` | `#3b3b3f` | 1 px separators and control borders, scrubber track |
| `--sel` | `#0062cc` | `#0a5bc4` | focused selection fill, primary buttons, focus borders |
| `--seltext` | `#ffffff` | `#ffffff` | text on `--sel` |
| `--selsoft` | `#dce7f7` | `#33405a` | unfocused selection, chosen radio option, ⌘-hover link bg |
| `--accent` | `#0062cc` | `#64adff` | links, text caret, progress ring/bars, drop outline |
| `--warn` | `#935300` | `#f2a649` | radio-excerpt icon and label, review badge, "needs decision" |
| `--danger` | `#bf2619` | `#ff8478` | broken link, file missing, failed, server unreachable icon |
| `--field` | `#ffffff` | `#1a1a1c` | text field background |
| `--chip` | `#ebebef` | `#36363a` | segmented-control track, kind badges, tag chips, banners, codec chips |
| `--skel` | `#ececf0` | `#2e2e31` | loading skeleton bars (States board only) |
| `--ok` | `#1d7a3a` | `#5fd187` | "Connected" in Settings (Settings board only) |
| `--shadow` | `0 12px 40px rgba(0,0,0,.18), 0 0 0 1px rgba(0,0,0,.08)` | `0 12px 40px rgba(0,0,0,.5), 0 0 0 1px rgba(255,255,255,.1)` | popovers, palettes, toasts, sources popover |

Other literal values:

- Toolbar badge text: `#fff` on `--warn` (count) or `--danger` ("!").
- Segmented control active segment shadow: `0 1px 2px rgba(0,0,0,.15)` (`.25` in Radio board).
- Tag suggestion popover shadow: `0 8px 24px rgba(0,0,0,.14), 0 0 0 1px rgba(0,0,0,.08)`.
- Cover thumbnail inner hairline: `box-shadow: inset 0 0 0 1px rgba(0,0,0,.08)`.
- "Drop image to replace" label: white 11 pt on `rgba(0,0,0,.55)`, pill radius 10.
- Traffic lights in the mock title bar (`#ff5f57`, `#febc2e`, `#28c840`) are just window chrome;
  use the real window.
- Placeholder cover colours are per item, not tokens.

Accent vs. system accent: the design hard-codes macOS-default blue (`#0062cc`) for `--sel` and
`--accent`. The brief says "follows the system accent colour unless design proposes otherwise".
The design neither proposes a custom accent nor mentions the system one. **Ambiguous**; the
natural SwiftUI reading is `Color.accentColor` / `NSColor.controlAccentColor` and
`selectedContentBackgroundColor`/`unemphasizedSelectedContentBackgroundColor`, with the hex values
as the reference for the default blue appearance.

The main window board has **no dark variant** (it defines only the light tokens and no `dark`
property). Dark appearance of the main window has to be derived from the shared dark tokens,
which the Radio board (the same frame) demonstrates.

### 2.2 Typography

- Family: `-apple-system, BlinkMacSystemFont, Inter, system-ui, sans-serif` → SF Pro (system
  font). Inter is only a web fallback.
- Monospace: `'SF Mono', ui-monospace, 'JetBrains Mono', monospace` → SF Mono
  (`.monospaced` design). Used for file paths, URLs, MusicBrainz IDs, import log, server address,
  watched-folder paths, the joining-text token.
- Numbers: times, lengths, track numbers use `font-variant-numeric: tabular-nums`
  (`.monospacedDigit()`).
- Base size **13 pt** (matches macOS body). Weights used: 400, 500, 600, 700.

| Size | Weight | Use |
|---|---|---|
| 10 | 700, letter-spacing .06em, uppercase | kind badge (ARTIST, ALBUM, TRACK, EPISODE); backlink type label (700, .04em) |
| 11 | 600, letter-spacing .05em, uppercase, `--text3` | section labels (NOTE, TAGS · 3, LINKED FROM, SHOWS · 14, DISC 2, JOBS, TOP HIT, board state labels) |
| 11 | 400 | row meta (release "1997 · Album"), player times, key-hint footers, type pills, codec chips, badges' digits (10 pt 700) |
| 12 | 400 | secondary text, column header meta, small buttons, hints |
| 12 | 600 | "Notes that link here · 3" sub-heading (`--text2`) |
| 13 | 400/500/600 | body; row titles 500 (release groups, shows, episodes); names and column headers 600 |
| 14 | 400, line-height 1.55 (main) / 1.6 (editor board) | note body text; panel title in note editor and radio detail (600) |
| 14 | 600 | sources popover title; import link field text (400) |
| 15 | 600 | import review title; note `###` heading in the editor board (15) |
| 16 | 600 | "Import" sheet heading |
| 17 | 400/600 | search palette input (400); empty-state heading "Nothing here yet" |
| 18 | 600–700 | note top heading in the main window (h3, 700); review question h2 |
| 22 | 600 | note `#` heading in the editor board |

Note heading sizes are **inconsistent** between boards: the main window renders the note's
first heading at 18/700, the editor board renders `# Mark Bell` at 22 and `### Where he shows
up` at 15. Suggest H1 22, H2 18, H3 15 (H2 is not shown anywhere — ambiguous).

Text colours: primary `--text`, secondary `--text2`, tertiary `--text3`.

### 2.3 Spacing, radii, sizes

- Separators: 1 px `--line` everywhere (column borders, header borders, row separators in
  jobs/settings lists).
- Radii: list rows 5–6; buttons 5–6 (small) / 8 (large import buttons); cards/panels/sheets 10–12;
  chips 4; tag chips and pills 10–13 (fully round); cover thumbnails 3 (32 pt) / 4 (36–40 pt);
  artist, show and episode images in player/search/autocomplete are **circles** (radius = half),
  release/track covers are rounded squares.
- Control heights: toolbar buttons 28; segmented control 24 inside a 2 pt padded track (radius
  7, segment radius 5, segment padding 0 16); search field 28 (radius 7); small buttons 22–26;
  standard buttons 28–30; large import buttons and link field 40.
- Row heights: artist 22; release group 42 (32 pt cover); track 26; disc heading ~24 (padding
  6 8 4); show 40 (28 pt round image); episode 48 (36 pt cover); tracklist entry 26; search/
  autocomplete hit 46 (36 pt image); autocomplete "no match" action 36; tag suggestion 28; tag
  chip 26; job row min 48; watched-folder row 40; edit-sheet track row 34; review question row
  ~64 (padding 9 10); radio "linked from" row min 34.
- List insets: rows sit inside 4–8 pt padding with 6–8 pt horizontal row padding, rounded
  selection inset from the column edge (Finder/Mail sidebar style).

### 2.4 Icons

Custom inline SVG line icons: 14 or 16 pt (viewBox 14/16), stroke 1.3–1.8, round caps and
joins, `currentColor`. They are close to SF Symbols; SF Symbols are the obvious SwiftUI
realisation, but the playability shapes are specific (below) and some need custom drawing.

| Icon | SVG description | Nearest SF Symbol (suggestion) |
|---|---|---|
| Back / forward | chevron, stroke 1.6, 16 pt | `chevron.left` / `chevron.right` |
| Import (+) | plus, stroke 1.6; with progress ring: 22 pt circle r9 `--line` 2 pt, arc `--accent` | `plus` + custom ring |
| Search | circle r4.5 + handle | `magnifyingglass` |
| Artist filter | three lines of decreasing width (funnel) | `line.3.horizontal.decrease` |
| Radio-only toggle | dot + two pairs of arcs | `dot.radiowaves.left.and.right` |
| Loose tracks | two-note glyph in a dashed 32 pt square | `music.note` in dashed box |
| Own file | filled dot r3 | `circle.fill` (small) |
| Range in single-file album | outlined 11×5 rounded rect with filled middle third | custom |
| Radio excerpt | dot + two arcs, `--warn` | `dot.radiowaves.left.and.right` (warn) |
| Link only | arrow up-right | `arrow.up.right` |
| No source | dashed circle r4 (dasharray 2 2) | `circle.dashed` |
| File missing | circle r5 with "!" in `--danger` | `exclamationmark.circle` |
| Now playing | three vertical bars | `chart.bar.fill` / animated bars |
| Collapse panels | chevron down 13 pt | `chevron.down` |
| Show playing | target (circle + filled dot) | `scope` / `smallcircle.filled.circle` |
| Queue | list lines + plus | `text.badge.plus` / `list.bullet` |
| Volume | speaker with one wave | `speaker.wave.1` |
| Transport | prev/next with bar; play/pause in a 30 pt filled `--text` circle | `backward.end.fill`, `forward.end.fill`, `pause.fill` |
| Remove tag | 9 pt × | `xmark` |
| Job states | review: filled circle with white "!" (`--warn`); failed: circle with × (`--danger`); waiting: clock; done: check (`--text3`) | `exclamationmark.circle.fill`, `xmark.circle`, `clock`, `checkmark` |
| Drag handle | 2×3 dots | `line.3.horizontal` / custom grip |
| Server error | circle with "!" `--danger` 16 pt | `exclamationmark.circle` |

---

## 3. Main window — Library (board 1)

Board 1800×1120. Top to bottom (heights in pt):

```
┌ toolbar 52 ──────────────────────────────────────────────────────────────────┐
│ ● ● ●  ‹  ›  (+)¹        [ Library | Radio ]          [🔍 Search      ⌘K]    │
├ lists 500 ─────────────┬─────────────────────┬────────────────────────────────┤
│ Artists 25fr ≥300      │ Release groups 30fr │ Tracks 45fr ≥460               │
│  (--side bg)           │  ≥340               │                                │
├ divider 7 (grabber) ───┴─────────────────────┴────────────────────────────────┤
├ detail panels (flex) ──┬─────────────────────┬────────────────────────────────┤
│ ARTIST Björk        ⌄  │ ALBUM Homogenic   ⌄ │ TRACK Jóga                   ⌄ │
│ NOTE / TAGS / LINKED   │ NOTE / TAGS / …     │ NOTE / TAGS / LINKED FROM      │
├ player bar 64 ─────────┴─────────────────────┴────────────────────────────────┤
│ [cover] Jóga            ⏮ ⏸ ⏭                   [◎ Show playing] ≡ 🔈 ───     │
│         Björk — Homogenic  1:42 ━━━━──────── −3:23                            │
└───────────────────────────────────────────────────────────────────────────────┘
```

Column grid (lists and detail panels share it, so each detail panel sits under its column):
`grid-template-columns: minmax(300px, 25fr) minmax(340px, 30fr) minmax(460px, 45fr)`.
Minimum total ≈ 1100 pt; the design does not show narrower behaviour (13-inch laptop is
1440 or 1280 wide — **ambiguous** how the three columns degrade below ~1100 pt).

### 3.1 Toolbar (height 52, `--bar`, bottom border `--line`, padding 0 12 0 16, gap 8)

- Unified title bar/toolbar: traffic lights at left, then (margin-right 12):
- **Back** button 30×28, radius 6, transparent, chevron-left 16 pt, aria-label "Back (⌘[)".
- **Forward** button same, shown disabled in `--text3`, aria-label "Forward (⌘])".
- **Import** button 34×28, margin-left 4. Icon 22 pt: grey ring (r9, 2 pt `--line`), accent
  arc showing overall progress, plus sign in `currentColor`. Badge top-right (top 0, right −2):
  min-width 15, height 15, radius 8, `--warn` bg, white 10 pt 700 digit "1".
  aria-label: "Import — 1 running, 1 waiting for review".
  States (board 11): **Running** — ring shows overall progress; **Needs review** — count
  badge in `--warn`, "⇧⌘I opens straight to it"; **Failed** — `--danger` badge with "!", "until
  the job is opened or retried".
- Flexible space, then the **Library | Radio** segmented control centred (track `--chip`,
  padding 2, radius 7; segments height 24, padding 0 16, radius 5; active segment `--bg`,
  600 weight, shadow; inactive `--text2`).
- Flexible space, then the **search field**: width 280 (260 in Radio board), height 28, radius
  7, `--field`, 1 pt `--line` border, magnifier 14 pt, placeholder "Search", trailing key hint
  "⌘K" (11 pt, 1 pt border, radius 4). aria-label "Search library".

### 3.2 Artists column (`--side` bg, right border)

- No column title. Top row (padding 8 10 6): filter field (height 24, radius 6, `--chip` bg,
  funnel icon 12 pt, placeholder **"Filter 2,418 artists"** at 12 pt) and a 26×24 icon
  button for radio-only artists (aria-label "Show artists known only from radio (1,206
  hidden)", tooltip "Show radio-only artists").
- Rows: height **22**, padding 0 8, radius 5, name only, single line with ellipsis. Selected
  row (Björk) shown in `--selsoft` because focus is in another column.
- Type-to-select is required by the brief; not visualised.

### 3.3 Release groups column

- Header 32 pt, padding 0 12, bottom border: artist name (600) left, "11 releases · by date"
  (12 pt `--text3`) right.
- Rows: height 42, padding 0 6, radius 6, gap 10: cover 32×32 radius 3 with inner hairline,
  then title (500, ellipsis) over meta "1997 · Album" / "1997 · Single" (11 pt `--text3`).
  Selected (Homogenic) in `--selsoft`.
- Last row: **"Loose tracks"** pseudo-entry, cover slot is a dashed 1 pt `--text3` 32 pt square
  with a music-note icon; second line "4 recordings on no release".
- Then **"Appears on"** (section label like DISC 2): albums by others with a track
  crediting the artist (a featured credit), rows as above with the album's artist leading
  the meta: "Björk · 2001 · Album". An artist with only these does not get the empty state.

### 3.4 Tracks column

- Header 32 pt, padding 0 14: album title (600), meta "1997 · Album · 10 tracks · 43:35"
  (12 pt `--text3`), flexible space, **"Play album"** button (height 22, padding 0 10, 1 pt
  border, radius 5, 12 pt).
- Rows: height 26, radius 5, padding 0 6, grid columns `22px 26px minmax(0,1fr) 160px 46px`:
  1. playability icon (14 pt; see 3.5),
  2. track number (tabular, `--text3`) — replaced by the **now-playing bars** icon on the
     playing track,
  3. title (ellipsis; italic for no-source),
  4. a 160 pt **note column** at 11 pt `--text3`: state text ("only in a radio episode",
     "Bandcamp link", "no source", "file missing" in `--danger`) or the artist credit when it
     differs from the album's ("Björk feat. Robert Wyatt", 12 pt `--text2` in board 5),
  5. length, right-aligned, tabular, `--text3`.
- Row tooltip = state label: "Playable from its own file", "Playable only as an excerpt inside
  a radio episode", "Link only — opens in browser", "No source — metadata only".
- Selected + playing row (Jóga): solid `--sel` with `--seltext` for all text and icons.
  Link-only and no-source titles are dimmed to `--text3`; no-source is also italic.
- Disc headings (board 5): "DISC 2", 11 pt 600 `--text3`, letter-spacing .04em, padding 6 8 4.

Selection colour logic: the focused column's selection is solid `--sel`; selections in the
other columns are `--selsoft` (macOS emphasised/unemphasised selection).

### 3.5 Track playability states (board 5, "icon shape carries the meaning, not colour")

| State | Icon (14 pt) | Colour | Row treatment / copy |
|---|---|---|---|
| Own file | filled dot r3 | `--text3` | "Plays. The quiet default — a small dot, never louder than the title." |
| Range in a single-file album | outlined bar with filled middle segment | `--text3` | "Plays. Seeks into the shared file; scrubber shows the whole file." |
| Only as a radio excerpt | dot with radio arcs | `--warn` | "Plays the episode at that point. May be talked over or mixed — the row says “excerpt”." (main board note text: "only in a radio episode") |
| Link only | arrow up-right | `--text3` | "Title dimmed. Return opens the link in the browser; ⌥Return offers Import." |
| No source | dashed circle | `--text3` | "Metadata only. Title dimmed and italic." |
| File missing on disk | circle with ! | `--danger` | "Known file, not found at its path. Offers Locate… and lists other sources." Row dimmed, note column "file missing" in `--danger`. |

### 3.6 Sources popover (board 5)

A floating card (radius 12, `--shadow`, `--bg`, width ≈ 450) titled **Sources for “Jóga”**
(14 pt 600), subtitle "Recording · 3 sources · choose which one plays" (12 pt `--text2`).
A radio group (aria-label "Preferred source"), each option padding 10, radius 8, the chosen one
on `--selsoft`:

1. **File** · chip "FLAC 16/44.1" (11 pt, `--chip`, radius 4) · "from Bandcamp · 3 Jan 2026";
   second line mono 11 pt path `~/Music/EP/Björk/Homogenic/02 Jóga.flac` (ellipsis).
2. **Range in radio episode** · chip "MP3 320" · "excerpt" in `--warn`; second line "Late Night
   Tapes — 14 Mar 2025 · 28:02 – 32:51".
3. **Link** · "YouTube · not downloaded"; mono "youtube.com/watch?v=…". Radio **disabled**
   (links can't be the playing source); whole option in `--text2`.

Footer (border-top, padding 10 14, right-aligned): **Show in Finder**, **Add source…**.
How the popover is opened is **not shown** (presumably from the track row or the detail panel).

### 3.7 Divider and detail panels

- One horizontal divider for all three panels: height 7, `--side`, borders top and bottom,
  centred grabber 36×3 radius 2 `--line`, `cursor: row-resize`. Draggable.
- **Three detail panels side by side, one per column** (artist, album, track), aligned to the
  column grid, each with a right border, `--bg` background.
- Panel header 40 pt, padding 0 8 0 14, bottom border: kind badge (10 pt 700, letter-spacing
  .06em, padding 2 6, radius 4, `--chip`, `--text2`: **ARTIST**, **ALBUM**, **TRACK**), name
  (600, ellipsis), flexible space, collapse button 24×24 with chevron-down, aria-label
  "Collapse detail panels (⌥⌘D)", tooltip "Collapse panels".
- Panel body scrolls (padding 14 18 24), three **stacked sections, not tabs**, 22 pt apart.
  Each section title: 11 pt 600 uppercase `--text3`, letter-spacing .05em, padding-bottom 6,
  bottom border, margin-bottom 8: **NOTE**, **TAGS · 3**, **LINKED FROM**.

**Note section**: article max-width 720, 14 pt, line-height 1.55. Rendered blocks: heading
(18/700), paragraph (margin-bottom 8), bullet list item ("•" in `--text3`, gap 8, margin 0 0 4 4),
blockquote (padding-left 12, 3 pt left border `--line`, `--text2`). Links in `--accent`, no
underline (underline on hover). Broken link: `--danger`, dashed underline offset 3, tooltip
"Link target no longer exists". Link with caret inside: `[[` and `]]` in `--text3`, text in
`--accent`, a 2×17 `--accent` caret.

Sample note copy (useful as fixtures):
- Artist Björk: "Start with Homogenic, then go sideways to Vespertine for the quiet version of
  the same idea." / "Collaborators worth following: Mark Bell, Arca, Matmos" / "Before all
  this: The Sugarcubes — artist was merged, relink" (broken) / quote "Every record sounds like
  a different room."
- Album Homogenic, Track Jóga: similar.

**Tags section**: wrapped chips, gap 6. Chip height 26, radius 13, padding 0 4 0 10, `--chip`:
"#name" (a link, tooltip "Show everything tagged {name}"), usage count 11 pt `--text3`, remove
button 18×18 round with 9 pt × (aria-label "Remove tag {name}"). Then an add field: dashed 1 pt
`--text3` pill, height 26, placeholder **"+ Add tag"**, width 90.
Suggestion popover (album panel): width 240, radius 8, padding 4: header "Typing “str”"
(11 pt `--text3`), rows height 28 radius 5 with name left and count right (first row selected
in `--sel`): "#strings 42", "#string-quartet 6", then "Create “#str”" in `--text2`.
Hints under the chips (12 pt `--text3`), one per entity kind:
- Artist: "Click a tag to list every artist, album and track with it."
- Album: "Return adds the highlighted tag. Tags on an album do not copy to its tracks."
- Track: "Tags on a track belong to its recording, so they also show on the radio excerpt."

**Linked from section**:
- Sub-heading "Notes that link here · {n}" (12 pt 600 `--text2`). Rows (padding 6 8, radius 6,
  whole row is a link): line 1 = type label (10 pt 700 `--text3`: ARTIST / TRACK / EPISODE /
  SHOW) + entity name (600); line 2 = snippet "…before **link** after" in `--text2` with the
  link text in `--accent`, single line, ellipsis.
- Sub-heading "Radio episodes that played Björk · 3" / "Radio episodes that played this album ·
  2" / "Radio episodes that played this · 2". Rows (grid `26px 1fr auto`, min-height 34): round
  24 pt play button (1 pt border, play triangle 9 pt; aria-label "Play episode from {pos}"),
  "{show} — {date}" over "{what}" (12 pt `--text3`, e.g. "Jóga", "Opening track, talked over",
  "Into Arca — Piel"), time position right (12 pt tabular `--text2`). **Estimated** positions
  are prefixed "~" and set in italic ("~1:12:40", "~0:47").

### 3.8 Player bar (64 pt, `--bar`, top border)

Grid `minmax(240px,1fr) minmax(420px,2fr) minmax(240px,1fr)`, gap 16, padding 0 16.
- Left: cover 40×40 radius 4; title (600, link → navigates to the track) over "Björk —
  Homogenic" (12 pt `--text2`).
- Centre: transport row centred, gap 14: previous (16 pt), play/pause in a 30 pt circle filled
  `--text` with `--bg` glyph (aria-label "Pause (Space)"), next. Below, gap 4: elapsed "1:42",
  scrubber (4 pt track `--line`, radius 2, progress in `--text2` — neutral, not accent),
  remaining "−3:23" (11 pt tabular `--text2`).
- Right, gap 12: **Show playing** button (height 26, 1 pt border, radius 6, target icon,
  12 pt, aria-label "Show what is playing (⌘L)", tooltip "Show playing — ⌘L"), **queue** button
  (aria-label "Queue — 8 up next"), speaker icon and volume slider (width 90).

---

## 4. Player bar states (board 3)

The board shows the bar in isolation (grid `320px 1fr 300px`, gap 24) — slightly different from
the main window's grid; treat the main window as authoritative for proportions.

1. **TRACK FROM ITS OWN FILE** — as 3.8. Right side here shows a "Show playing ⌘L" chip and
   "Up next: Unravel · 8" instead of queue/volume (**inconsistent** with board 1, which has
   queue icon + volume; the right column content is ambiguous; suggest: Show playing, Up next
   text when space allows, queue button, volume).
2. **RADIO EPISODE — ONE LONG FILE, TRACKLIST MARKS (HOLLOW = ESTIMATED TIME)** — bar grows to
   96 pt (items aligned to bottom, padding-bottom 12). Left: **round** 40 pt episode image;
   context line "Late Night Tapes · 14 Mar 2025 · 7 of 31" (11 pt `--text3`); current entry
   title "Jóga" (600 link); "Björk · also in library as a file" (the latter in `--text3`).
   Centre: transport on its own row above the scrubber. Scrubber 14 pt tall: 4 pt track,
   progress to 26 %, the **current entry's range** highlighted as an 8 pt `--accent` band at
   35 % opacity, and **marks** for each tracklist entry: 2×12 pt, radius 1, 1 pt `--text2`
   border; filled `--text2` = exact time, **hollow** (filled `--bar`) = estimated. Hover tooltip
   above the scrubber (inverted: `--text` bg, `--bg` text, radius 6, 11 pt): "~58:40 · Jon
   Hassell — Dreamworld (time estimated)". Times "31:08" / "−88:52". Right: "Previous / next
   jump between entries". aria-label of scrubber: "Episode scrubber, 31 tracklist marks, 4
   estimated".
3. **SINGLE-FILE ALBUM — RANGE INSIDE ONE FILE** — 64 pt; context line "Track 4 of 14 · one
   file"; title "Desired Constellation"; "Björk — Medúlla"; scrubber over the whole file with
   13 solid marks (no transport drawn in this variant — presumably omitted for space; keep
   transport). Right: "Scrubber covers the whole file". Times "9:12" / "−36:31".
4. **NOTHING PLAYING** — 64 pt, `--text3`: dashed 40 pt empty cover + "Double-click or press
   Return on a track to play."
5. **TRIED TO PLAY A LINK-ONLY TRACK** — 64 pt: link icon 18 pt, "**Alarm Call** has no audio
   here — only a Bandcamp link.", buttons **Open in Bandcamp** (secondary) and **Import…**
   (primary `--sel`).

In the Radio board the bar's title is "Björk — Jóga" with context "Late Night Tapes · 7 of 31",
transport inline left of the scrubber, aria-labels "Previous entry" / "Next entry".

---

## 5. Note editor (board 2)

Board shows the editor as a standalone card (640 wide) plus four state cards.

**Editor card**: header 40 pt (padding 0 16): kind badge "ARTIST", name "Mark Bell" (14 pt 600),
flexible space, save status **"Editing…"** (12 pt `--text3`) — implies autosave status text
(other values, e.g. "Saved", are **not shown**). Body padding 18 24, 14 pt, line-height 1.6:
`# Mark Bell` rendered as 22 pt heading, paragraph in `--text2` "Producer, half of LFO." (LFO a
link), `### Where he shows up` at 15 pt, bullet list; the last item is being typed:
"Co-producer on [[homgen|" with the caret.

**Link autocomplete popup** (listbox "Link suggestions"): anchored below the caret, width 420,
radius 10, `--shadow`, padding 6. Rows height 46, radius 6, grid `36px 1fr auto`, gap 10:
image 36 pt (radius 3 for album/EP/track, **round** for episode/artist), name (600, ellipsis),
second line (12 pt), and a **type pill** (11 pt, 1 pt border in the secondary colour, radius
4). First row selected in `--sel`. Rows shown for query "homgen" (typo tolerance):
- Homogenic — "Björk · 1997 · Album" — Album
- Homogenic Remixes Vol. 1 — "Björk · 1998 · EP" — EP
- Hunter — "Björk · Homogenic · 4:15" — Track
- Homage to Hassell — "Late Night Tapes · 2 Feb 2025" — Episode
Footer (border-top, 11 pt `--text3`, gap 14): "↑↓ choose", "↩ insert link", "⇥ insert & keep
typing", "esc close".

**⌘ HELD — LINKS BECOME TARGETS**: all links get an underline (offset 3); the hovered link also
gets a `--selsoft` background (radius 3, padding 0 2) and a pointer cursor. An inverted tooltip
(`--text` bg, 12 pt) shows "Artist · Mark Bell ⌘-click to open · ⌥⌘ new window".

**CURSOR IN A LINK — SYNTAX REVEALED**: "Pairs with [[All Is Full|of Love]]" → only `[[`/`]]`
(in `--text3`) and the display text (in `--accent`) are revealed; caption: "Only brackets and
display text show; the type and id stay hidden. Editing the text renames the link, not the
target."

**BROKEN LINK**: "See Jóga (remixes) for the mixes." — the link in `--danger` with dashed
underline plus a 12 pt `--danger` circle-! icon (aria-label "Missing"). Caption: "⌘-click offers:
**Relink…** (opens autocomplete with the old text) or **Remove link, keep text**."

**NOTHING MATCHES “[[Laurel Halo”**: the popup shows three action rows (height 36, radius 6):
1. "+ New artist “Laurel Halo”" — selected (`--sel`) — key "↩"
2. "+ New album or recording…" — key "⌥↩"
3. "🔍 Search MusicBrainz" — key "⇧↩"

This answers the brief's open question (create new or search MusicBrainz): both, with the new
artist as default.

---

## 6. Radio view (board 4)

Same frame as the main window; toolbar has "Radio" active. List area 478 pt.
Column grid: `minmax(200px, 17fr) minmax(280px, 26fr) minmax(380px, 57fr)` — **different
proportions from Library** (narrow shows column, wide tracklist).

- **Shows** (`--side`, padding 8 6): label "SHOWS · 14" (11 pt 600 `--text3`); rows height 40,
  radius 6, gap 8: round 28 pt image, name (500) over station (11 pt `--text3`, e.g. "NTS 1",
  "Rinse FM", "Cashmere Radio", "Worldwide FM", "dublab", "Radio 80000"). No filter field.
- **Episodes**: header 32 pt "Late Night Tapes" (600) / "NTS 1 · 46 episodes" (12 pt). Rows
  height 48, grid `36px 1fr`, gap 10: 36 pt cover radius 4, title (500) over "14 Mar 2025 ·
  London" (date · location, 11 pt).
- **Tracklist**: header "14 Mar 2025 — Homage to the night shift" (600) + "31 entries · 2:00:00 ·
  22 matched" (12 pt). Rows height 26, radius 5, grid `64px minmax(0,1fr) minmax(0,1.3fr)
  120px`, gap 8: time (tabular `--text3`; **estimated times prefixed "~" and italic**), artist,
  title, state (11 pt right-aligned). Matched entries: artist and title in `--accent` with
  underline (offset 2) — they are links; unmatched in `--text2`, no underline. State texts:
  "in library", "only this excerpt" (`--warn`), "not matched", "" for talk, with " · est. time"
  appended for estimated entries. A talk segment is a row "Talk" / "—". Row tooltip "Matched to
  a library recording" / "Not matched". Selected/playing entry solid `--sel`.
- **Detail panel**: a **single full-width panel** (not three), header 40 pt: badge "EPISODE",
  title "Homage to the night shift" (14 pt 600), "Late Night Tapes · 14 Mar 2025 · London"
  (`--text2`), then a **tab bar** (role tablist, margin-left 20): "Note", "Tags 2", "Linked from
  3" (height 26, padding 0 12, radius 6; selected tab `--chip` bg 600). Shown with "Linked
  from" selected: "NOTES THAT LINK HERE" then rows in a 2-column grid `200px 1fr` (type + name |
  snippet with highlighted link).

The Library board uses stacked sections in three panels; the Radio board uses tabs in one
panel. **Ambiguous / inconsistent**: see departures §13.

---

## 7. Import (boards 6, 7, and 11)

### 7.1 Import sheet — start, progress, jobs (board 7, 1100×780, radius 12)

**Start section** (padding 20 24, bottom border): heading "Import" (16 pt). A row: link field
(height 40, radius 8, **2 pt `--sel` border** when focused, 14 pt text, value
"https://fennesz.bandcamp.com/album/agora") with a **detected-kind pill** inside on the right
("Bandcamp album", 12 pt, `--selsoft`, radius 10); primary button **"Import ↩"** (40 pt, radius
8); secondary **"Choose folder…"**. Hint (12 pt `--text2`): "Paste a Bandcamp album, YouTube
video or playlist, or NTS episode — or drop a folder anywhere on this sheet. Imports keep
running after you close it."

**Jobs section**: label "JOBS" + right "Watching 2 folders" (12 pt `--text3`).
- **Active job card** (margin 0 16, padding 12, radius 10, `--side`): 36 pt cover, "**Fennesz —
  Agora**" over "Bandcamp · started 22:41", **Cancel** button. A 4-stage progress strip (grid of
  4, gap 8, each a 4 pt bar + 12 pt label): "✓ Fetching metadata" (full `--text2`), "✓ Matching"
  (full), "Downloading · 3 of 5 · 62%" (600; bar `--line` with `--accent` fill 62 %), "Done"
  (`--text3`, empty bar). A log box (`<pre>`, mono 11 pt, line-height 1.5, max-height 64,
  `--bg`, 1 pt border, radius 6, `--text2`):
  ```
  22:41:07  matched release group “Agora” (MusicBrainz)
  22:41:09  artist Fennesz → existing artist (score 0.99)
  22:41:12  downloading 03 “We Trigger the Sun” (FLAC)
  ```
- **Job rows** (min-height 48, padding 6 8, bottom border; grid `22px 1fr 180px 130px auto`,
  gap 12): state icon, title (600) over detail (12 pt), source (12 pt `--text2`), time (12 pt
  `--text3`), action button (height 26, radius 6, 600; primary style for Review):

| State | Title | Detail (colour) | Source | Time | Action |
|---|---|---|---|---|---|
| review (`--warn` icon) | Late Night Tapes — 14 Mar 2025 | Needs review · 5 of 31 entries uncertain (`--warn`) | NTS episode | Today 22:30 | **Review** (primary) |
| fail (`--danger`) | Grouper — Shade | Already in library as a different edition (Shade, 2021, 11 tracks). Editions are not supported yet. (`--danger`) | Watched folder · ~/Downloads/Music | Today 21:02 | Show existing |
| fail | youtube.com/playlist?list=PL… | Download failed: video unavailable (2 of 9). 7 imported. | YouTube playlist | Today 19:48 | Retry 2 |
| wait (clock) | Arthur Russell — Picture of Bunny Rabbit | Waiting · starts after Agora | Watched folder · ~/Downloads/Music | Today 22:42 | — |
| done (check) | Cocteau Twins — Treasure | 10 tracks · 1 new artist | Folder | Yesterday | — |
| done | Burial — Untrue | 13 tracks | Bandcamp album | Yesterday | — |

Dismiss (brief: "resolve, retry or dismiss") is **not shown** for failed jobs.

### 7.2 Review uncertain matches (board 6, 1100×780, radius 12)

- **Header** 64 pt (padding 0 20): round 36 pt image, "Review import — Late Night Tapes, 14 Mar
  2025" (15 pt 600) over "31 entries · 26 matched confidently · **5 need a decision**" (the last
  part bold `--warn`). Right: segmented filter **"Uncertain 5" | "All 31"** (Uncertain active).
- **Left pane** (430 pt, `--side`, padding 8, gap 2): question rows (padding 9 10, radius 8,
  grid `52px 1fr`, gap 8): time (tabular; italic if estimated), "Artist — Title" (600), the
  question (12 pt), and a line with the current pick as an outlined pill (radius 10) + score
  (12 pt). First row selected (`--sel`). Rows:
  - 10:02 · Alice Coltrane — Turiya and Ramakrishna · "Same recording as library track?" ·
    [Same recording] 0.82
  - 18:15 · Laurel Halo — Unknown title · "Artist not in library" · [New artist] no candidates
  - 33:10 · Arca — Piel · "Which artist “Arca”?" · [Arca (Venezuelan producer)] 0.91 · 1 other
  - ~58:40 · Jon Hassell — Dreamworld · "Same recording as library track?" · [Same recording] 0.67
  - ~66:05 · Beverly Glenn-Copeland — Ever New · "Two artists with this name" ·
    [Beverly Glenn-Copeland] 0.74 · 1 other
  Footer of the pane (border-top, 12 pt `--text2`): "› 26 confident matches, 4 entries left as
  plain text (talk, IDs)" (a disclosure).
- **Right pane** (padding 20 24, gap 16): eyebrow "ENTRY 4 OF 31 · 10:02"; question h2 (18 pt)
  "Is this the same recording as the one in your library?". **Comparison table** (radius 10, 1 pt
  border, grid `110px 1fr 1fr`; header row `--side` with "Incoming" | "In library" in 600):
  - Title: Turiya and Ramakrishna | Turiya & Ramakrishna
  - Artist: Alice Coltrane | Alice Coltrane
  - Length: ~8:13 (range in episode) | 8:20
  - From: NTS tracklist | Ptah, the El Daoud · FLAC, watched folder
  **Decision radio group** (each option padding 10 12, radius 8, 1 pt border; chosen option has a
  2 pt `--sel` border and `--selsoft` bg; trailing key cap `.kbd`: min-width 16, padding 0 4,
  1 pt border, radius 4, 11 pt, line-height 16):
  1. "**Same recording** — link this excerpt to it · score 0.82" — key 1 (preselected)
  2. "Turiya & Ramakrishna (live, 1971) · score 0.44" — key 2
  3. "New recording" — key N
  4. "Leave unmatched (plain text)" — key U
  Then a secondary button "Search the library for another…" with key "/".
- **Footer** 56 pt (`--side`, 12 pt `--text2`): key legend "↑ ↓ question", "1–9 candidate",
  "↩ accept & next", "space preview audio"; right: **Later** (secondary) and **"Confirm all 31
  ⌘↩"** (primary).

Only the "same recording?" question has a drawn detail pane. Detail panes for "which artist",
"artist not in library", "which MusicBrainz release" and the edition conflict are **not drawn**;
the edition conflict appears only as a failed job (7.1). The "none, keep as entered" option for
MusicBrainz release is not shown.

### 7.3 Import states elsewhere (board 11)

- Toolbar button states (3.1).
- **Toast** (role status, padding 10 12, radius 10, `--shadow`): round 30 pt image, "**Late
  Night Tapes is ready for review**" over "5 of 31 entries need a decision" (12 pt), primary
  **Review** button. Position on screen and duration are **not specified**.

---

## 8. Search (board 8)

Command-palette style overlay over the window (shown 56 pt from the top, centred), width 640,
radius 12, `--shadow`, role dialog.
- Input row 52 pt (padding 0 16, bottom border): magnifier 18 pt `--text3`, input 17 pt
  (value "bjork vesp"), trailing "esc" (11 pt `--text3`).
- Results (padding 6, max-height 470), grouped with 11 pt 600 uppercase headers (padding 8 10
  4): **TOP HIT**, **TRACKS**, **ARTISTS**, **RADIO**. Rows identical to link autocomplete
  (46 pt, 36 pt image, name, second line, type pill). First row selected.
  - TOP HIT: Vespertine — "Björk · 2001 · Album · 12 tracks" — Album
  - TRACKS: Hidden Place — "Björk · Vespertine · 5:28"; Pagan Poetry — "… 5:14"; Cocoon — "… 4:28"
  - ARTISTS: Björk — "11 releases · 3 loose tracks" — Artist (round image)
  - RADIO: Strings only — "Late Night Tapes · 24 Nov 2024 · plays “Cocoon” at 41:10" — Episode
- Footer (`--side`, 11 pt `--text2`, gap 16): "↩ open", "⌘↩ play", "⌥↩ play next", "⌘C copy
  link", right-aligned "Matches ignore accents and small typos".

---

## 9. Edit release group sheet (board 9, 900×740, radius 12)

- Body (padding 22 24, grid `180px 1fr`, gap 24).
- **Cover** 180×180 radius 6, drop target state: 2 pt dashed `--accent` outline offset 4, label
  "Drop image to replace" at the bottom (aria-label "Cover — drop an image to replace"); button
  **"Choose image…"** (height 26, full width) below.
- **Form** (grid `110px 1fr`, gap 10 12, labels right-aligned `--text2`): Title (text field 28 pt,
  radius 6, "Medúlla"); **Artist credit** (token field: chip "Björk" + "+ artist" button); Type
  (pop-up Album/EP/Single) and Date on one row (date as text "2004-08-30", width 110);
  MusicBrainz: mono 11 pt "release-group · 3c1c…a92e" + **Detach** button.
- **Tracks table** (1 pt border, radius 8): header "TRACKS · DRAG TO REORDER" with "+ Track"
  button; rows 34 pt, grid `18px 26px 1fr 1fr 44px`: drag grip, number, title, credit (12 pt
  `--text3`), length. Rows: Pleasure Is All Mine 3:26; Show Me Forgiveness 1:23; Where Is the Line
  — Björk feat. Robert Wyatt 4:41 (selected, `--selsoft`); Vökuró 1:54; Öll birtan 1:52.
- **Inline credit editor** for the selected track (below the table, `--selsoft` bg): label
  "Credit for track 3 — artists with joining text"; token field with 2 pt `--sel` border: chip
  "Björk", joining-text token "␣feat.␣" (dashed border, mono 12 pt), chip "Robert Wyatt", caret.
  Help: "Reads as “Björk feat. Robert Wyatt”. Type to search artists; ⌥, inserts joining text."
- Footer 56 pt (`--side`): "Changes stay in EP — audio files are never rewritten." (12 pt
  `--text2`); **Cancel**; **"Save ⌘S"** (primary).

Not designed: editing artists, tracks/recordings or radio shows/episodes; creating entities by
hand (only the "Add album by hand…" button in states and "New artist" in autocomplete); how the
sheet is opened (no shortcut shown, e.g. ⌘E or ⌘I); "edit in place" (the brief allowed either; the
design chose a sheet).

---

## 10. Settings (board 10, 720×560)

Window with a centred title bar "Settings" (44 pt, `--bar`) — a single pane, no tabs.
- Form grid `150px 1fr`, gap 8 14, labels right-aligned `--text2`:
  - **Server**: mono field "http://ep.local:7710" + status "● Connected" (12 pt, 8 pt dot, `--ok`).
    Under it: "Server 0.9.2 · 2,418 artists · 31,904 tracks" (12 pt `--text2`).
  - **Library location**: mono "/Volumes/Music/EP" + "(set on the server)" in `--text3`
    (read-only).
- **Watched folders** — bold title + "New folders here are imported automatically." (12 pt).
  Bordered list (radius 8): rows 40 pt with an enable checkbox, mono path, right status (12 pt
  `--text3`):
  - ☑ ~/Downloads/Music — "last import 21:02"
  - ☑ /Volumes/Music/incoming — "idle"
  - ☐ /Volumes/Archive/rips — "paused" (row in `--text3`)
  Footer bar (`--side`) with "+" (aria "Add folder") and "−" (aria "Remove selected folder")
  buttons 26×22 — standard macOS list +/− pattern.

The example address `ep.local:7710` is design data; the real default host/port comes from the
backend.

---

## 11. Empty, loading and error states (board 11)

1. **EMPTY LIBRARY — FIRST LAUNCH**: centred: music-note icon 44 pt (stroke 1.3 `--text3`),
   "Nothing here yet" (17 pt), "Bring music in once, or point EP at a folder and it will keep
   importing what lands there." (max-width 300, `--text2`), buttons **Import…** (primary, 30 pt)
   and **Watch a folder…**, then "or paste a link anywhere with ⌘V" (12 pt `--text3`).
2. **SERVER UNREACHABLE**: a banner under the toolbar (role alert, `--chip` bg, padding 10 14):
   danger icon, "**Can’t reach ep.local:7710.** Retrying in 8 s." (countdown in `--text2`),
   buttons **Retry**, **Settings…** (24 pt). The last-loaded content stays visible at 55 %
   opacity. Footer note: "Showing what was last loaded. Editing and playback of server files are
   paused; notes are read-only until reconnected." (implies a client-side cache of last loaded
   data — see departures).
3. **LOADING — LISTS AND NOTE**: skeleton rows: 32 pt square + two bars (10 pt and 8 pt tall,
   radius 3, `--skel`, varying widths 40–70 % / 26–44 %). Note skeleton: 12 pt bar 40 % + 9 pt bars
   92 %, 78 %; label "Note — loading (shown only after 300 ms)" — i.e. no skeleton for fast loads.
4. **ARTIST WITH NO RELEASES**: header "Beverly Glenn-Copeland · known from radio only"; centred
   "No albums in your library. Played in [4 radio episodes]." (link) and buttons **Add album by
   hand…**, **Import…**.
5. **ALBUM WITH NO PLAYABLE TRACKS**: banner (`--chip`): link icon, "None of these 8 tracks play
   here. 6 have a Spotify link.", primary **Import…** (24 pt). Rows all dimmed with link-only or
   no-source icons (no-source titles italic).
6. **IMPORT BUTTON — RUNNING, REVIEW, FAILED** and the review toast (7.3).

Not shown: loading state for the radio view, search with no results, search loading, empty
tags, note-save failure, "file missing" Locate… dialog, server unreachable on first launch (no
cached data).

---

## 12. Interactions and keyboard shortcuts

From the brief and confirmed by the design (aria-labels, hints):

| Shortcut | Action | Source |
|---|---|---|
| ⌘[ / ⌘] | Back / forward through navigation history | brief + aria-labels |
| ⌘K | Search palette (brief also ⌘F; design shows only ⌘K) | toolbar hint |
| Space | Play/pause (not while typing) | aria "Pause (Space)" |
| Double-click / Return | Play track | player empty state copy |
| **⌘L** | Show playing — jump columns + detail to the playing item | **new** |
| **⌥⌘D** | Collapse / expand detail panels | **new** |
| **⇧⌘I** | Open the import sheet straight at review | **new** |
| **⌘V** (anywhere) | Paste a link to start an import | **new** |
| Return on link-only track | Open the link in the browser | **new** |
| **⌥Return** on link-only track | Offer Import | **new** |
| `[[` in a note | Link autocomplete; ↑↓ choose, ↩ insert link, **⇥ insert & keep typing**, esc close | brief + new ⇥ |
| ↩ / **⌥↩** / **⇧↩** in autocomplete with no match | New artist / New album or recording… / Search MusicBrainz | **new** |
| ⌘ held | Links become click targets | brief |
| ⌘-click | Navigate to link target; on a broken link: menu Relink… / Remove link, keep text | brief + new |
| **⌥⌘-click** | Open link target in a **new window** | **new** |
| Search: ↩ / **⌘↩** / **⌥↩** / **⌘C** | Open / play / **play next** / **copy link** | brief ("a modifier plays") + new |
| Review: ↑↓, 1–9, N, U, /, ↩, space, ⌘↩ | Question, candidate, new, unmatched, search library, accept & next, preview audio, confirm all | **new** |
| Edit sheet: **⌥,** / **⌘S** | Insert joining text in a credit / Save | **new** |
| Tags: Return | Add highlighted suggestion | design hint |

Implied but not specified: arrow-key movement between the three columns, type-to-select in
lists, media keys, Escape closing sheets/palette, the menu bar structure (not designed at all).

---

## 13. Departures from and additions to the brief

**Layout changes**
1. **Detail panel**: the brief has one full-width tabbed panel (Note | Tags | Linked from) that
   shows the most recently selected entity. The Library design has **three detail panels side
   by side, one under each column** (artist, album, track at once), each showing Note, Tags and
   Linked from as **stacked sections, no tabs**, with one shared resize divider and a collapse
   chevron (⌥⌘D). The Radio design instead uses **one full-width panel with tabs** ("Note",
   "Tags 2", "Linked from 3") for the episode. **Inconsistent; needs a decision** (whether the
   radio view should also get show/episode/[entry] panels, and what the third Radio panel would
   show).
2. Panels follow the selection at every level simultaneously, plus ⌘L "Show playing" — this
   resolves the brief's open question 2 without a pin feature.
3. Column proportions given: Library 25/30/45 (min 300/340/460); Radio 17/26/57 (min
   200/280/380).
4. Column headers added: release-group column header ("Björk · 11 releases · by date"), track
   column header with album meta and a **Play album** button; artists column has a filter field
   instead of a title.

**New UI elements**
5. Artist filter field with live count placeholder ("Filter 2,418 artists"); radio-only toggle is
   an icon button with the hidden count in its label.
6. Track rows get a **160 pt note column** (state text or differing artist credit) and a
   now-playing bars icon in place of the number.
7. New track state **"File missing on disk"** with its own icon and **Locate…** (the brief lists
   it only under "States to cover").
8. **Sources popover** with preferred-source radio group, codec chips, provenance and
   **Show in Finder / Add source…**.
9. Player: **"Show playing" button (⌘L)**, queue button with count / "Up next: … · 8", entry
   context line ("7 of 31", "Track 4 of 14 · one file"), "also in library as a file",
   current-entry highlight band on the scrubber, hover tooltip on marks, round episode artwork,
   bar grows to 96 pt for episodes, a "nothing playing" state, and a "tried to play a link-only
   track" state with **Open in Bandcamp / Import…**.
10. Note editor: save status text ("Editing…"), ⇥ insert & keep typing, the no-match actions
    (**New artist / New album or recording… / Search MusicBrainz**), broken-link
    **Relink… / Remove link, keep text**, hover tooltip showing target type and name, **⌥⌘-click
    opens in a new window** (implies multiple main windows), "editing the text renames the link,
    not the target".
11. Tags: usage counts on chips, "Create “#str”" in suggestions, per-kind explanatory hints.
12. Linked from: estimated radio positions shown with "~" and italic.
13. Import: start and job queue combined in **one "Import" sheet** (the brief's steps 1, 2 and 4);
    auto-detected **link-kind pill**; drop a folder anywhere on the sheet; "Watching 2 folders";
    active-job card with a 4-segment stage bar and log; job actions **Review / Show existing /
    Retry N / Cancel**; partial failures ("2 of 9 … 7 imported"); "Waiting · starts after
    Agora" (implies sequential job execution).
14. Review: a separate review sheet with **Uncertain/All filter**, question list + detail pane,
    side-by-side comparison, **numbered candidates with keyboard keys**, "Leave unmatched (plain
    text)", "Search the library for another… /", **space previews audio**, **Later**, **Confirm
    all ⌘↩**.
15. Import notifications: **toast** "… is ready for review", toolbar badge states, **⇧⌘I**.
16. **⌘V anywhere** starts an import from a pasted link.
17. Search: results **grouped** (TOP HIT / TRACKS / ARTISTS / RADIO), **play next** (⌥↩) and
    **copy link** (⌘C); radio results show where the match plays ("plays “Cocoon” at 41:10").
18. Edit sheet: token-based artist credit with joining-text tokens; **Detach** MusicBrainz ID;
    drag-to-reorder tracks; "+ Track"; footer reassurance copy.
19. Settings: connection status, server version and library counts, per-folder status (last
    import / idle / paused).
20. States: server unreachable keeps **stale data visible**, retries with a countdown, makes notes
    read-only — implies a **client-side cache** of the last loaded data (not in the brief);
    note-loading skeleton only after **300 ms**; artist-with-no-releases shows radio play count
    and "Add album by hand…"; album-with-no-playable-tracks banner counts links.

**Renamed / vocabulary**
21. User-facing kind labels: **ARTIST, ALBUM, TRACK, EPISODE, SHOW**. "Release group" is "Album"
    (type pills Album / EP / Single); "Recording" appears only in the sources popover subtitle
    ("Recording · 3 sources") and in "New recording" / "4 recordings on no release". The detail
    panel for a recording is labelled TRACK.
22. Playability phrasing: "Own file", "Range in a single-file album", "Only as a radio excerpt",
    "Link only", "No source", "File missing on disk".

**Dropped / not covered**
23. ⌘F for search (only ⌘K shown).
24. No queue view/popover, no playlists (out of scope anyway), no standalone notes (open
    question 5 unanswered), no cover-grid option.
25. The list of everything with a tag (destination of a tag click) is not designed.
26. The review sub-questions "which MusicBrainz release" (with "none, keep as entered") and the
    artist-choice detail pane are not drawn; edition conflict is shown only as a failed job.
27. Dismissing a failed job is not shown.
28. Main window dark appearance not drawn; responsive behaviour below ~1100 pt not shown.
29. VoiceOver: aria-labels are given for many controls (quoted above) — usable as accessibility
    labels; Dynamic Type not addressed.
30. Fixed blue accent rather than an explicit choice about the system accent colour (§2.1).

## 14. Open ambiguities (decide before or during the build)

1. One tabbed detail panel vs. three stacked panels (Library vs. Radio boards disagree).
2. What the third detail panel shows in Radio if the three-panel layout is used.
3. Player bar right side contents (board 1 vs. board 3).
4. System accent colour vs. fixed blue.
5. Note heading scale (H1 22 / H3 15 in editor board vs. 18 in main window; H2 missing).
6. Narrow-window behaviour (13-inch) — which columns shrink or collapse.
7. How the Sources popover and the Edit sheet are opened (no shortcut or menu shown).
8. Destination of a tag click; the queue UI; menu bar contents.
9. Review detail panes for question types other than "same recording".
10. Toast placement and lifetime; autosave status values beyond "Editing…".
11. Offline cache semantics implied by "Showing what was last loaded" (needs backend/client
    agreement).
12. ⌥⌘-click "new window": the brief says several windows may use the backend, but multiple
    main windows are a new requirement.

---

## Harness reference

The existing harness lives in `~/Development/transmission-remote-gtk` (a Rust/GTK 4 app; the
harness was added in commit `20c8192 Add macOS UI test tooling and skill`, with a Linux variant
in `f8b5656`). Files:

- `scripts/ui-test/uitest` — bash front end (415 lines); every operation goes through it.
- `scripts/ui-test/trgui.swift` — a single-file Swift CLI (375 lines) for real input,
  window listing, accessibility tree and menu bar, compiled on demand.
- `scripts/ui-test/example-scenario.sh` — a complete scripted test ending in `PASS`.
- `.claude/skills/macos-ui-debugging/SKILL.md` — how agents are told to use it (a Claude Code
  skill; its `description` front matter makes it load when an agent needs to run, drive or
  screenshot the app).
- `src/debug_snapshot.rs` + cargo feature `debug-snapshot` — in-app offscreen rendering.
- `scripts/ui-test/linux/*` + `.claude/skills/linux-ui-debugging/SKILL.md` — a Docker
  container with headless GNOME Shell and AT-SPI driving; irrelevant for a macOS-only app.

### How it works

**Isolated environment.** `uitest start` wipes and recreates a scratch dir
(`<repo>/target/ui-test/env`, gitignored, override `TRG_UITEST_DIR`), starts a private backend
(`transmission-daemon -f` in the background on `127.0.0.1:19091`, `TRG_UITEST_PORT`; no auth,
no network features; `-f` because daemonised mode exits silently), polls until it answers,
seeds deterministic fixtures (two torrents generated from random files with
`transmission-create`: one complete, one multi-file not downloaded), and writes a scratch app
config (fixed 1000×700 window, pane position, auto-connect, 1 s update interval). The app runs
with `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_CACHE_HOME`/`XDG_STATE_HOME` pointing into the scratch
dir, so the user's real config is never touched; the skill forbids starting it any other way or
using the installed /Applications bundle. Layout: `daemon/`, `daemon.log`, `app.log`, `app.pid`,
`daemon.pid`, `shots/`, `snapshots/`, `xdg/…`.

**Launching.** The app binary (`target/debug/...`, override `TRG_UITEST_BIN`) is started
directly with `env … "$bin" >>app.log 2>&1 &`; its pid goes to `env/app.pid`. `start_app` waits
up to 20 s for a main window (via the window list) and dies with the log tail if the process
exits. `uitest app` restarts only the app after a rebuild (backend state kept). `uitest stop`
kills app and daemon (TERM, then KILL after 5 s). **Everything targets the pid in app.pid,
never the app name**, so the user's own instance is never touched.

**Windows and screenshots.** `trgui windows --pid P` uses `CGWindowListCopyWindowInfo` and
prints `id pid layer x,y wxh(points) scale owner title`. The main window = biggest layer-0
window. `uitest shot NAME --win W` runs `screencapture -x -o -l W env/shots/NAME.png` (one
window, no shadow, bounds include the 28 pt title bar); without `--win` it captures the union
rectangle of all the app's windows with `screencapture -x -R x,y,w,h` (so popovers and menus,
which are separate windows, are included). It prints the PNG path and pixel size.
`wait-window TITLE TIMEOUT` polls for a window. The skill tells agents to downscale shots to
point size before looking (`sips -Z <width> shot.png --out small.png`) so image coordinates
equal click coordinates.

**Driving.** `trgui.swift` posts Quartz `CGEvent`s to `.cghidEventTap` from a `.hidSystemState`
source: mouse move/click (with click count and modifier flags), right click, drag (interpolated
`leftMouseDragged` steps), scroll wheel (lines), key combos (`cmd+shift+a`, `return`, `escape`,
arrows, F-keys) using US ANSI virtual key codes and explicit modifier key down/up, and `type`
(each character with its US key code plus `keyboardSetUnicodeString`, because widgets'
key handlers read the key code). Coordinates are **window-relative points** (`--win W`; default
the main window), or `--px` = screenshot pixels divided by the display's backing scale (computed
per display from `NSScreen`). `frontmost --pid` activates the app (input goes to whatever is
under the pointer). `ax --pid [--depth]` dumps the AX tree (role, title, desc, value, label,
AXIdentifier, frame); `menu --pid TITLE…` walks `kAXMenuBarAttribute` and performs
`kAXPressAction` on a menu item. `check` reports Accessibility (`AXIsProcessTrusted`), Screen
Recording (`CGPreflightScreenCaptureAccess`) and whether the screen is locked / on console
(`CGSessionCopyCurrentDictionary`), exit status 0 only if all good. The binary is built with
`swiftc -O` into `target/ui-test/bin/trgui` whenever the source is newer.

**Checking results.** Tests are plain bash (`example-scenario.sh`): `set -euo pipefail`, a
`fail()` that stops the environment and exits 1, fixed coordinates for the fixed window size,
`sleep`s sized to the app's update interval plus a round trip (1.5–2.5 s), screenshots at each
step for a human/agent to look at, and **assertions against the backend state** (via
`uitest remote …`, i.e. `transmission-remote` on the test daemon) rather than pixels, plus
log assertions (the app logs every RPC request/response to stderr → `app.log` when
`TRG_CLIENT_DEBUG=1`, which `uitest` sets by default; "connected" is detected by grepping the
log). It ends with `PASS (screenshots in …)`.

**Offscreen snapshots.** With the `debug-snapshot` feature and `TRG_DEBUG_SNAPSHOT=<dir>` (always
passed by `uitest`), the app polls `<dir>/request` every 500 ms; each line is an action name
(+ optional parameter) activated on the main window; the file is removed; after 300 ms every
window and popover is rendered with the app's own renderer to `<n>-<title>[-popover-<m>].png`
and the list written last to `index.txt`. `uitest snap ACTION…` writes the request atomically
(tmp + mv) and waits up to 5 s for `index.txt`. Works while the screen is locked or without
Screen Recording permission, but only reaches what actions can reach.
`TRG_DEBUG_SNAPSHOT_INTERVAL` also renders periodically.

**Agent instructions** (SKILL.md): start/stop sequence, always `stop` when done; the scratch
dir layout; the isolation rule; command cheat sheet; coordinate conventions; "verify effects on
the backend rather than only in pixels"; wait times; log grepping; inspector use; offscreen
mode; and pitfalls: permissions belong to the terminal process, a locked screen gives black
shots (use `snap`), **the user is at the machine** (clicks move their pointer, `front` steals
focus — keep sequences short), multiple displays, US key codes, the AX tree being useless for
GTK widgets on macOS (so coordinates from screenshots), window position from macOS cascading,
layout-dependent coordinates, Finder drag-and-drop not covered.

### Mapping to the EP macOS/SwiftUI app

| Part | Transfers? | EP equivalent |
|---|---|---|
| `trgui.swift` (CGEvent input, window list, scale, `check`, `ax`, `menu`) | **Directly**, almost verbatim (it is already macOS Swift) | Copy into e.g. `app/scripts/ui-test/epgui.swift`. |
| AX tree usage | **Better on macOS/SwiftUI** | SwiftUI exposes every control to AX. Add `find`/`press` commands (the Linux harness's equivalents) that locate elements by `AXIdentifier` (set with `.accessibilityIdentifier(...)` on key views: columns, rows, tabs, player controls, note editor, import field) and press/click their frame centre or perform `AXPress`/`AXSetValue`. Coordinates become a fallback; tests survive layout changes. |
| `screencapture -o -l` / `-R` | **Directly** | Same. Sheets, popovers and menus are separate CG windows on macOS too, so the union shot still matters. |
| `uitest` bash front end, pid files, scratch dir, `wait-window`, `shot`, `front`, `log`, `status` | **Directly** (rename prefix, e.g. `EP_UITEST_*`) | Scratch dir under the app's build dir (gitignored). |
| Private backend + fixtures | **Needs replacement** | Instead of `transmission-daemon`: start the EP Python server from `server/` on a private port with a scratch database (Postgres schema/DB created and dropped by the harness) and a scratch library directory; seed fixtures deterministically through the server API or a server-side fixture/seed command (to be agreed with the backend — it should exist for this purpose rather than the harness reaching into the DB). Fixtures should cover every design state: artists with/without releases, radio-only artist, album with all five playability states plus a missing file, a multi-disc album, a single-file album with ranges, a radio show/episode with a tracklist containing exact and estimated times, notes with links and a broken link, tags, an import job awaiting review and a failed job. Generate short audio files locally (e.g. `afconvert`/`ffmpeg` sine tones) — no network. |
| "Verify on the backend" (`uitest remote …`) | **Concept transfers** | `uitest api GET /…` wrapper around `curl` against the test server, to assert that edits, tags, notes and import decisions reached the backend. |
| `XDG_*` isolation | **Needs replacement** | A SwiftUI app keeps state in UserDefaults, Application Support and saved window state. Options: launch with an env var (e.g. `EP_SERVER_URL`, `EP_DATA_DIR`) the app honours in debug builds; override defaults via the argument domain (`-serverURL http://127.0.0.1:PORT`); disable state restoration (`-ApplePersistenceIgnoreState YES`); fix the window size with a launch argument; and/or use a separate bundle identifier for the test build so its defaults domain and caches never collide with the user's copy. |
| Launching the binary | **Needs adaptation** | Build with `xcodebuild` (or SwiftPM) into the build dir, and run `EP.app/Contents/MacOS/EP` directly (not `open`) to keep the pid and capture stderr into `app.log`. A real `.app` bundle is needed for normal activation, menus and a bundle id. |
| RPC log (`TRG_CLIENT_DEBUG`) | **Concept transfers** | Debug-build env var (e.g. `EP_CLIENT_DEBUG=1`) that logs every HTTP request/response line to stderr. |
| Offscreen snapshots (`debug-snapshot`, request file) | **Needs a macOS replacement** | In debug builds, with `EP_DEBUG_SNAPSHOT=<dir>`, poll `<dir>/request` (the same file protocol transfers directly); each line names an app command (navigate to an entity, switch Library/Radio, open search/import/settings, set appearance light/dark) dispatched through the app's own command layer; then render every `NSWindow`'s content view with `bitmapImageRepForCachingDisplay(in:)` + `cacheDisplay(in:to:)` to PNG (renders AppKit-backed SwiftUI controls, unlike `ImageRenderer`) and write `index.txt` last. Lets agents get light **and** dark screenshots without Screen Recording or an unlocked screen. |
| `example-scenario.sh` | **Pattern transfers** | One EP scenario, e.g. select artist → album → track, play it, edit a note, check the note via the API, ⌘L, quit with ⌘Q, `PASS`. |
| SKILL.md | **Transfers with edits** | `.claude/skills/macos-ui-debugging/SKILL.md` in the EP repo with the same sections and pitfalls (permissions, locked screen, user at the machine, US key codes, displays). Drop the GTK-specific notes (inspector, AX tree useless, GTK warnings); add how to find elements by identifier, and that per `plans/frontend.md` the harness is used sparingly and testing with it is done by Sonnet subagents. |
| Linux container | **Not needed** | — |

Media keys: the harness has no media-key events; if needed, they are `NSEvent.otherEvent` with
subtype 8 (`NX_KEYTYPE_PLAY` etc.) posted via `CGEvent`, an addition to `trgui.swift`.
Drag-and-drop from Finder (dropping a folder or cover image) is not covered by the existing
harness either; exercise those code paths through the request-file commands instead.
