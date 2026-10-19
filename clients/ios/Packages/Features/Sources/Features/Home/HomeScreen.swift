import Core
import Design
import SwiftUI

/// Your paths (PRD §5.2). This ticket draws the shell: the greeting and the account menu.
/// The list, the Continue card and polling arrive with AL-604. Web: `routes/index.tsx`.
struct HomeScreen: View {
    let account: Account
    let onSignOut: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.md) {
                Text("Welcome back, \(account.firstName).", bundle: .module)
                    .font(.largeTitle.weight(.semibold))
                    .foregroundStyle(Palette.porcelain)
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("home-greeting")
            }
            .padding(Spacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Palette.night)
        .accessibilityIdentifier("paths-switcher")
        .navigationTitle(Text("Your paths", bundle: .module))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                AccountMenu(account: account, onSignOut: onSignOut)
            }
        }
    }
}

/// The account menu: display name, email and Sign out (PRD §5.1). Notification settings join
/// it with push (AL-614).
struct AccountMenu: View {
    let account: Account
    let onSignOut: () -> Void

    var body: some View {
        Menu {
            Section {
                Text(account.displayName)
                if let email = account.email {
                    Text(email)
                }
            }
            Button(role: .destructive, action: onSignOut) {
                Text("Sign out", bundle: .module)
            }
            .accessibilityIdentifier("sign-out")
        } label: {
            Label {
                Text("Account", bundle: .module)
            } icon: {
                Image(systemName: "person.crop.circle")
            }
        }
        .accessibilityIdentifier("account-menu")
    }
}
