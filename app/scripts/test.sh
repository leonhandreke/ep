#!/usr/bin/env bash
# Runs the unit tests (Swift Testing, EPTests). With --ui, the UI tests (EPUITests)
# instead; they drive the real app and need Accessibility/automation permission.
#   app/scripts/test.sh            # unit tests
#   app/scripts/test.sh --ui       # UI tests
source "$(dirname "$0")/common.sh"
target=EPTests
if [[ "${1:-}" == "--ui" ]]; then target=EPUITests; shift; fi
"${XCODEBUILD[@]}" -quiet -enableCodeCoverage NO test -only-testing:"$target" "$@"
