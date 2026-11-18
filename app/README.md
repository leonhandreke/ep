# EP — macOS app

SwiftUI client for the EP server (`../server`). Plan: [`../plans/frontend.md`](../plans/frontend.md);
design: [`../plans/frontend-design.md`](../plans/frontend-design.md).

Xcode 26.3, Swift 6.2, macOS 26.2 SDK, deployment target macOS 15. Swift 6 language
mode, strict concurrency, main-actor isolation by default and approachable concurrency
in the app and unit-test targets.

## Layout

```
EP.xcodeproj          hand-written; buildable folders, so new files need no project edits
EP/                   the app target
  App/                EPApp (scenes, menu commands, focused values)
  Connection/         ServerConnection (app-wide API client + connected/unreachable, retries
                      by itself while unreachable: 5, 10, 20, then 30 s), the banner
  Design/             Tokens.swift (all colours, fonts, sizes), ColumnGrid layout
  Settings/           AppSettings (UserDefaults + launch overrides), Settings window,
                      WatchFolders (/watch-dirs) and the "Watch a folder…" panel
  Shell/              main window frame: toolbar, list columns, detail panels, player bar
  Library/            artists / albums / tracks columns: ArtistList and LibraryBrowser
                      (per window: selection, history, navigate(to:)), CoverImages, the
                      sources popover (SourcesStore)
  Details/            detail panels: EntityDetails (one entity's note, tags, backlinks, radio
                      appearances from GET /entity-details), the Note / Tags / Linked from
                      sections (the Radio view hosts them in tabs), tag suggestions,
                      FloatingPopup (suggestion lists over the window), OpenEntityAction
  NoteEditor/         the note editor: NoteLinks, NoteStyling, LinkQuery (pure, UTF-16
                      NSRange), NoteDocument (autosave, 409), LinkAutocomplete, NoteTextView
                      (TextKit 2) and its SwiftUI wrapper
  Radio/              RadioBrowser (per window: shows, episodes, tracklist, history,
                      navigate(to:), re-pointing entries), the three columns, the tabbed
                      panel, the re-point sheet
  Search/             SearchStore (debounced, grouped, tag filter) and the ⌘K palette
  Editing/            edit forms (PATCH bodies) and their stores, the edit sheets, credits
                      as tokens, create by hand (Creator), MusicBrainz search; WindowSheet
  Import/             ImportStore (one for the app: summary polling while jobs run, jobs,
                      logs, actions, toast, library reloads), ImportDraft (link kind via
                      /links/classify, folder by host path), ReviewModel (issues as
                      questions, defaults, keys, answers); the import and review sheets,
                      toolbar button, toast, ⌘V monitor
  Player/             Player (one for the app: item, queue, transport), PlayItem (spans,
                      long-file entries), player bar, Now Playing, Space key
    Audio/            engines: AVPlayer, and AVAudioEngine fed by decoders for Ogg
                      (AudioToolbox) and WebM/Opus (own Matroska demuxer), over HTTP Range
  Info.plist          only what build settings cannot say (App Transport Security)
EPTests/              unit tests (Swift Testing) against StubTransport
EPUITests/            XCUITest: harness support (Support/), screenshot Scenarios, flow tests
Packages/EPAPI/       the API client (local Swift package; Generated/ is checked in)
Packages/EPAPIGenerator/  tools that generate it (not part of the app build)
scripts/              build, run, test, generate-api wrappers; ui-test/uitest (the harness)
```

Later features go in `EP/<Feature>/`, each with its `@Observable` store(s) and its views. No extra
layers: views read stores from the environment, stores call the generated client.

## Build, run, test (from the repo root)

```sh
app/scripts/build.sh                          # Debug build → app/build/DerivedData/…/EP.app
app/scripts/run.sh http://127.0.0.1:18000     # run against a server (for this run only)
app/scripts/run.sh                            # run against the saved address (default localhost:8000)
app/scripts/test.sh                           # unit tests
app/scripts/test.sh -only-testing:EPTests/AppSettingsTests   # extra arguments go to xcodebuild
app/scripts/generate-api.sh                   # after server/openapi.json changed
```

Always build through the scripts: they share one derived-data folder
(`app/build/DerivedData`), so the packages and the API client (~39k generated lines) are
compiled once and every later build only recompiles what changed. A clean build takes
about 30 s on an idle machine, a no-change build a few seconds. Do not delete
`app/build` or pass `clean` without a reason.

