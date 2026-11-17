#!/usr/bin/env bash
# Runs the unit tests (Swift Testing, EPTests). Extra arguments go to xcodebuild.
# UI tests and screenshots: app/scripts/ui-test/uitest (see its README).
#   app/scripts/test.sh
#   app/scripts/test.sh -only-testing:EPTests/AppSettingsTests
source "$(dirname "$0")/common.sh"
# A test that waits for ever must not hold the run: each test also has a time limit
# (Swift Testing `.timeLimit` on the suites), and Xcode stops the host after 10 minutes.
"${XCODEBUILD[@]}" -quiet -enableCodeCoverage NO \
    -test-timeouts-enabled YES -default-test-execution-time-allowance 120 \
    -maximum-test-execution-time-allowance 300 \
    test -only-testing:EPTests "$@"
