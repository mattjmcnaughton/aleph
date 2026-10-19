import Foundation

/// Which links in untrusted model output stay links (iOS TDD §7, D9). Only `http` and
/// `https`: an `aleph:` link could drive the app's own OAuth callback, and `javascript:`,
/// `data:` or `file:` have no business in a Read passage. Anything else renders as its
/// plain text.
public enum LinkPolicy {
    public static let allowedSchemes: Set<String> = ["http", "https"]

    public static func allows(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else {
            return false
        }
        return allowedSchemes.contains(scheme)
    }
}
