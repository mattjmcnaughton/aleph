import Foundation
import Testing

@testable import ContractRewrite

struct ContractRewriteTests {
    private func rewrite(_ schemas: [String: Any]) throws -> [String: Any] {
        let document: [String: Any] = ["openapi": "3.1.0", "components": ["schemas": schemas]]
        let data = try JSONSerialization.data(withJSONObject: document)
        let output = try JSONSerialization.jsonObject(with: ContractRewrite.rewrite(data))
        let components = (output as? [String: Any])?["components"] as? [String: Any]
        return try #require(components?["schemas"] as? [String: Any])
    }

    private func schema(_ schemas: [String: Any], _ name: String) throws -> [String: Any] {
        try #require(schemas[name] as? [String: Any])
    }

    @Test func nullableReferenceBecomesAnOptionalReference() throws {
        let schemas = try rewrite([
            "Row": [
                "type": "object",
                "properties": [
                    "id": ["type": "string"],
                    "next": [
                        "anyOf": [["$ref": "#/components/schemas/Next"], ["type": "null"]],
                        "description": "Where continue goes.",
                    ],
                ],
                "required": ["id", "next"],
            ]
        ])
        let row = try schema(schemas, "Row")
        let properties = try #require(row["properties"] as? [String: Any])
        let next = try #require(properties["next"] as? [String: Any])
        #expect(next.count == 1)
        #expect(next["$ref"] as? String == "#/components/schemas/Next")
        #expect(row["required"] as? [String] == ["id"])
    }

    @Test func nullablePrimitiveKeepsItsMetadataInEitherOrder() throws {
        let schemas = try rewrite([
            "Row": [
                "type": "object",
                "properties": [
                    "at": [
                        "anyOf": [["type": "null"], ["type": "string", "format": "date-time"]],
                        "title": "At",
                        "default": NSNull(),
                    ]
                ],
                "required": ["at"],
            ]
        ])
        let row = try schema(schemas, "Row")
        let properties = try #require(row["properties"] as? [String: Any])
        let at = try #require(properties["at"] as? [String: Any])
        #expect(at["type"] as? String == "string")
        #expect(at["format"] as? String == "date-time")
        #expect(at["title"] as? String == "At")
        #expect(at["default"] == nil)
        #expect(at["anyOf"] == nil)
        #expect(row["required"] == nil)
    }

    @Test func alwaysNullPropertyIsDropped() throws {
        let schemas = try rewrite([
            "Skipped": [
                "type": "object",
                "properties": ["id": ["type": "string"], "number": ["type": "null", "title": "Number"]],
                "required": ["id"],
            ]
        ])
        let properties = try #require(try schema(schemas, "Skipped")["properties"] as? [String: Any])
        #expect(properties["number"] == nil)
        #expect(properties["id"] != nil)
    }

    @Test func schemasWithoutNullAreUntouched() throws {
        let original: [String: Any] = [
            "type": "object",
            "properties": ["id": ["type": "string"]],
            "required": ["id"],
        ]
        let row = try schema(try rewrite(["Row": original]), "Row")
        #expect(NSDictionary(dictionary: row).isEqual(to: original))
    }

    @Test(arguments: [
        // A nullable union of two non-null types.
        #"{"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}"#,
        // A 3.1 type array.
        #"{"type": ["string", "null"]}"#,
        // Null nested below a property, not at its top.
        #"{"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "null"}]}}"#,
    ])
    func anyOtherNullFormFailsLoudly(property: String) throws {
        let value = try JSONSerialization.jsonObject(with: Data(property.utf8))
        #expect(throws: ContractRewrite.UnsupportedNull.self) {
            _ = try rewrite(["Row": ["type": "object", "properties": ["value": value]]])
        }
    }
}
