# Shared by the scripts in this directory (sourced, not run).
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DERIVED_DATA="${EP_DERIVED_DATA:-$APP_DIR/build/DerivedData}"
APP_PATH="$DERIVED_DATA/Build/Products/Debug/EP.app"
# -skipPackagePluginValidation: the EPAPI package's own build plugin generates the API
# client; Xcode asks once to trust it, xcodebuild needs the flag.
XCODEBUILD=(xcodebuild -project "$APP_DIR/EP.xcodeproj" -scheme EP -configuration Debug
    -destination "platform=macOS,arch=$(uname -m)" -derivedDataPath "$DERIVED_DATA" -skipPackagePluginValidation)
