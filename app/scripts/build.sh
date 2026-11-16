#!/usr/bin/env bash
# Builds EP.app (Debug) into app/build/DerivedData. Extra arguments go to xcodebuild.
#   app/scripts/build.sh            # build
#   app/scripts/build.sh clean build
source "$(dirname "$0")/common.sh"
args=("$@")
[[ ${#args[@]} -eq 0 ]] && args=(build)
"${XCODEBUILD[@]}" -quiet "${args[@]}"
echo "$APP_PATH"