UI tests and screenshots go through the harness, `app/scripts/ui-test/uitest`: a private
seeded backend, named screenshot scenarios and XCUITest flow tests. Use it sparingly and
read [`scripts/ui-test/README.md`](scripts/ui-test/README.md) first. Never point the app at
the dev database for experiments.

## Server address and isolation

`AppSettings.serverURL` (UserDefaults key `serverURL`, default `http://localhost:8000`).
For one run, `EP_SERVER_URL=<url>` or the launch argument `-serverURL <url>` overrides
it; an overridden address is never saved. `-ApplePersistenceIgnoreState YES` keeps
window restoration out of the way. Two more launch arguments exist for deterministic
screenshots (`EP/App/LaunchOptions.swift`): `-windowSize 1280x800` and
`-appearance light|dark`.

Debug builds (everything the scripts and unit tests build) are a separate app,
`de.ndreke.ep.debug`; Release builds are `de.ndreke.ep`. So everything a test or agent
run stores (window frames, scene storage, settings, saved state) lives in the
`de.ndreke.ep.debug` domain and never touches the real app's preferences. A run that
needs fresh state clears that domain first (`defaults delete de.ndreke.ep.debug`), never
`de.ndreke.ep`. The harness runs its own copy of the Debug app with bundle id
`de.ndreke.ep.uitest`, so it touches neither domain. As the unit tests' host the app does not connect to any server. App Transport Security allows plain HTTP only to
local hosts (`NSAllowsLocalNetworking`: localhost, IP addresses, `.local`, unqualified
names).

## The API client

- Generated ahead of time from `server/openapi.json` into
  `Packages/EPAPI/Sources/EPAPI/Generated/` and checked in, so the app build never builds
  or runs the generator. When the server's document changes, run
  `app/scripts/generate-api.sh` and commit the result together with the Swift changes
  it needs; `OpenAPIDocumentTests` (EPTests) fails while the client is stale: when the
  document or the generator's inputs (normalizer, configuration, Package.resolved)
  changed. Never edit
  the generated files.
- swift-openapi-generator 1.x supports nullability only as `type: [X, "null"]` and leaves
  out every property or parameter written `anyOf: [X, {"type": "null"}]` (how the server
  writes nullable values, with a generation warning). The script therefore runs
  `EPOpenAPINormalize` (Packages/EPAPIGenerator) first, which turns them into optional
  properties, and fails on any generator warning. This is a Swift-side concern: the
  server's document stays as it is.
- Generated enums are closed (one unknown value fails the whole response). The schemas a
  newer server may extend (`openEnums` in `EPOpenAPINormalize`: `IssueKind`, `JobState`,
  `Choice`) are rewritten into the generator's documented open-enum pattern: the closed
  enum becomes `Known<Name>` (also what query parameters take), `<Name>` a struct with the
  known case or the raw string. `OpenEnum.swift` adds `known` (nil: unknown), `rawValue`,
  `init(_ known:)` and `==` with a known case; `switch` on `.known`, with a generic branch.
- `Client.ep(serverURL:transport:)` is the configured client (fractional-second
  timestamps, explicit nulls). The app has one, `ServerConnection.client`.
- A new call: use the generated method (`try await connection.client.listArtists(...)`,
  idiomatic names from the operation IDs), then `.ok.body.json`. No hand-written DTOs.
- Clearing a field in a PATCH (JSON `null`):
  ```swift
  try await withExplicitNulls(Components.Schemas.RecordingUpdate.CodingKeys.preferredSourceId) {
      try await client.updateRecording(path: .init(id: id), body: .json(.init()))
  }
  ```
- Tests stub the transport (`EPTests/StubTransport.swift`), never the network.

## Conventions

- **Tokens**: every colour, font, radius, row height and size comes from
  `EP/Design/Tokens.swift` (design §2). Add a token there rather than a literal in a view.
  Selection and accent follow the system accent colour.
