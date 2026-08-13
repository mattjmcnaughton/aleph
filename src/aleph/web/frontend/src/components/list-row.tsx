// The one row grammar the home screen has (design critique, theme 1). A path
// row and a Beat row are the same object with a different verb — the Phase 6
// PRD already said so ("the same card grammar (title, a line of state) with a
// different verb") — but the two had drifted into genuinely different
// primitives: a path was a table row (`lg:border-b`, transparent, five
// columns) and a Beat was a bordered `rounded-lg` card with a shadow, so at
// `lg` they read as two unrelated kinds of thing stacked on one page. This is
// the primitive both now render through.
//
// **Grid, not flex — and that is the bug fix.** The old path row was a flex
// line whose leading `<Link>` was `flex-1` with the review chip and Delete as
// `shrink-0` siblings *outside* it. So a row that happened to carry a chip
// gave its title column less room than a row that did not, and the progress
// and status columns landed at visibly different x positions from row to row.
// A grid with named, fixed track widths cannot express that bug: every row
// resolves the same tracks whether or not it fills them.

import type { ReactNode } from "react";

/** Status tone per row variant — refusal is iris, failure is danger (CONTEXT). */
export const ROW_STATUS_TONE = {
  neutral: "text-mist",
  refusal: "text-iris-300",
  error: "text-danger",
} as const;

export type RowVariant = keyof typeof ROW_STATUS_TONE;

// Phone: a full card fill per status. Desktop (`lg:`): the row becomes a
// table-ish line and the tint becomes a 2px inset bar instead (mock #2c rows
// 4-5) — `lg:shadow-none` for the ordinary row, an inset `lg:shadow-[...]` for
// refusal/error, so exactly one `lg:shadow-*` utility is ever active per row.
const ROW_TONE = {
  neutral: "border-divider bg-surface lg:shadow-none",
  refusal: "border-iris-700 bg-iris-900 lg:shadow-[inset_2px_0_0_theme(colors.iris.700)]",
  error:
    "border-danger-border/60 bg-danger-bg lg:shadow-[inset_2px_0_0_theme(colors.danger.DEFAULT)]",
} as const;

// `group` is what lets a row's Delete button reveal on hover (see `RowActions`).
const ROW_BASE =
  "group relative rounded-lg border p-4 shadow-sm lg:grid lg:items-center lg:gap-x-5 lg:rounded-none lg:border-0 lg:border-b lg:border-divider lg:bg-transparent lg:px-2 lg:py-4";

// The shared track template, and the reason every row lines up:
//   title (flexible) | meta (210px) | status (110px) | chip (76px) | actions (84px)
//
// **Every track except the title is a fixed width, and that is load-bearing.**
// Each row is its own grid element, so tracks resolve per row, not across the
// list — CSS Grid aligns cells within one grid, and sibling grids share
// nothing. What makes the columns line up anyway is that every row is the same
// outer width and every non-flexible track is a constant, so `minmax(0,1fr)`
// resolves to the identical number in each of them. An `auto` track would
// break exactly that: a row with no review chip would size its chip track to
// zero and pull the four columns after it leftward, which is the original
// misalignment bug wearing a grid costume (it is visible in the very first
// screenshot of this layout, where the two chipless rows sat visibly right of
// the rest). `minmax(0,1fr)` rather than a bare `1fr` is what keeps a long
// title truncating inside its track instead of widening the grid.
//
// Rows are implicit rather than declared: `footer` flows into a second one
// when a row has it, and no row is reserved when it does not.
const ROW_GRID = "lg:grid-cols-[minmax(0,1fr)_210px_110px_88px_84px]";

/**
 * One row of a home list. Every cell is optional so a Beat (title + state, no
 * progress, no chip) and a path (all five) share one set of tracks.
 *
 * `main` is the row's own link — the whole title/meta block — and the caller
 * owns it rather than this primitive, because a path links to `/paths/$id`
 * and a Beat to `/beats/$id`, and because `actions` must stay a **sibling** of
 * that link, never nested inside it (a link inside a link is invalid HTML and
 * races the row's own navigation).
 */
