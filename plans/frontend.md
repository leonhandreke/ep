# EP — macOS app plan (stub; to be written after the backend stages finish)

## Instructions from the user (2026-11-14)

- Start after backend stages 4c–8 are done.
- First read the Claude Design result:
  https://claude.ai/artifact/C6tVMAHQfLeTh7ZF86c1Nf#page-dcb81aee8d83
  (read it with the Artifact tool, in a subagent, and distil it into this plan).
- Then write the full plan here; every subagent gets it as context.
- Build in stages with Opus 5.5 subagents (medium effort), one after another, each
  followed by a separate reviewer subagent. Commit at reasonable intervals.
- One of the first stages: a harness to take screenshots of the app and test it.
  Look at `~/Development/transmission-remote-gtk` for an existing one to model it on.
  Use the harness very sparingly. Any testing with it is done by Sonnet subagents.
- Use the latest versions and current conventions; no backward-compat.
- Inspect each subagent's output for ways the backend could be better suited to this
  frontend. When that comes up, launch a subagent to fix the backend. Neither side
  works around shortcomings of the other; they should work in harmony.
- The app lives alongside `server/` in its own directory.
