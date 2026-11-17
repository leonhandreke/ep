// Rewrites the server's OpenAPI document into the shape swift-openapi-generator supports,
// before generation (run by app/scripts/generate-api.sh; the output is never committed).
//
// The server writes a nullable value as JSON Schema `anyOf: [<schema>, {"type": "null"}]`
// (standard OpenAPI 3.1, what Pydantic emits). swift-openapi-generator 1.x supports
// nullability only as `type: [<type>, "null"]`; for an `anyOf`/`oneOf` with a
// `{"type": "null"}` member it warns "Schema "null" is not supported" and leaves the
// property or parameter out of the generated code. This tool replaces each such
// `anyOf`/`oneOf` by the remaining schema (a `$ref` stays a bare `$ref` plus the
// description; other keys such as `title` and a non-null `default` are kept, a `null`
// default dropped) and removes the property from its object's `required` list, so the
// generated Swift property is an Optional: `null` and absent both decode to `nil`.
//
// - Requests: a `nil` property is left out of the encoded body. The server guarantees
//   request bodies have no required nullable fields, so leaving it out is always valid;
//   sending an explicit `null` (clearing a field in a PATCH) is `withExplicitNulls`.
// - Nullable array items or map values (none today) would lose their nullability: a
//   `null` element then fails to decode, loudly.
// - Afterwards no `{"type": "null"}` schema may remain; the script also fails on any
//   generator warning, so a new unsupported shape never disappears silently.
//
// Open enums. The generator's enums are closed: one unknown value fails decoding of the
// whole response, so a newer server with a new issue kind or job state would break the
// import queue, while the app must show unknown issue kinds generically. For the schemas
// in `openEnums` this tool applies the generator's documented open-enum pattern
// ("Useful OpenAPI patterns"): the closed enum moves to `Known<Name>`, and `<Name>`
// becomes `anyOf: [{$ref: Known<Name>}, {type: string}]`, a struct with `value1` (the
// known case, nil if unknown) and `value2` (the raw string). `OpenEnum.swift` in EPAPI
// gives these structs `known`, `rawValue`, `init(_ known:)` and `==` with a known case.
// Parameters (`paths`) keep the closed `Known<Name>`: the client only sends known values.
//
// Usage: EPOpenAPINormalize <input.json> <output.json>

import Foundation

let arguments = CommandLine.arguments
guard arguments.count == 3 else {
    FileHandle.standardError.write(Data("usage: EPOpenAPINormalize <input.json> <output.json>\n".utf8))
    exit(2)
}

func isNullSchema(_ value: Any) -> Bool {
    guard let schema = value as? [String: Any] else { return false }
    return schema.count == 1 && (schema["type"] as? String) == "null"
}

/// Returns the schema without null alternatives, and whether it was nullable.
func stripNull(_ schema: [String: Any]) -> (schema: [String: Any], nullable: Bool) {
    for key in ["anyOf", "oneOf"] {
        guard let alternatives = schema[key] as? [[String: Any]] else { continue }
        let rest = alternatives.filter { !isNullSchema($0) }
        guard rest.count < alternatives.count, !rest.isEmpty else { continue }
        var result = schema
        result[key] = nil
        if result["default"] is NSNull { result["default"] = nil }
        if rest.count == 1 {
            let only = rest[0]
            if only["$ref"] != nil {
                // A reference keeps only its description (OpenAPI 3.1 allows it there).
                var reference = only
                if let description = schema["description"] { reference["description"] = description }
                return (reference, true)
            }
            result.merge(only) { _, inner in inner }
        } else {
            result[key] = rest
        }
        return (result, true)
    }
    return (schema, false)
}

func normalize(_ value: Any) -> Any {
    if let array = value as? [Any] { return array.map(normalize) }
    guard var object = value as? [String: Any] else { return value }
    object = stripNull(object).schema
    if var properties = object["properties"] as? [String: Any] {
        var nullable: Set<String> = []
        for (name, property) in properties {
            guard let property = property as? [String: Any] else { continue }
            let stripped = stripNull(property)
            if stripped.nullable { nullable.insert(name) }
            properties[name] = stripped.schema
        }
        object["properties"] = properties
        if let required = object["required"] as? [String] {
            let kept = required.filter { !nullable.contains($0) }
            object["required"] = kept.isEmpty ? nil : kept
        }
    }
    for (key, child) in object { object[key] = normalize(child) }
    return object
}

/// JSON pointers of every `{"type": "null"}` schema left in the document.
func remainingNullSchemas(_ value: Any, at path: String = "#") -> [String] {
    if isNullSchema(value) { return [path] }
    if let array = value as? [Any] {
        return array.enumerated().flatMap { remainingNullSchemas($1, at: "\(path)/\($0)") }
    }
    guard let object = value as? [String: Any] else { return [] }
    return object.keys.sorted().flatMap { remainingNullSchemas(object[$0]!, at: "\(path)/\($0)") }
}

/// Enums a newer server may extend; see "Open enums" above.
let openEnums = ["IssueKind", "JobState", "Choice"]

/// Replaces `$ref`s to the open enums by `$ref`s to their closed `Known<Name>`.
func referToKnown(_ value: Any) -> Any {
    if let array = value as? [Any] { return array.map(referToKnown) }
    guard var object = value as? [String: Any] else { return value }
    for name in openEnums where (object["$ref"] as? String) == "#/components/schemas/\(name)" {
        object["$ref"] = "#/components/schemas/Known\(name)"
    }
    for (key, child) in object { object[key] = referToKnown(child) }
    return object
}

func openingEnums(_ document: Any) throws -> Any {
    guard var document = document as? [String: Any],
        var components = document["components"] as? [String: Any],
        var schemas = components["schemas"] as? [String: Any]
    else { throw CocoaError(.fileReadCorruptFile) }
    for name in openEnums {
        guard let closed = schemas[name] as? [String: Any], closed["enum"] != nil, schemas["Known\(name)"] == nil
        else {
            FileHandle.standardError.write(Data("EPOpenAPINormalize: \(name) is not a closed enum schema\n".utf8))
            exit(1)
        }
        var known = closed
        known["title"] = "Known\(name)"
        schemas["Known\(name)"] = known
        var open: [String: Any] = [
            "anyOf": [["$ref": "#/components/schemas/Known\(name)"], ["type": "string"]]
        ]
        open["description"] = closed["description"]
        schemas[name] = open
    }
    components["schemas"] = schemas
    document["components"] = components
    if let paths = document["paths"] { document["paths"] = referToKnown(paths) }
    return document
}

do {
    let input = try Data(contentsOf: URL(filePath: arguments[1]))
    let document = try openingEnums(normalize(try JSONSerialization.jsonObject(with: input)))
    let remaining = remainingNullSchemas(document)
    guard remaining.isEmpty else {
        FileHandle.standardError.write(
            Data("EPOpenAPINormalize: unsupported null schemas at:\n\(remaining.joined(separator: "\n"))\n".utf8)
        )
        exit(1)
    }
    let output = try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
    try output.write(to: URL(filePath: arguments[2]), options: .atomic)
} catch {
    FileHandle.standardError.write(Data("EPOpenAPINormalize: \(error)\n".utf8))
    exit(1)
}
