// The Aleph brand mark and lockup. `AlephGlyph` is the single source of the mark;
// every surface that needs it (top-chrome lockup, sign-in, empty states, the
// tutor rail) renders it through here rather than hand-building one.
//
// The glyph is a drawn outline, not a font character. It used to be the text
// "א" in `font-serif`, which no shipped font contains, so each platform
// substituted its own Hebrew face and the brand looked different on every
// device. The outline is the same one `public/icons/aleph.svg` uses (DejaVu
// Sans, public domain; see `public/icons/LICENSE.txt`).
//
// The mark carries an **accent**, not a fixed colour: teal is the brand's and the
// path's, iris is the tutor's (PRD §5.10). Both are variants of one component for
// the reason this file exists at all: a second hand-built mark drifts.

const GLYPH_SIZES = {
  "2xs": "h-5 w-5 rounded-[5px]", // 20px: the mark beside a tutor message
  xs: "h-7 w-7 rounded-md", // 28px: the tutor rail's header mark, the lockup
  sm: "h-9 w-9 rounded-md", // 36px
  md: "h-12 w-12 rounded-lg", // 48px: empty-state illustration
  lg: "h-16 w-16 rounded-xl", // 64px: sign-in
} as const;

/** Tint and glyph colour: the whole accent, in one string each. */
const GLYPH_ACCENTS = {
  teal: "bg-teal/[0.14] text-teal",
  iris: "bg-iris/[0.16] text-iris-300",
} as const;

export type AlephGlyphSize = keyof typeof GLYPH_SIZES;
export type AlephGlyphAccent = keyof typeof GLYPH_ACCENTS;

/** The mark on its own: the aleph on a quiet tinted tile. */
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
      // tile is fixed-size in both axes.
      className={`inline-flex shrink-0 items-center justify-center ${GLYPH_ACCENTS[accent]} ${GLYPH_SIZES[size]}`}
    >
      <svg
        viewBox="124.5 0 1120 1120"
        className="h-[58%] w-[58%]"
        fill="currentColor"
        aria-hidden="true"
      >
        <path
          transform="translate(0 1120) scale(1 -1)"
          d="M896 411 1181 0H964L548 600Q520 586 502.5 575.5Q485 565 457 538.5Q429 512 412.5 479.5Q396 447 383 392Q370 337 370 268V0H186V185Q186 266 196 335Q206 404 221.5 452Q237 500 262 540.5Q287 581 309.5 606Q332 631 364.5 653Q397 675 419 685.5Q441 696 472 709L188 1120H405L821 520Q848 533 865.5 543.5Q883 554 911 581Q939 608 956 640.5Q973 673 986 728Q999 783 999 852V1120H1183V935Q1183 853 1173 784.5Q1163 716 1147.5 667.5Q1132 619 1107 578.5Q1082 538 1059 513.5Q1036 489 1004 467Q972 445 950 434.5Q928 424 896 411Z"
        />
      </svg>
    </span>
  );
}

/** Brand lockup: the mark beside the wordmark, set in the reading face. */
export function AlephLogo() {
  return (
    <span className="inline-flex items-center gap-2.5">
      <AlephGlyph size="xs" />
      <span className="font-serif text-[1.1875rem] font-medium tracking-[-0.01em] text-porcelain">
        Aleph
      </span>
    </span>
  );
}
