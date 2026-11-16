// swift-tools-version: 6.2
// The EP API client. Sources/EPAPI/Generated is swift-openapi-generator's output for
// server/openapi.json, generated ahead of time and checked in (app/scripts/generate-api.sh,
// tools in ../EPAPIGenerator): building the app never builds or runs the generator.
import PackageDescription

let package = Package(
    name: "EPAPI",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "EPAPI", targets: ["EPAPI"])
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-runtime", from: "1.13.4"),
        .package(url: "https://github.com/apple/swift-openapi-urlsession", from: "1.4.0"),
    ],
    targets: [
        .target(
            name: "EPAPI",
            dependencies: [
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
                .product(name: "OpenAPIURLSession", package: "swift-openapi-urlsession"),
            ]
        )
    ],
    swiftLanguageModes: [.v6]
)
