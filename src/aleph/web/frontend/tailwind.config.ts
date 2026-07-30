import type { Config } from "tailwindcss";

// Nocturne — Aleph's dark/teal, mobile-first visual system. Most tokens are
// lifted directly from the CSS custom properties in docs/mocks/aleph-mvp-*.html
// so the app and the mocks stay one system. A few are Nocturne extensions with
// no mock counterpart — the hover/dim teal ramp (teal.bright, teal.dim) and the
// accent glow (shadow.glow) — derived from the accent to fill interaction states
// the static mocks don't cover.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Surfaces (--color-bg / --color-surface / neutral-900 inset).
        night: "#161826",
        surface: "#232532",
        elevated: "#292b31",
        // Text (--color-text + neutral scale).
        porcelain: "#e9e9ed",
        mist: "#9397ab", // neutral-500 — muted body / kickers
        slate: "#75798c", // neutral-600 — dimmer captions
        faint: "#595d6c", // neutral-700 — hairline borders
        // Primary accent — Nocturne teal (--color-accent override in the mocks).
        teal: {
          DEFAULT: "#4fb8c4",
          bright: "#6fced9",
          dim: "#3f959f",
        },
        // Secondary accent — the iris/violet scale (--color-accent-*), used for
        // section glows and the aleph watermark texture.
        iris: {
          DEFAULT: "#9184d9",
          300: "#d2cefd",
          400: "#b5abfc",
          500: "#968ae0",
          600: "#796cbf",
          700: "#5d5294",
          900: "#2b2741",
        },
        section: {
          DEFAULT: "#262a60",
          glow: "#353b80",
          ghost: "#4c5397",
        },
        // Hairline divider — --color-divider (porcelain @ 16%).
        divider: "#e9e9ed29",
        // Error surface (--color error tokens in the mocks).
        danger: {
          DEFAULT: "#ff8a80",
          bg: "#2a1215",
          border: "#5c2b2e",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderRadius: {
        sm: "4px", // --radius-sm
        md: "8px", // --radius-md
        lg: "14px", // --radius-lg
        xl: "20px",
      },
      boxShadow: {
        sm: "0 0 0 1px #3f424d", // --shadow-sm
        md: "0 0 0 1px #595d6c, 0 6px 18px rgba(0,0,0,0.55)", // --shadow-md
        lg: "0 0 0 1px #9397ab, 0 16px 40px rgba(0,0,0,0.65)", // --shadow-lg
        glow: "0 0 0 1px rgba(79,184,196,0.45), 0 10px 34px rgba(79,184,196,0.16)",
        // The tutor's glow, iris to teal's (Phase 2 PRD §5.10 — iris marks the
        // tutor, teal the path). Same recipe as `glow`, derived from iris
        // DEFAULT (#9184d9) so the two accents read as one system.
        "glow-iris": "0 0 0 1px rgba(145,132,217,0.45), 0 10px 34px rgba(145,132,217,0.16)",
      },
      letterSpacing: {
        kicker: "0.14em", // uppercase mono section kickers
      },
    },
  },
  plugins: [],
} satisfies Config;
