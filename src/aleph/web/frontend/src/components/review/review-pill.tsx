// The `10 due` pill in the app bar (PRD §3, Phase 3 TDD §8) — the one piece of
// persistent navigation this phase adds, mounted in `app-header.tsx` on every
// route. Presentational only: `app-header.tsx` owns the query and the flag
// gate, this component only decides what to render for a given summary.

import { Link } from "@tanstack/react-router";
import type { ReviewSummary } from "../../lib/api";

/**
 * `summary` is whatever `reviewSummaryQueryOptions`'s query currently holds —
 * `undefined` on load, on a flag-off `skipToken` idle, or on a failed `GET
 * /reviews/summary`. This is **decoration on every route** (TDD §5.6's last
 * row, raised to the header): the caller never branches on `isError`, it just
 * hands this component whatever `.data` is, and rendering nothing here *is*
 * failing as decoration — the same contract `StreakLine` holds for the home
 * route, now extended to a component mounted everywhere.
 *
 * Hidden entirely at zero (PRD §3): a `0 due` pill would be a debt
 * notification, which the restraint list forbids in every other form too.
 */
export function ReviewPill({ summary }: { summary: ReviewSummary | undefined }) {
  if (summary === undefined || summary.due_count === 0) return null;

  return (
    <Link
      to="/review"
      data-testid="review-pill"
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-teal px-2.5 py-1 text-xs font-semibold text-night"
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-night/55" />
      {summary.due_count} due
    </Link>
  );
}
