public import Foundation
public import HTTPTypes
public import OpenAPIRuntime

// The server's PATCH bodies treat an omitted field as "leave alone" and an explicit
// `null` as "clear" (JSON Merge Patch). Generated request types encode `nil` by
// omitting the key, so a client cannot clear a field with them alone. This adds the
// nulls to the encoded JSON body:
//
//     try await withExplicitNulls(Components.Schemas.RecordingUpdate.CodingKeys.preferredSourceId) {
//         try await client.updateRecording(path: .init(id: id), body: .json(.init()))
//     }
//
// sends `{"preferred_source_id": null}`. Keys are top-level keys of the JSON object
// body (the PATCH fields; nested objects in request bodies are always sent whole) and
// apply to every JSON request sent inside the closure, including from child tasks. Nested
// calls add their keys to the enclosing ones. A key set to null replaces any value the
// body has for it.

/// Sends JSON `null` for the given top-level body keys in every request made in `operation`.
public func withExplicitNulls<Key: CodingKey, Result>(
    _ keys: Key...,
    isolation: isolated (any Actor)? = #isolation,
    operation: () async throws -> Result
) async throws -> Result {
    try await ExplicitNullsMiddleware.$keys.withValue(ExplicitNullsMiddleware.keys.union(keys.map(\.stringValue))) {
        try await operation()
    }
}

/// Adds the keys set by `withExplicitNulls` to JSON object request bodies.
struct ExplicitNullsMiddleware: ClientMiddleware {
    @TaskLocal static var keys: Set<String> = []

    /// Request bodies are small JSON documents; anything larger is not a PATCH body.
    static let maxBodySize = 1 << 20

    func intercept(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String,
        next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
    ) async throws -> (HTTPResponse, HTTPBody?) {
        let keys = Self.keys
        guard !keys.isEmpty, let body,
            request.headerFields[.contentType]?.hasPrefix("application/json") == true
        else {
            return try await next(request, body, baseURL)
        }
        let data = try await Data(collecting: body, upTo: Self.maxBodySize)
        guard var object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return try await next(request, HTTPBody(data), baseURL)
        }
        for key in keys { object[key] = NSNull() }
        let patched = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        var request = request
        request.headerFields[.contentLength] = String(patched.count)
        return try await next(request, HTTPBody(patched), baseURL)
    }
}
