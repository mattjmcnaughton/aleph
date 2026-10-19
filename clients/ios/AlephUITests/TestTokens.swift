import Foundation
import XCTest

/// The pre-signed tokens the e2e fake issuer accepts (`Fixtures/tokens.json`, re-minted by
/// `scripts/mint_e2e_tokens.py`; iOS TDD §5.5, D22).
enum TestTokens {
    enum Identity: String {
        case learner
        case admin
        case expiredLearner
    }

    /// The `-AlephTestTokens` value: a JSON array of tokens, used in order.
    static func launchValue(_ identities: Identity...) throws -> String {
        let fixture = try XCTUnwrap(
            Bundle(for: Marker.self).url(forResource: "tokens", withExtension: "json"),
            "Fixtures/tokens.json is missing from the UI test bundle"
        )
        let tokens = try JSONDecoder().decode([String: String].self, from: Data(contentsOf: fixture))
        let chosen = try identities.map { identity in
            try XCTUnwrap(tokens[identity.rawValue], "no \(identity.rawValue) token in tokens.json")
        }
        let encoded = try JSONEncoder().encode(chosen)
        return String(decoding: encoded, as: UTF8.self)
    }

    private final class Marker {}
}
