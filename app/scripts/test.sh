#!/usr/bin/env bash
# Runs the unit tests (Swift Testing, EPTests). Extra arguments go to xcodebuild.
# UI tests and screenshots: app/scripts/ui-test/uitest (see its README).
#   app/scripts/test.sh
#   app/scripts/test.sh -only-testing:EPTests/AppSettingsTests
source "$(dirname "$0")/common.sh"
"${XCODEBUILD[@]}" -quiet -enableCodeCoverage NO test -only-testing:EPTests "$@"
