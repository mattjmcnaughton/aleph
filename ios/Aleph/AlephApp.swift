import SwiftUI

@main
struct AlephApp: App {
    var body: some Scene {
        WindowGroup {
            HomeView()
                .preferredColorScheme(Self.forcedColorScheme)
        }
    }

    /// Lets UI tests pin the appearance: `ALEPH_COLOR_SCHEME=light|dark`.
    static var forcedColorScheme: ColorScheme? {
        switch ProcessInfo.processInfo.environment["ALEPH_COLOR_SCHEME"] {
        case "dark": return .dark
        case "light": return .light
        default: return nil
        }
    }
}
