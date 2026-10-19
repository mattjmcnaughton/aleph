import Foundation

/// Rewrites the contract into the nullability form swift-openapi-generator 1.x supports.
///
/// FastAPI (OpenAPI 3.1) declares a nullable property as `anyOf: [X, {"type": "null"}]`.
/// The generator does not support the `null` schema: it warns "not supported, skipping"
/// and leaves the **whole property** out of the generated type. This rewrite runs on a
/// temp copy of `clients/openapi.json` before the generator (generate-api.sh); the
/// committed contract and the web client are unchanged.
///
/// It is deliberately narrow. On a component schema's property it handles exactly:
/// - `anyOf: [X, {"type": "null"}]`, in either order: the property becomes `X` and leaves
///   `required`, so Swift sees an Optional. A JSON `null` decodes as `nil`.
/// - a property whose schema is `{"type": "null"}` alone (always null, e.g. a skipped
///   Beat entry's `number`): the property is dropped, because there is nothing to decode.
/// Any other `null` anywhere in the document throws ``UnsupportedNull``.
package enum ContractRewrite {
    package struct UnsupportedNull: Error, CustomStringConvertible {
        package let path: String
        package var description: String {
            "unsupported null schema at \(path); extend ContractRewrite deliberately rather than let the generator skip it"
        }
    }

    package struct NotADocument: Error, CustomStringConvertible {
        package var description: String { "the contract is not a JSON object" }
    }

    package static func rewrite(_ data: Data) throws -> Data {
        guard var document = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NotADocument()
        }
        if var components = document["components"] as? [String: Any],
            var schemas = components["schemas"] as? [String: Any]
        {
            for (name, value) in schemas {
                if let schema = value as? [String: Any] {
                    schemas[name] = rewriteProperties(of: schema)
                }
            }
            components["schemas"] = schemas
            document["components"] = components
        }
        if let path = firstNull(in: document, at: "#") {
            throw UnsupportedNull(path: path)
        }
        return try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
    }

    private static func rewriteProperties(of schema: [String: Any]) -> [String: Any] {
        guard var properties = schema["properties"] as? [String: Any] else { return schema }
        var schema = schema
        var optional: Set<String> = []
        for (name, value) in properties {
            guard let property = value as? [String: Any] else { continue }
            if isNull(property) {
                properties[name] = nil
                optional.insert(name)
            } else if let inner = nonNullAlternative(of: property) {
                properties[name] = inner["$ref"] != nil ? inner : merge(property, inner)
                optional.insert(name)
            }
        }
        schema["properties"] = properties
        if let required = schema["required"] as? [String] {
            let kept = required.filter { !optional.contains($0) }
            schema["required"] = kept.isEmpty ? nil : kept
        }
        return schema
    }

    private static func isNull(_ schema: [String: Any]) -> Bool {
        schema["type"] as? String == "null"
    }

    /// `X` when `schema` is exactly `anyOf: [X, {"type": "null"}]` (either order).
    private static func nonNullAlternative(of schema: [String: Any]) -> [String: Any]? {
        guard let alternatives = schema["anyOf"] as? [Any], alternatives.count == 2 else { return nil }
        let objects = alternatives.compactMap { $0 as? [String: Any] }
        guard objects.count == 2 else { return nil }
        let nonNull = objects.filter { !isNull($0) }
        guard nonNull.count == 1, objects.contains(where: { isNull($0) && $0.count == 1 }) else {
            return nil
        }
        return nonNull[0]
    }

    /// The property's own metadata (title, description, ...) over `X`, minus the
    /// `anyOf` and a `null` default, which would contradict a non-null type.
    private static func merge(_ property: [String: Any], _ inner: [String: Any]) -> [String: Any] {
        var merged = property
        merged["anyOf"] = nil
        if merged["default"] is NSNull { merged["default"] = nil }
        merged.merge(inner) { _, new in new }
        return merged
    }

    private static func firstNull(in node: Any, at path: String) -> String? {
        if let object = node as? [String: Any] {
            if object["type"] as? String == "null" { return path }
            if let types = object["type"] as? [Any], types.contains(where: { $0 as? String == "null" }) {
                return path
            }
            for key in object.keys.sorted() {
                if let found = firstNull(in: object[key]!, at: "\(path)/\(key)") { return found }
            }
        } else if let array = node as? [Any] {
            for (index, element) in array.enumerated() {
                if let found = firstNull(in: element, at: "\(path)/\(index)") { return found }
            }
        }
        return nil
    }
}
