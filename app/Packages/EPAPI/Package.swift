// swift-tools-version: 6.2
// The EP API client, generated at build time by swift-openapi-generator from
// server/openapi.json (Sources/EPAPI/openapi.json is a symlink to it: the only copy),
// through the EPAPIGenerator plugin (Plugins/EPAPIGenerator/plugin.swift).
import PackageDescription

let package = Package(
    name: "EPAPI",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "EPAPI", targets: ["EPAPI"])
    ],
    dependencies: [
        // Pinned: EPAPIGenerator lists the files this version generates.
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.14.4"),
        .package(url: "https://github.com/apple/swift-openapi-runtime", from: "1.13.4"),
        .package(url: "https://github.com/apple/swift-openapi-urlsession", from: "1.4.0"),
    ],
    targets: [
        .target(
            name: "EPAPI",
            dependencies: [
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
                .product(name: "OpenAPIURLSession", package: "swift-openapi-urlsession"),
            ],
            plugins: ["EPAPIGenerator"]
        ),
        .executableTarget(name: "EPOpenAPINormalize"),
        .plugin(
            name: "EPAPIGenerator",
            capability: .buildTool(),
            dependencies: [
                "EPOpenAPINormalize",
                .product(name: "swift-openapi-generator", package: "swift-openapi-generator"),
            ]
        ),
    ],
    swiftLanguageModes: [.v6]
)
