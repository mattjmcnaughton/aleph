import Core
import Design
import SwiftUI

/// The launch screen with one Sign in button (PRD §5.1). Copy matches the web's
/// `routes/login.tsx`; identifiers are what the UI tests find.
struct SignInScreen: View {
    @State private var model: SignInModel

    init(model: SignInModel) {
        _model = State(initialValue: model)
    }

    var body: some View {
        ScrollView {
            VStack(spacing: Spacing.xl) {
                AlephMark()
                    .padding(.top, Spacing.xxl)

                VStack(spacing: Spacing.md) {
                    Text("Sign in to Aleph", bundle: .module)
                        .font(.title.weight(.semibold))
                        .foregroundStyle(Palette.porcelain)
                        .accessibilityAddTraits(.isHeader)
                    Text(
                        "Name a topic, get a generated learning path. Your paths and progress sync to your account.",
                        bundle: .module
                    )
                    .font(.subheadline)
                    .foregroundStyle(Palette.mist)
                }

                if case .failed(let message) = model.state {
                    Text(message)
                        .font(.subheadline)
                        .foregroundStyle(Palette.danger)
                        .padding(Spacing.md)
                        .frame(maxWidth: .infinity)
                        .background(Palette.dangerBackground, in: .rect(cornerRadius: Radius.md))
                        .accessibilityIdentifier("sign-in-error")
                }

                Button {
                    Task { await model.signIn() }
                } label: {
                    if model.state == .signingIn {
                        Text("Signing in…", bundle: .module)
                    } else {
                        Text("Sign in", bundle: .module)
                    }
                }
                .buttonStyle(.alephPrimary)
                .disabled(model.state == .signingIn)
                .accessibilityIdentifier("sign-in-button")

                Text("We only read your public profile and email.", bundle: .module)
                    .font(.footnote)
                    .foregroundStyle(Palette.slate)
            }
            .multilineTextAlignment(.center)
            .padding(.horizontal, Spacing.lg)
            .frame(maxWidth: 440)
            .frame(maxWidth: .infinity)
        }
        .background(Palette.night)
    }
}
