// Generates the EPAPI client at build time: normalises the target's `openapi.json` (a
// symlink to server/openapi.json, the only copy) with EPOpenAPINormalize, then runs
// swift-openapi-generator on the result with the target's
// `openapi-generator-config.yaml`. Like swift-openapi-generator's own build plugin,
// which cannot be used directly because it reads the document unmodified (see
// Sources/EPOpenAPINormalize/main.swift for why the document needs normalising).

import Foundation
import PackagePlugin

@main
struct EPAPIGenerator: BuildToolPlugin {
    /// The files swift-openapi-generator writes for every mode
    /// (`OutputFileName` in its `_OpenAPIGeneratorCore`); the version is pinned
    /// in Package.swift, so a change here shows up as a build error.
    static let outputFiles = [
        "Types.swift",
        "Types+Components.swift",
        "Types+Operations.swift",
        "Types+Components+Schemas.swift",
        "Types+Components+Parameters.swift",
        "Types+Components+RequestBodies.swift",
        "Types+Components+Responses.swift",
        "Types+Components+Headers.swift",
        "Client.swift",
        "Server.swift",
    ]

    func createBuildCommands(context: PluginContext, target: Target) async throws -> [Command] {
        guard let target = target as? SwiftSourceModuleTarget else { return [] }
        func input(_ name: String) throws -> URL {
            guard let file = target.sourceFiles.first(where: { $0.url.lastPathComponent == name }) else {
                throw PluginError.missing(name, target: target.name)
            }
            return file.url
        }
        let document = try input("openapi.json")
        let config = try input("openapi-generator-config.yaml")

        let work = context.pluginWorkDirectoryURL
        let normalized = work.appending(component: "openapi.json")
        let generated = work.appending(component: "GeneratedSources")

        return [
            .buildCommand(
                displayName: "Normalising OpenAPI document for swift-openapi-generator",
                executable: try context.tool(named: "EPOpenAPINormalize").url,
                arguments: [document.path(percentEncoded: false), normalized.path(percentEncoded: false)],
                inputFiles: [document],
                outputFiles: [normalized]
            ),
            .buildCommand(
                displayName: "Running swift-openapi-generator",
                executable: try context.tool(named: "swift-openapi-generator").url,
                arguments: [
                    "generate", normalized.path(percentEncoded: false),
                    "--config", config.path(percentEncoded: false),
                    "--output-directory", generated.path(percentEncoded: false),
                    "--plugin-source", "build",
                ],
                inputFiles: [normalized, config],
                outputFiles: Self.outputFiles.map { generated.appending(component: $0) }
            ),
        ]
    }
}

enum PluginError: Error, CustomStringConvertible {
    case missing(String, target: String)

    var description: String {
        switch self {
        case .missing(let name, let target): "Target \(target) has no \(name)."
        }
    }
}