export function ListRow({
  variant = "neutral",
  testid,
  dataAttrs,
  main,
  meta,
  status,
  chip,
  actions,
  footer,
}: {
  variant?: RowVariant;
  testid: string;
  dataAttrs?: Record<string, string | undefined>;
  /** The title block — the caller's own `<Link>`, spanning the first track. */
  main: ReactNode;
  /** Progress, cadence, or nothing: the row's quantitative middle. */
  meta?: ReactNode;
  /** The one-line state readout. */
  status?: ReactNode;
  /** A call to action that is not the row's own destination (e.g. `Review 7`). */
  chip?: ReactNode;
  /** Destructive/secondary controls, always a sibling of `main`'s link. */
  actions?: ReactNode;
  /**
   * Full-width content beneath the row's cells, **inside** the row element:
   * the inline delete confirm, which is a paragraph plus two buttons and
   * cannot live in a track sized for one button. Keeping it inside the row
   * rather than after it is what makes "this row's confirm" a containment
   * question — which is how both the tests and a screen reader ask it.
   */
  footer?: ReactNode;
}) {
  return (
    <div
      data-testid={testid}
      {...dataAttrs}
      className={`${ROW_BASE} ${ROW_GRID} ${ROW_TONE[variant]}`}
    >
      {main}
      {/* Empty cells are rendered, not omitted: an omitted cell would let the
          following ones slide up a track and re-create the misalignment this
          grid exists to prevent. Below `lg` the grid is off and an empty div
          costs nothing. */}
      <div className="lg:min-w-0">{meta}</div>
      <div className={`mt-1 text-sm lg:mt-0 ${ROW_STATUS_TONE[variant]}`}>{status}</div>
      <div>{chip}</div>
      {/* `lg:justify-end`: a path row's actions are Delete + chevron and a
          Beat row's are the chevron alone, so left-packing would land the two
          sections' chevrons at different x positions — the one thing a shared
          row grammar cannot afford to get wrong. */}
      <div className="mt-3 flex items-center gap-2 lg:mt-0 lg:justify-end">{actions}</div>
      {footer ? <div className="lg:col-span-full">{footer}</div> : null}
    </div>
  );
}

/**
 * The row's title block: the caller's destination, the title, and a line of
 * small metadata beneath it. Kept here rather than at each call site so a path
 * and a Beat cannot drift apart on type scale or truncation behaviour again.
 */
export function RowTitle({
  title,
  titleTestid,
  children,
}: {
  title: string;
  titleTestid: string;
  /** The sub-line: a level tag, a streak chip, a cadence — or nothing. */
  children?: ReactNode;
}) {
  return (
    <div className="min-w-0">
      {/* `truncate` is the project's convention for learner text (`sidebar.tsx`,
          `paths.$pathId.tsx`): a pasted URL or an over-long topic must not
          widen the row at 390px. */}
      <p data-testid={titleTestid} className="truncate text-base font-semibold leading-snug">
        {title}
      </p>
      {children ? <div className="mt-1 flex items-center gap-2">{children}</div> : null}
    </div>
  );
}

/**
 * A row's destructive/secondary controls.
 *
 * **Quiet by default at `lg`** (design critique, theme 4): Delete used to be
 * the only bordered element on a row, so eight rows stacked it into a hard
 * vertical rail that pulled the eye to the destructive action — while the
 * row's actual primary action (open it) was a 12px grey chevron. Here it is
 * transparent until the row is hovered or something inside it takes focus,
 * and it never leaves the layout, so revealing it cannot shift a column.
 *
 * `focus-within` is not optional garnish: without it the control would be
 * invisible to a keyboard user who has just tabbed onto it. And a touch device
 * has no hover at all, so `(hover: none)` pins it visible there — the phone
 * layout is unchanged from what shipped.
 */
export function RowActions({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-1 lg:opacity-0 lg:transition-opacity lg:group-hover:opacity-100 lg:group-focus-within:opacity-100 lg:[@media(hover:none)]:opacity-100">
      {children}
    </div>
  );
}
