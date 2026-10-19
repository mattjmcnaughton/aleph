import SwiftUI

/// Nocturne's colours, copied from `clients/web/tailwind.config.ts` so both clients are one
/// system (iOS TDD §3.3). For content only: bars, toolbars and sheets keep the system's Liquid
/// Glass (TDD §1.B8). The app is dark only (TDD D2).
public enum Palette {
    // Surfaces.
    public static let night = Color(hex: 0x161826)
    public static let surface = Color(hex: 0x232532)
    public static let elevated = Color(hex: 0x292B31)

    // Text.
    public static let porcelain = Color(hex: 0xE9E9ED)
    public static let mist = Color(hex: 0x9397AB)
    public static let slate = Color(hex: 0x75798C)
    public static let faint = Color(hex: 0x595D6C)

    /// The path's accent.
    public static let teal = Color(hex: 0x4FB8C4)
    public static let tealBright = Color(hex: 0x6FCED9)
    public static let tealDim = Color(hex: 0x3F959F)

    /// The tutor's and the analyst's accent.
    public static let iris = Color(hex: 0x9184D9)

    /// Porcelain at 16% (`#e9e9ed29`).
    public static let divider = Color(hex: 0xE9E9ED, opacity: 0.16)

    public static let danger = Color(hex: 0xFF8A80)
    public static let dangerBackground = Color(hex: 0x2A1215)
    public static let dangerBorder = Color(hex: 0x5C2B2E)
}

extension Color {
    /// An sRGB colour from `0xRRGGBB`.
    init(hex: UInt32, opacity: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: opacity
        )
    }
}
