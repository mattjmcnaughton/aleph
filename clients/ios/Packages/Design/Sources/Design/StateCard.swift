import Core
import SwiftUI

/// The card every screen shows for a state that is not its content: loading, an error with a
/// retry (iOS TDD §6.2, PRD §5 "a defined state for every server state"). Later tickets add
/// the generating, refused, stalled and not-found variants.
public struct StateCard: View {
    private let title: String?
    private let message: String
    private let tone: Tone
    private let action: (title: String, run: () -> Void)?

    enum Tone {
        case neutral
        case danger
    }

    init(title: String?, message: String, tone: Tone, action: (title: String, run: () -> Void)?) {
        self.title = title
        self.message = message
        self.tone = tone
        self.action = action
    }

    /// A spinner with a short line of copy ("Loading…" when none is given).
    public static func loading(_ message: String? = nil) -> StateCard {
        StateCard(
            title: nil,
            message: message ?? String(localized: "Loading…", bundle: .module),
            tone: .neutral,
            action: nil
        )
    }

    /// A failure the learner can retry. The message comes from the error, never from the server.
    public static func error(_ error: AppError, title: String, retry: @escaping () -> Void) -> StateCard {
        StateCard(
            title: title,
            message: ErrorCopy.message(for: error),
            tone: .danger,
            action: (String(localized: "Try again", bundle: .module), retry)
        )
    }

    public var body: some View {
        VStack(spacing: Spacing.md) {
            if action == nil && title == nil {
                ProgressView()
                    .tint(Palette.mist)
            }
            if let title {
                Text(title)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(tone == .danger ? Palette.danger : Palette.porcelain)
                    .accessibilityAddTraits(.isHeader)
            }
            Text(message)
                .font(.subheadline)
                .foregroundStyle(Palette.mist)
            if let action {
                Button(action.title, action: action.run)
                    .buttonStyle(.alephPrimary)
                    .padding(.top, Spacing.sm)
            }
        }
        .multilineTextAlignment(.center)
        .padding(Spacing.xl)
        .frame(maxWidth: .infinity)
        .background(Palette.surface, in: .rect(cornerRadius: Radius.lg))
        .overlay {
            RoundedRectangle(cornerRadius: Radius.lg)
                .strokeBorder(tone == .danger ? Palette.dangerBorder.opacity(0.6) : Palette.divider, lineWidth: 1)
        }
    }
}

/// What the learner reads for each failure (PRD §5 cross-cutting rules).
public enum ErrorCopy {
    public static func message(for error: AppError) -> String {
        switch error {
        case .offline:
            String(localized: "You're offline. Check your connection and try again.", bundle: .module)
        case .timedOut:
            String(localized: "That took too long. Try again.", bundle: .module)
        case .serviceUnavailable:
            String(localized: "Can't reach sign-in right now. Try again.", bundle: .module)
        case .rateLimited:
            String(localized: "Daily limit reached. Try again tomorrow.", bundle: .module)
        case .notFound:
            String(localized: "That isn't here.", bundle: .module)
        case .unauthenticated, .conflict, .validation, .server:
            String(localized: "Something went wrong. Try again.", bundle: .module)
        }
    }
}
