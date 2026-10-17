import SwiftUI

@main
struct AlephApp: App {
    var body: some Scene {
        WindowGroup {
            // Dark only, matching the dark-only web app.
            HomeView()
                .preferredColorScheme(.dark)
        }
    }
}
