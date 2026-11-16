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
Packages/EPAPI/       the generated API client (local Swift package)
scripts/              build, run, test wrappers
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
```

The scripts pass `-skipPackagePluginValidation` (the EPAPI package's build plugin
generates the client). Opening `EP.xcodeproj` in Xcode asks once to trust that plugin.

A private seeded backend to run against: `server/README.md`, "Demo data". Never point
the app at the dev database for experiments.

## Server address and isolation

`AppSettings.serverURL` (UserDefaults key `serverURL`, default `http://localhost:8000`).
For one run, `EP_SERVER_URL=<url>` or the launch argument `-serverURL <url>` overrides
it; an overridden address is never saved. `-ApplePersistenceIgnoreState YES` keeps
window restoration out of the way. App Transport Security allows plain HTTP only to
local hosts (`NSAllowsLocalNetworking`: localhost, IP addresses, `.local`, unqualified
names).

## The API client

- Generated at build time from `server/openapi.json`, the only copy:
  `Packages/EPAPI/Sources/EPAPI/openapi.json` is a symlink to it. Regenerate the
  document on the server side (`uv run python -m ep.openapi`); the next build picks it up.
- swift-openapi-generator 1.x drops every `anyOf: [X, {"type": "null"}]` (how the
  server writes nullable values). The package's own build plugin (`EPAPIGenerator`)
  therefore runs `EPOpenAPINormalize` first, which turns them into optional properties,
  then the generator (pinned to an exact version: the plugin lists its output files).
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
  Rows: `area.row.<id>` with the entity ID. Identifiers are API for the harness; do not
  rename them casually.
- **State**: `@Observable` stores, injected with `.environment(...)`; per-window UI
  state in `@SceneStorage`; menu commands reach the focused window via `@FocusedValue`.
- **Tests**: Swift Testing; happy path and the most important failures only.
- Formatting: `swift format` (toolchain) with `.swift-format` in this directory.
