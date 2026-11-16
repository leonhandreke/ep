# Shared by the scripts in this directory (sourced, not run).
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# One fixed derived-data folder for every script, so packages and the API client are
# compiled once and later builds are incremental. Override only for a throwaway build.
DERIVED_DATA="${EP_DERIVED_DATA:-$APP_DIR/build/DerivedData}"
APP_PATH="$DERIVED_DATA/Build/Products/Debug/EP.app"
XCODEBUILD=(xcodebuild -project "$APP_DIR/EP.xcodeproj" -scheme EP -configuration Debug
    -destination "platform=macOS,arch=$(uname -m)" -derivedDataPath "$DERIVED_DATA")
