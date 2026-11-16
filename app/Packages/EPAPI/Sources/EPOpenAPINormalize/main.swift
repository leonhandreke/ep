// Rewrites the server's OpenAPI document into the shape swift-openapi-generator supports,
// before generation (run by the EPAPIGenerator build plugin; never committed).
//
// The server writes a nullable value as JSON Schema `anyOf: [<schema>, {"type": "null"}]`
// (standard OpenAPI 3.1, what Pydantic emits). swift-openapi-generator 1.x does not
// support the `null` schema type and silently drops every such property and parameter.
// This tool replaces each `anyOf`/`oneOf` that contains `{"type": "null"}` by the
// remaining schema and removes the property from its object's `required` list, so the
// generated Swift property is an Optional (`null` and absent both decode to `nil`).
// Sending an explicit `null` in a request is `ExplicitNulls` (Sources/EPAPI).
//
// Usage: EPOpenAPINormalize <input.json> <output.json>

import Foundation

let arguments = CommandLine.arguments
guard arguments.count == 3 else {
    FileHandle.standardError.write(Data("usage: EPOpenAPINormalize <input.json> <output.json>\n".utf8))
    exit(2)
}

/// Returns the schema without null alternatives, and whether it was nullable.
func stripNull(_ schema: [String: Any]) -> (schema: [String: Any], nullable: Bool) {
    for key in ["anyOf", "oneOf"] {
        guard let alternatives = schema[key] as? [[String: Any]] else { continue }
        let rest = alternatives.filter { ($0["type"] as? String) != "null" || $0.count != 1 }
        guard rest.count < alternatives.count else { continue }
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

do {
    let input = try Data(contentsOf: URL(filePath: arguments[1]))
    let document = normalize(try JSONSerialization.jsonObject(with: input))
    let output = try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
    try output.write(to: URL(filePath: arguments[2]), options: .atomic)
} catch {
    FileHandle.standardError.write(Data("EPOpenAPINormalize: \(error)\n".utf8))
    exit(1)
}
