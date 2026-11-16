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
  Connection/         ServerConnection (app-wide API client + connected/unreachable), banner
  Design/             Tokens.swift (all colours, fonts, sizes), ColumnGrid layout
  Settings/           AppSettings (UserDefaults + launch overrides), Settings window
  Shell/              main window frame: toolbar, list columns, detail panels, player bar
  Info.plist          only what build settings cannot say (App Transport Security)
EPTests/              unit tests (Swift Testing) against StubTransport
EPUITests/            XCUITest (the stage 2 harness builds here)
Packages/EPAPI/       the API client (local Swift package; Generated/ is checked in)
Packages/EPAPIGenerator/  tools that generate it (not part of the app build)
scripts/              build, run, test, generate-api wrappers
```

Later features go in `EP/<Feature>/` (`Library/`, `Player/`, `NoteEditor/`, `Radio/`,
`Search/`, `Import/`, …), each with its `@Observable` store(s) and its views. No extra
layers: views read stores from the environment, stores call the generated client.

## Build, run, test (from the repo root)

```sh
app/scripts/build.sh                          # Debug build → app/build/DerivedData/…/EP.app
app/scripts/run.sh http://127.0.0.1:18000     # run against a server (for this run only)
app/scripts/run.sh                            # run against the saved address (default localhost:8000)
app/scripts/test.sh                           # unit tests
app/scripts/test.sh --ui                      # UI tests (needs UI-automation permission)
app/scripts/test.sh -only-testing:EPTests/AppSettingsTests   # extra arguments go to xcodebuild
app/scripts/generate-api.sh                   # after server/openapi.json changed
```

Always build through the scripts: they share one derived-data folder
(`app/build/DerivedData`), so the packages and the API client (~39k generated lines) are
compiled once and every later build only recompiles what changed. A clean build takes
about 30 s on an idle machine, a no-change build a few seconds. Do not delete
`app/build` or pass `clean` without a reason.

A private seeded backend to run against: `server/README.md`, "Demo data". Never point
the app at the dev database for experiments.

## Server address and isolation

`AppSettings.serverURL` (UserDefaults key `serverURL`, default `http://localhost:8000`).
For one run, `EP_SERVER_URL=<url>` or the launch argument `-serverURL <url>` overrides
it; an overridden address is never saved. `-ApplePersistenceIgnoreState YES` keeps
window restoration out of the way.

Debug builds (everything the scripts, tests and the harness build) are a separate app,
`de.ndreke.ep.debug`; Release builds are `de.ndreke.ep`. So everything a test or agent
run stores (window frames, scene storage, settings, saved state) lives in the
`de.ndreke.ep.debug` domain and never touches the real app's preferences. A run that
needs fresh state clears that domain first (`defaults delete de.ndreke.ep.debug`), never
`de.ndreke.ep`. As the unit tests' host the app does not connect to any server. App Transport Security allows plain HTTP only to
local hosts (`NSAllowsLocalNetworking`: localhost, IP addresses, `.local`, unqualified
names).

## The API client

- Generated ahead of time from `server/openapi.json` into
  `Packages/EPAPI/Sources/EPAPI/Generated/` and checked in, so the app build never builds
  or runs the generator. When the server's document changes, run
  `app/scripts/generate-api.sh` and commit the result together with the Swift changes
  it needs; `OpenAPIDocumentTests` (EPTests) fails while the client is stale. Never edit
  the generated files.
- swift-openapi-generator 1.x supports nullability only as `type: [X, "null"]` and leaves
  out every property or parameter written `anyOf: [X, {"type": "null"}]` (how the server
  writes nullable values, with a generation warning). The script therefore runs
  `EPOpenAPINormalize` (Packages/EPAPIGenerator) first, which turns them into optional
  properties, and fails on any generator warning. This is a Swift-side concern: the
  server's document stays as it is.
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
- **Accessibility**: every interactive control gets `.accessibilityIdentifier` and a
  label (the design's aria-label text where it gives one). Identifiers are
  `area.element[.detail]` in lowerCamelCase segments: `toolbar.back`, `toolbar.section`,
  `artists.column`, `detail.artist.collapse`, `connection.retry`, `settings.serverURL`.
  Rows: `area.row.<id>` with the entity ID. Areas so far: `toolbar`, `connection`,
  `settings`, `player`, `detail` (`detail.<artist|album|track|episode>`), and one per
  list column (`artists`, `releaseGroups`, `tracks`, `shows`, `episodes`, `tracklist`;
  the column itself is `<area>.column`). Containers a test needs to find (columns,
  panels, banner, player bar) use `.accessibilityElement(children: .contain)` plus an
  identifier, so their controls stay reachable. Identifiers are API for the harness; do
  not rename them casually.
- **State**: `@Observable` stores, injected with `.environment(...)`; per-window UI
  state in `@SceneStorage`; menu commands reach the focused window via `@FocusedValue`.
- **Tests**: Swift Testing; happy path and the most important failures only.
- Formatting: `swift format` (toolchain) with `.swift-format` in this directory.
