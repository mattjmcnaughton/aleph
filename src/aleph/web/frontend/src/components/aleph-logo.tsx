// The Aleph brand mark and lockup from the mocks. `AlephGlyph` is the single
// source of the bordered rounded square holding the aleph glyph (א); every
// surface that needs the mark (top-chrome lockup, sign-in hero, empty-state
// illustration, the tutor rail) renders it through here rather than hand-building
// the box.
//
// The mark carries an **accent**, not a fixed colour: teal is the brand's and the
// path's, iris is the tutor's (PRD §5.10). Both are variants of one component for
// the reason this file exists at all — a second hand-built square drifts.

const GLYPH_SIZES = {
  "2xs": "h-5 w-5 text-xs", // 20px — the mark beside a tutor message
  xs: "h-7 w-7 text-lg", // 28px — the tutor rail's header mark
  sm: "h-9 w-9 text-2xl", // 36px — brand lockup mark
  md: "h-14 w-14 text-3xl", // 56px — empty-state illustration
  lg: "h-[120px] w-[120px] text-7xl", // 120px — sign-in hero
} as const;

/** Border, fill, glyph colour and glow — the whole accent, in one string each. */
const GLYPH_ACCENTS = {
  teal: "border-teal/60 bg-teal/10 text-teal shadow-glow",
  iris: "border-iris/60 bg-iris/10 text-iris-300 shadow-glow-iris",
} as const;

export type AlephGlyphSize = keyof typeof GLYPH_SIZES;
export type AlephGlyphAccent = keyof typeof GLYPH_ACCENTS;

/** The glyph square on its own — the reusable Aleph mark. */
export function AlephGlyph({
  size = "sm",
  accent = "teal",
}: {
  size?: AlephGlyphSize;
  accent?: AlephGlyphAccent;
}) {
  return (
    <span
      aria-hidden="true"
      // `shrink-0` matters only where the mark sits in a flex row beside prose
      // (the tutor's message bubbles); everywhere else it is inert, because the
      // square is fixed-size in both axes.
      className={`inline-flex shrink-0 items-center justify-center rounded-lg font-serif border ${GLYPH_ACCENTS[accent]} ${GLYPH_SIZES[size]}`}
    >
      א
    </span>
  );
}

/** Brand lockup: the glyph mark next to the Aleph wordmark (top chrome). */
export function AlephLogo() {
  return (
    <span className="inline-flex items-center gap-2.5">
      <AlephGlyph size="sm" />
      <span className="font-sans text-lg font-semibold tracking-tight text-porcelain">Aleph</span>
    </span>
  );
}
