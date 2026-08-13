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
      // The tutor's "thinking" dots — the one motion the mocks don't cover,
      // because no mock surface waits on a token. Three dots share the cycle and
      // are offset by the delays in `tutor-rail.tsx`; the dim/lift pair reads as
      // a pulse travelling left to right rather than three lights blinking.
      keyframes: {
        thinking: {
          "0%, 70%, 100%": { opacity: "0.3", transform: "translateY(0)" },
          "35%": { opacity: "1", transform: "translateY(-2px)" },
        },
        // The path-complete seal (docs/mocks/aleph-path-complete.html). Four
        // one-shot steps, staggered by delay utilities at the call site rather
        // than by a single composite keyframe, so each element owns its own
        // motion and any one of them can be dropped without re-timing the rest.
        // `seal-draw`'s dash length is the circle's circumference (2πr, r=48),
        // set as a literal here because Tailwind keyframes take no arguments —
        // `path-complete.tsx` sets the matching `strokeDasharray`.
        "seal-draw": {
          from: { strokeDashoffset: "302" },
          to: { strokeDashoffset: "0" },
        },
        "seal-glyph": {
          from: { opacity: "0", transform: "scale(0.72)" },
          "60%": { opacity: "1", transform: "scale(1.06)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        "seal-halo": {
          from: { opacity: "0.9", transform: "scale(0.9)" },
          to: { opacity: "0", transform: "scale(1.22)" },
        },
        "rise-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "none" },
        },
        // One piece of confetti. The distance, drift and spin are per-particle
        // custom properties the component sets inline; only the shape of the
        // arc lives here.
        confetti: {
          "0%": { opacity: "1", transform: "translate(0, 0) rotate(0deg)" },
          "70%": { opacity: "1" },
          "100%": {
            opacity: "0",
            transform: "translate(var(--conf-dx), var(--conf-dy)) rotate(var(--conf-rot))",
          },
        },
      },
      animation: {
        thinking: "thinking 1.2s ease-in-out infinite",
        "seal-draw": "seal-draw 1.1s cubic-bezier(0.65, 0, 0.35, 1) both",
        "seal-glyph": "seal-glyph 0.7s cubic-bezier(0.22, 1, 0.36, 1) both",
        "seal-halo": "seal-halo 0.9s ease-out both",
        "rise-in": "rise-in 0.48s cubic-bezier(0.22, 1, 0.36, 1) both",
        confetti: "confetti var(--conf-dur) cubic-bezier(0.15, 0.65, 0.4, 1) both",
      },
    },
  },
  plugins: [],
} satisfies Config;