- **Layout**: list columns, detail panels and the player bar use `ColumnGrid` with a
  `ColumnSpec` (the design's CSS `minmax(min, fr)` grids), so panels line up with columns.
- **Row alignment** (`Design/RowAlignment.swift`): a row's texts share one baseline
  (`TextRow`, an `HStack` on `.firstTextBaseline`); icons and small buttons in it use
  `centeredOnText()`; covers, two-line blocks and controls are centred on the row.
  `ColumnGrid` centres its cells and reports its first cell's baseline.
- **Accessibility**: every interactive control gets `.accessibilityIdentifier` and a
  label (the design's aria-label text where it gives one). Identifiers are
  `area.element[.detail]` in lowerCamelCase segments: `toolbar.back`, `toolbar.section`,
  `artists.column`, `detail.artist.collapse`, `connection.retry`, `settings.serverURL`.
  Rows: `area.row.<id>` with the entity ID (`artists.row.<artist>`,
  `releaseGroups.row.<release group>` (also under `releaseGroups.appearsOn`) and
  `releaseGroups.row.looseTracks`,
  `tracks.row.<track>`, or `<recording>` among loose tracks). Areas so far: `toolbar`,
  `settings` (`.serverURL`, `.retry`, `.connectionStatus`, `.serverInfo`,
  `.libraryLocation`, `.folders` with `.add`, `.remove`, `.error`, and
  `settings.folder.<id>` with `.enabled`, `.status`), `empty` (`empty.library`,
  `.import`, `.watchFolder`), `connection` (`.banner`, `.countdown`, `.retry`,
  `.settings`), `player` (`player.bar`, `.title`, `.playPause`, `.scrubber`, `.queue`,
  `.notice`, …), `detail` (`detail.<artist|album|track|episode>` with `.title`,
  `.collapse`, `.note`, `.note.editor`, `.note.status`, `.note.conflict` (`.keepMine`,
  `.useTheirs`), `.tags`, `.tag.<tag>` (`.remove`), `.addTag` (`.suggestions.row.<n>`),
  `.linkedFrom`, `.backlink.<entity>`, `.radio.<entry>` (`.play`)), `linkSuggestions`
  (`.row.<n>`, `.newArtist`, `.newOther`, `.searchMusicBrainz`), one per
  list column (`artists`, `releaseGroups`, `tracks`, `shows`, `episodes`, `tracklist`;
  the column itself is `<area>.column`, its header `<area>.header`), the Radio panel
  `detail.radio` (`.tab.<note|tags|linkedFrom>`, `.collapse`; its sections are
  `detail.episode.*` or `detail.show.*`), `search` (`.palette`, `.field`, `.tag`,
  `.group.<topHit|tracks|albums|artists|radio>`, `.row.<n>`), `sources` (`.popover`,
  `.option.<source>`, `.locate.<source>`, `.add`, `.showInFinder`), the sheets
  `edit.album` / `edit.artist` / `edit.track` (`.title`, `.name`, `.credit`, `.date`,
  `.save`, `.cancel`, `.detach`), `create`, `musicBrainz` and `repoint`; the import sheet
`import` (`.sheet`, `.link`, `.kind`, `.start`, `.chooseFolder`, `.error`, `.jobs`,
`.stages`, `.done`, `.actionError`, `.job.<job>` with `.review`, `.retry`, `.dismiss`,
`.cancel`, `.showExisting`, `.toggleLog`, `.log`, `.detail`; the toast `import.toast`
with `.open`, `.close`), the toolbar's `toolbar.import` (its label says the badge), and
the review sheet `review` (`.sheet`, `.title`, `.headline`, `.filter`, `.questions`,
`.row.<issue id>`, `.detail`, `.eyebrow`, `.question`, `.comparison`, `.options`,
`.option.<choice>:<candidate>`, `.search` with `.field` and `.row.<n>`, `.later`,
`.confirm`, `.error`). Containers a test needs to find (columns,
  panels, banner, player bar) use `.accessibilityElement(children: .contain)` plus an
  identifier, so their controls stay reachable. Identifiers are API for the harness; do
  not rename them casually.
- **State**: `@Observable` stores, injected with `.environment(...)`; per-window UI
  state in `@SceneStorage`; menu commands reach the focused window via `@FocusedValue`.
  Per-window stores (`ArtistList`, `LibraryBrowser`) are created by `MainWindow`.
- **Opening things**: note links, backlinks, radio appearances, tracklist links and
  search results call the window's `OpenEntityAction` (environment `openEntity`):
  Library entities through `LibraryBrowser.navigate(to:)`, radio shows, episodes and
  entries through `RadioBrowser.navigate(to:)`, or a new main window (⌥⌘-click;
  `WindowGroup(for: WindowTarget.self)`). A tag click calls the environment's `showTag`:
  the search palette filtered to the tag (decision 7). Sheets (edit, create by hand,
  MusicBrainz) go through the environment's `presentSheet` (`WindowSheet`); ⌘E edits
  the focused list's selection (`editSelection`), the track's state icon or
  "Sources…" opens the sources popover (decision 6).
- **Edits**: forms (`EditForms.swift`) compare with what was loaded and send only what
  changed; a cleared nullable field is an explicit `null` (`withExplicitNulls` with the
  form's list of keys). Audio files are never written; the window reloads after a save.
- **Notes**: the body is edited as stored (`[[type:uuid|Display]]`), styled by attributes
  only; ranges are UTF-16 (`NSRange`), as the API counts. A track's note and tags are its
  recording's. `NoteDocument` saves 1 s after the last edit with `base_updated_at`; a 409
  keeps the edit and asks (Keep Mine / Use Theirs); notes are read-only while unreachable.
- **Navigation**: each window has one history across both views (`WindowNavigation`,
  `Shell/`): an entry is the view shown plus where the Library and the Radio view are, so
  Back after following a tracklist link returns to the Radio view at that entry, and
  switching views is an entry too. Anything that shows an entity in the Library (links,
  search results, the player's title, artist and album) calls
  `LibraryBrowser.navigate(to:)`, which resolves the artist → album → track path and
  records it; radio shows, episodes and entries go through `RadioBrowser.navigate(to:)`.
  The player's title (and ⌘L) shows an episode played from the Radio view there, at the
  entry playing, and anything else in the Library. Playing goes
  through `LibraryBrowser.play(_:)` (`onPlay`, set by the player).
- **Imports**: `ImportStore` polls `GET /imports/summary` only while a job is queued or
  running (and once on connect, window activation and after a start, review or action),
  reloads `GET /imports` when the summary changes, toasts a job that reached done, failed
  or needs review, and bumps `libraryChanges` (windows reload) when one put rows into the
  library. Folders are sent as paths on this Mac; the server maps them (`EP_HOST_PATHS`)
  and refuses one outside its media roots (the sheet names the folders it reads). The
  review sheet works for any issue kind: the kinds it has words for get a question, any
  other (also one this app does not know) shows its message and its known choices. A
  job queued again after review shows the answers given (`Issue.answer`) above the log of
  its expanded row. ⇧⌘I opens the sheet for its jobs (Return reviews
  the newest job waiting); ⌘V with a link (not in a text field) opens it with the link.
- **Playback**: `Player` (in the environment of every window) plays what
  `LibraryBrowser.play(_:)` asks for and, for the Radio view, `playEpisode(_:from:)`.
  An item is a span of one file streamed from `GET /sources/{id}/stream`; a
  single-file album or an episode is one item whose entries (tracks, tracklist) give
  the marks, the title and previous/next. `AudioOutput` picks the engine from the
  file's `PlaybackRead.mediaType` (the stream's `Content-Type`), before fetching
  anything: AVPlayer for what AVFoundation plays, `DecodingEngine` for Ogg
  (Opus, Vorbis; AudioToolbox reads Ogg since macOS 15) and WebM/Opus. No third-party
  audio code. Unit tests generate Ogg/WebM fixtures with `ffmpeg` (skipped without it).
- **States**: while the server does not answer, the window shows the banner with a
  countdown to the next attempt (`ServerConnection.retryAt`) and keeps what it loaded
  at 55 % opacity; notes are read-only. A cold start without a server has no data
  (nothing is cached). A connected server whose counts are all zero shows the empty
  library (§11.1) in place of the Library's columns. Lists show a skeleton only after
  300 ms (`DelayedSkeleton`, the note's own).
- **Selection**: every list's table always holds the true selection (`shownSelection`; handing
  it none while unfocused lost clicks); the unfocused list's selected row also draws
  `--selsoft` itself (`softSelection`). An artist shown by navigation that the filter hides
  clears the filter, then shows radio-only artists.
- **Menus**: Go (Back ⌘[, Forward ⌘]), File (New Artist/Album/Recording…, Import… ⇧⌘I,
  Import Link from Clipboard — ⌘V outside text fields), Edit (Edit… ⌘E, Search… ⌘K,
  Find… ⌘F), View (Library ⌘1, Radio ⌘2, Hide/Show Detail Panels ⌥⌘D), Controls
  (Play/Pause — Space outside text fields and sheets, Next, Previous, Show Playing ⌘L),
  Settings… ⌘,. Space in the review sheet previews the incoming track.
- **Tests**: Swift Testing; happy path and the most important failures only.
- Formatting: `swift format` (toolchain) with `.swift-format` in this directory.
