import SwiftUI

/// The Aleph mark at hero size: the aleph glyph in a bordered rounded square, teal. Web:
/// `AlephGlyph size="lg"` (`clients/web/src/components/aleph-logo.tsx`). Decorative, so
/// hidden from VoiceOver.
public struct AlephMark: View {
    private static let side: CGFloat = 120
    private static let glyphSize: CGFloat = 72

    public init() {}

    public var body: some View {
        Text(verbatim: "\u{05D0}")
            .font(.system(size: Self.glyphSize, design: .serif))
            .foregroundStyle(Palette.teal)
            .frame(width: Self.side, height: Self.side)
            .background(Palette.teal.opacity(0.1), in: .rect(cornerRadius: Radius.lg))
            .overlay {
                RoundedRectangle(cornerRadius: Radius.lg)
                    .strokeBorder(Palette.teal.opacity(0.6), lineWidth: 1)
            }
            .shadow(color: Palette.teal.opacity(0.16), radius: 17, y: 10)
            .accessibilityHidden(true)
    }
}
