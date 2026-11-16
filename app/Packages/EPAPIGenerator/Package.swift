// swift-tools-version: 6.2
// Tools that generate the EPAPI client from server/openapi.json; run by
// app/scripts/generate-api.sh only, never part of the app build.
import PackageDescription

let package = Package(
    name: "EPAPIGenerator",
    platforms: [.macOS(.v15)],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.14.4")
    ],
    targets: [
        .executableTarget(name: "EPOpenAPINormalize")
    ],
    swiftLanguageModes: [.v6]
)
