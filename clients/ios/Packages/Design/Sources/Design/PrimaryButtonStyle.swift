import SwiftUI

/// The one prominent action on a screen (TDD §1.B8): a full-width teal button with night text.
/// Web: the `bg-teal ... text-night` buttons.
public struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    public init() {}

    public func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .foregroundStyle(Palette.night)
            .frame(maxWidth: .infinity)
            .padding(.vertical, Spacing.md)
            .padding(.horizontal, Spacing.lg)
            .background(
                configuration.isPressed ? Palette.tealBright : Palette.teal,
                in: .rect(cornerRadius: Radius.md)
            )
            .opacity(isEnabled ? 1 : 0.5)
    }
}

extension ButtonStyle where Self == PrimaryButtonStyle {
    /// `.buttonStyle(.alephPrimary)`.
    public static var alephPrimary: PrimaryButtonStyle {
        PrimaryButtonStyle()
    }
}
