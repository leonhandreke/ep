#!/usr/bin/env bash
# Builds and runs EP.app in the foreground against a server (default: the saved
# setting, else http://localhost:8000). With a URL, the address is set for this run
# only (EP_SERVER_URL) and the user's settings are not changed. Ctrl-C quits.
#   app/scripts/run.sh [http://127.0.0.1:18000]
source "$(dirname "$0")/common.sh"
"$(dirname "$0")/build.sh" >/dev/null
if [[ $# -gt 0 ]]; then export EP_SERVER_URL="$1"; fi
# The executable directly (not `open`), so the pid and stderr stay with this shell.
exec "$APP_PATH/Contents/MacOS/EP" -ApplePersistenceIgnoreState YES
