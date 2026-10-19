import Foundation
import OpenAPIURLSession
import Testing

@testable import API

/// The committed generated client builds and decodes the contract's shared shapes
/// (iOS TDD §4.1, §4.2).
struct GeneratedClientTests {
    @Test func clientBuildsOverTheURLSessionTransport() throws {
        let serverURL = try #require(URL(string: "https://aleph.example/"))
        _ = Client(serverURL: serverURL, transport: URLSessionTransport())
    }

    @Test func errorEnvelopeDecodes() throws {
        let body = Data(
            #"{"error":{"code":"not_found","message":"No such path","request_id":"r-1"}}"#.utf8
        )
        let envelope = try JSONDecoder().decode(Components.Schemas.ErrorEnvelope.self, from: body)
        #expect(envelope.error.code == "not_found")
        #expect(envelope.error.requestId == "r-1")
    }

    @Test func streamDeltaFrameDecodes() throws {
        let frame = try JSONDecoder().decode(
            Components.Schemas.StreamDelta.self, from: Data(#"{"text":"Hello"}"#.utf8))
        #expect(frame.text == "Hello")
    }

    /// FastAPI declares a nullable field as `anyOf: [X, {"type": "null"}]`, which the
    /// generator skips; prepare-contract rewrites it to an Optional. These fields used
    /// to be missing from the generated types, so naming them is a compile-time pin.
    @Test func nullableFieldsDecodeAsOptionals() throws {
        let row = #"""
            {"id": "p1", "topic": "Rust", "title": "Rust", "level": "new_to_it", "status": "ready",
             "progress": {"total_lessons": 3, "generated_lessons": 1, "completed_lessons": 0},
             "last_activity_at": null, "next_lesson": null}
            """#
        let path = try JSONDecoder().decode(Components.Schemas.PathSummaryDTO.self, from: Data(row.utf8))
        #expect(path.nextLesson == nil)
        #expect(path.lastActivityAt == nil)

        let lesson = #"""
            {"id": "l1", "path_id": "p1", "title": "Ownership", "position_in_path": 1,
             "position_in_unit": 1, "generation_state": "generated", "unlock_state": "available",
             "read_passage": "# Ownership", "quick_check": null, "attempt": null,
             "generation_error": null}
            """#
        let detail = try JSONDecoder().decode(
            Components.Schemas.LessonDetailResponse.self, from: Data(lesson.utf8))
        #expect(detail.readPassage == "# Ownership")
        #expect(detail.quickCheck == nil)
    }
}
