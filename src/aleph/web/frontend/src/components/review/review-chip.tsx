// The `Review 7` chip inside each `PathRow` (PRD §4.3, Phase 3 TDD §8): this
// path's share of the global daily queue, and Door 3 into a filtered session
// (`/review?path=…`). Deliberately its own link rather than nested inside the
// row's `path-item-open` anchor — a link inside a link is invalid HTML and
// would race the row's own navigation, so `routes/index.tsx` renders this as
// a sibling of that link, the same way the row's Delete button already is.

import { Link } from "@tanstack/react-router";

/**
 * `dueCount` is this path's row in the summary's `paths` array, or
 * `undefined` when the path has no cards in today's queue — "absent means
 * zero", the same rule `PathStreak` already established (D5), so the caller
 * never has to special-case "no row" from "a row of zero".
 *
 * Per §5.3, the number here is this path's **share** of the global ten —
 * `Review 7` beside `10 cards` means seven of today's ten came from this
 * path, never that the path itself has seven cards of its own due.
 *
 * The visibility guard (hidden at `undefined`/`0`) and its layout wrapper both
 * live here — the one place that spells it — rather than split with a second
 * copy in `routes/index.tsx`'s caller.
 */
export function ReviewChip({
  pathId,
  dueCount,
}: {
  pathId: string;
  dueCount: number | undefined;
}) {
  if (dueCount === undefined || dueCount === 0) return null;

  return (
    <div className="mt-3 lg:mt-0 lg:shrink-0">
      <Link
        to="/review"
        search={{ path: pathId }}
        data-testid="review-chip"
        className="inline-flex items-center rounded-full bg-teal px-2 py-0.5 text-xs font-semibold text-night"
      >
        Review {dueCount}
      </Link>
    </div>
  );
}
