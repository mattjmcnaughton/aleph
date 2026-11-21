import type { Config } from "tailwindcss";

// Nocturne: Aleph's dark, teal, mobile-first visual system. This is its second
// pass ("a study, not an arcade"): the token *names* are unchanged so every
// surface keeps working, but the values moved from a violet-navy with neon
// accents to a neutral ink with warm paper text, a serif reading face, and
// accents that each mean one thing. docs/design.md records the reasoning.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Surfaces: a deep, neutral-cool ink (a room at night, not a purple
        // "dark mode"), with two raised steps.
        night: "#11161b",
        surface: "#182027",
        elevated: "#202a32",
        // Text: warm paper on the cool ink (lamplight on a page). Each step
        // down is a role, not a tint: body / secondary / tertiary / hairline.
        porcelain: "#ebe6da",
        mist: "#a5a89f",
        slate: "#7f847d",
        faint: "#49535a",
        // Teal means "forward": the primary action, the learner's position and
        // their progress. Nothing else is teal.
        teal: {
          DEFAULT: "#56b3ab",
          bright: "#7dcdc5",
          dim: "#3f8d86",
        },
        // Iris marks the tutor and what it proposes (ghost rows, Proposals,
        // unread Briefs). Never an action colour.
        iris: {
          DEFAULT: "#9a92d8",
          300: "#cfcaf2",
          400: "#b3acea",
          500: "#9a92d8",
          600: "#7b73b8",
          700: "#575088",
          900: "#24223a",
        },
        section: {
          DEFAULT: "#262a60",
          glow: "#353b80",
          ghost: "#4c5397",
        },
        // The streak's flame, and only that.
        ember: "#e3a35a",
        // Hairline divider: paper at 12%.
        divider: "#ebe6da1f",
        danger: {
          DEFAULT: "#ef8f82",
          bg: "#2a1614",
          border: "#5e2e29",
        },
      },
      fontFamily: {
        // UI: Instrument Sans. Reading and titles: Literata, a face drawn for
        // long-form reading on screens. Mono is for code, never for labels.
        sans: [
          '"Aleph Interpunct"',
          '"Instrument Sans Variable"',
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
        serif: ['"Aleph Interpunct"', '"Literata Variable"', "Charter", "Georgia", "serif"],
        mono: ['"JetBrains Mono Variable"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderRadius: {
        // Radius follows hierarchy: controls 8, cards 12, sheets 16.
        sm: "4px",
        md: "8px",
        lg: "12px",
        xl: "16px",
      },
      boxShadow: {
        // Elevation is for things that float (menus, sheets), not for cards:
        // a card is separated by its border, so `sm` is deliberately nothing.
        sm: "0 0 #0000",
        md: "0 8px 24px rgba(0,0,0,0.4)",
        lg: "0 20px 56px rgba(0,0,0,0.55)",
        // The "glow" tokens are now plain accent rings. The blurred halo read
        // as decoration; a ring still marks the tutor (iris) and the path (teal).
        glow: "0 0 0 1px rgba(86,179,171,0.35)",
        "glow-iris": "0 0 0 1px rgba(154,146,216,0.4)",
      },
      letterSpacing: {
        kicker: "0", // labels are no longer tracked-out capitals
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
        // The flow receipt's strip (`components/flow/flow-bar.tsx`,
        // `FlowSegments`): a done cell lights from the empty-cell tint to teal,
        // and the last cell then pulses once. `to` is the cell's resting
        // colour, so an un-animated cell looks exactly like a finished one.
        "seg-light": {
          from: { backgroundColor: "rgb(235 230 218 / 0.1)" },
          to: { backgroundColor: "#56b3ab" },
        },
        "seg-pulse": {
          from: { boxShadow: "0 0 0 0 rgb(125 205 197 / 0.55)" },
          to: { boxShadow: "0 0 0 9px rgb(125 205 197 / 0)" },
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
        "seg-light": "seg-light 0.32s ease-out both",
        "seg-last": "seg-light 0.32s ease-out both, seg-pulse 0.7s ease-out both",
        confetti: "confetti var(--conf-dur) cubic-bezier(0.15, 0.65, 0.4, 1) both",
      },
    },
  },
  plugins: [],
} satisfies Config;
