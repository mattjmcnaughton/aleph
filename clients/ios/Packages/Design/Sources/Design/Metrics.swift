import CoreGraphics

/// Spacing steps, in points (Tailwind's 4-point scale).
public enum Spacing {
    public static let xs: CGFloat = 4
    public static let sm: CGFloat = 8
    public static let md: CGFloat = 12
    public static let lg: CGFloat = 16
    public static let xl: CGFloat = 24
    public static let xxl: CGFloat = 32
}

/// Corner radii (`borderRadius` in the web's Tailwind config).
public enum Radius {
    public static let sm: CGFloat = 4
    public static let md: CGFloat = 8
    public static let lg: CGFloat = 14
    public static let xl: CGFloat = 20
}
