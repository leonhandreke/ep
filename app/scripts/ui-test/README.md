# UI test harness

`uitest` runs the real app (XCUITest) against a private, seeded backend, to see what a
state looks like (`shot`) or to check a user flow end to end (`test`). **Light appearance
only for now.**

## When to use it (sparingly)

- Unit tests first (`app/scripts/test.sh`, Swift Testing against `StubTransport`). The
  harness is only for what they cannot show: layout and look, focus and keyboard, real
  windows and menus, a flow through several views against the real server.
- Harness runs are done by Sonnet test agents at the plan's ⟡ points. An implementer runs
  at most one smoke check (`uitest shot main`), not a test loop.
- It takes over the screen: launching the app activates it and steals focus. The user may
  be at the machine; keep runs short and few.

## Commands (from the repo root)

```sh
app/scripts/ui-test/uitest up                 # private backend (idempotent)          ~6 s
app/scripts/ui-test/uitest shot main          # one scenario → PNG path(s)            ~10 s
app/scripts/ui-test/uitest shot main radio unreachable settings                    # ~26 s
app/scripts/ui-test/uitest shot --list        # scenario names
app/scripts/ui-test/uitest test               # all flow tests, compact summary        ~13 s
app/scripts/ui-test/uitest test ConnectionFlowTests/testChangingServerAddressChangesConnection
app/scripts/ui-test/uitest api server         # GET on the private backend (JSON)
app/scripts/ui-test/uitest reset              # back to the seeded state               <1 s
app/scripts/ui-test/uitest status
app/scripts/ui-test/uitest down               # stop and clean up — always when done   <1 s
```

`shot` options: `--size 1280x800` (main window, points), `--2x` (keep Retina pixels;
default is scaled to points, so image pixels = window points), `--out DIR`, `--dark`
(exists for a one-off look; not part of the normal checks).

`shot` and `test` start the backend if needed, build for testing (incremental, shared
`app/build/DerivedData`, ~3 s when nothing changed), reset the backend if an earlier run
may have changed it, run without building, and quit the app afterwards. Times above are on
this Mac (16 GB) with a warm build.

## What it sets up

- Backend: Postgres 18 container `ep-uitest-db` on `127.0.0.1:55439` (tmpfs, no fsync),
  seeded once by `ep.seed` into the template database `ep_seed`; the API (`uvicorn`, no
  worker) serves a copy, `ep`, on `http://127.0.0.1:18439`. `reset` recreates `ep` from the
  template and restores the library folder from a clone. Ports: `EP_UITEST_DB_PORT`,
  `EP_UITEST_API_PORT`. A second API on the next port serves an empty library (database
  `ep_empty`, the schema only) for the first-launch state: `launch(server: .empty)`.
  Never the dev database, never `~/Music`. The seeded audio covers
  every engine path: FLAC, MP3, M4A (AVPlayer); Sigur Rós's Svefn-g-englar (Ogg Vorbis),
  Björk's loose "Loose Tone" (WebM/Opus) and the episode "Homage to the night shift" (Ogg
  Opus) (`DecodingEngine`); the episode "Strings only" (Cocoon's excerpt) is M4A.
- App: a copy of the Debug build, `app/build/ui-test/EP.app`, with bundle id
  `de.ndreke.ep.uitest`, so its settings and window frames never touch `de.ndreke.ep` or
  `de.ndreke.ep.debug`. Its defaults are wiped before every run, with `serverURL` saved as
  the private backend.
- State: `app/build/ui-test/` (gitignored): `shots/`, `failures/`, `last.xcresult`,
  `xcodebuild.log`, `api.log`, `seed.log`, `build.log`, `data/` (the library).

## Reading results

- `shot` prints one line per PNG: absolute path and pixel size. Look at it with the Read
  tool (it renders PNGs). Shots are the app's window only, without shadow.
- `test` prints `PASS`/`FAIL`/`SKIP` per test; a failure shows the assertion with file and
  line and `screenshot: <path>` (the app's window at the moment of failure). Exit status 1
  on any failure. More: `xcodebuild.log`, or open `last.xcresult` (it also holds a screen
  recording of failed tests).

## Adding a scenario or a flow test

Support code is in `app/EPUITests/Support/` (`EPUITestCase`, `Harness`).

- Scenario: a method `testFoo()` in `app/EPUITests/Scenarios.swift` is `uitest shot foo`.
  Launch, reach the state, `screenshot("foo")` (or `screenshot("foo", of: window)`):
  ```swift
  func testRadio() throws {
      let app = try launchConnected()
      app.element("toolbar.section").radioButtons["Radio"].click()
      waitFor(app.element("shows.column"))
      screenshot("radio")
  }
  ```
- Flow test: a new `final class …Tests: EPUITestCase` in `app/EPUITests/`. Assert on the UI
  (`waitFor`, `waitForText`, `waitForAbsence`) and, where the UI cannot show it, on the
  server: `try await Harness.get("artists/…")`. Happy path and the most important failure
  only.
- Find controls by accessibility identifier only (`app.element("area.element")`,
  `app.window(containing: "settings.serverURL")`); add identifiers in the app if missing
  (conventions in `app/README.md`). No coordinates, no sleeps: wait for an element or text.
- `launch(server:)`: `.harness` (default), `.unreachable`, `.empty` (nothing in the
  library), or `.saved` (only to test the
  address setting itself; an overridden address cannot be edited).
- Tests in one run share the backend (reset happens between runs, not between tests). A
  test that changes data must not break the others: change something only it looks at, or
  put it back.

## Troubleshooting

- *Screen locked*: `uitest` refuses to run. Screenshots and input need an unlocked, logged
  in session. A sleeping display is woken and kept awake during a run (`caffeinate -u`).
- *Window has the wrong size*: a tiling window manager decides. With AeroSpace (`uitest`
  warns), add to its config:
  `[[on-window-detected]] if.app-id = 'de.ndreke.ep.uitest' run = 'layout floating'`.
- *Build failed*: the first errors are printed; full log `app/build/ui-test/build.log`.
- *Backend did not start*: `api.log`, `seed.log`; Docker must be running. `uitest down`
  then `uitest up` clears whatever a crashed run left.
- *A test hangs*: each test is stopped after 120 s.
- *UI automation prompt*: automation permission belongs to Xcode's tools
  (`automationmodetool`); if macOS asks, the user has to allow it. Do not work around it.
