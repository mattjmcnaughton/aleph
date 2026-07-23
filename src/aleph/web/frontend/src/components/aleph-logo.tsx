// The Aleph brand mark and lockup from the mocks. `AlephGlyph` is the single
// source of the teal-bordered rounded square holding the aleph glyph (א); every
// surface that needs the mark (top-chrome lockup, sign-in hero, empty-state
// illustration) renders it through here rather than hand-building the box.

const GLYPH_SIZES = {
  sm: "h-9 w-9 text-2xl", // 36px — brand lockup mark
  md: "h-14 w-14 text-3xl", // 56px — empty-state illustration
  lg: "h-[120px] w-[120px] text-7xl", // 120px — sign-in hero
} as const;

export type AlephGlyphSize = keyof typeof GLYPH_SIZES;

/** The teal glyph square on its own — the reusable Aleph mark. */
export function AlephGlyph({ size = "sm" }: { size?: AlephGlyphSize }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-flex items-center justify-center rounded-lg border border-teal/60 bg-teal/10 font-serif text-teal shadow-glow ${GLYPH_SIZES[size]}`}
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
