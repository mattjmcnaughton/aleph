import AlephMarkdown
import Foundation
import Testing

/// The link half of the Markdown allowlist. The web's counterpart is the `javascript:` case
/// in `clients/web/src/components/markdown.test.tsx`; iOS is stricter (http and https only,
/// TDD D9).
struct LinkPolicyTests {
    @Test(arguments: ["https://example.com/a", "http://example.com", "HTTPS://EXAMPLE.COM"])
    func webLinksStayLinks(link: String) throws {
        #expect(LinkPolicy.allows(try #require(URL(string: link))))
    }

    @Test(arguments: [
        "aleph://oauth/callback?code=stolen",
        "javascript:alert(1)",
        "data:text/html,hi",
        "file:///etc/passwd",
        "mailto:someone@example.com",
        "/relative/path",
    ])
    func everythingElseIsPlainText(link: String) throws {
        #expect(!LinkPolicy.allows(try #require(URL(string: link))))
    }
}
