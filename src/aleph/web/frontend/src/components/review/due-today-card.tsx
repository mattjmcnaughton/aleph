// Home's *Due today* card (PRD §3, Phase 3 TDD §8): `10 cards · ~4 min`, the
// `Review` action, and a one-line provenance breakdown. Rendered by
// `routes/index.tsx` above the path list and below the streak line.
//
// Carries no `/cards` door of its own (unlike an earlier draft of this
// component): this card hides outright at zero due (below), and a learner
// with 200 kept cards and none due today is exactly the learner who most
// wants to browse them — the one day this card renders nothing at all. That
// door now lives on `routes/index.tsx` itself, gated only on the `flashcards`
// flag rather than on `summary`/`due_count`, so it survives a quiet day (AL-410
// review finding 1). One door, not two: a duplicate "Your cards" link here,
// next to that one, would just be noise on the one screen where both showed.

import { Link } from "@tanstack/react-router";
import type { ReviewSummary } from "../../lib/api";
import { PRIMARY_CTA_BASE } from "../state-card";

/**
 * `summary` follows the same decoration contract as `StreakLine`/`ReviewPill`:
 * `undefined` on load, flag off, or a failed `GET /reviews/summary` all render
 * nothing, and `routes/index.tsx` never branches on `isError` to get there.
 *
 * Hidden at zero too, by the same restraint PRD §3 states for the pill: a
 * `0 cards` card with a disabled-in-spirit `Review` action is not an
 * invitation, it is empty chrome, so there is nothing to show once the day's
 * queue is empty.
 *
 * `pathTitles` resolves `paths[].path_id` to a display name for the
 * breakdown line (`ReviewSummaryResponse.paths` carries counts only, never
 * titles — TDD §6). Built by the caller from the already-fetched paths list,
 * the same lookup shape `routes/index.tsx` already builds for `pathStreaks`.
 * A path id the map has no title for (the list hasn't loaded, or raced the
 * summary) is simply left out of the sentence rather than rendering "undefined".
 */
export function DueTodayCard({
  summary,
  pathTitles,
}: {
  summary: ReviewSummary | undefined;
  pathTitles: Map<string, string>;
}) {
  if (summary === undefined || summary.due_count === 0) return null;

  const breakdown = provenanceLine(summary, pathTitles);

  return (
    <div
      data-testid="due-today-card"
      className="mt-6 rounded-lg border border-teal/40 bg-surface p-4 shadow-sm"
    >
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <p className="kicker text-teal-bright">Due today</p>
          <p className="mt-1.5 text-3xl font-semibold leading-none tracking-tight text-teal-bright">
            {summary.due_count} {summary.due_count === 1 ? "card" : "cards"}
          </p>
        </div>
        <span className="text-xs text-mist">~{summary.estimated_minutes} min</span>
      </div>

      {breakdown ? <p className="mt-2.5 text-sm leading-6 text-mist">{breakdown}</p> : null}

      <Link to="/review" data-testid="due-today-review" className={`mt-3.5 ${PRIMARY_CTA_BASE}`}>
        Review
      </Link>
    </div>
  );
}

/** "Across every path. 3 from Learn TypeScript, 7 from SQL performance." —
 *  the mock's own line — or, with a single path represented, just its clause. */
function provenanceLine(summary: ReviewSummary, pathTitles: Map<string, string>): string | null {
  const named = summary.paths
    .map((path) => ({ title: pathTitles.get(path.path_id), count: path.due_count }))
    .filter((path): path is { title: string; count: number } => path.title !== undefined);
  if (named.length === 0) return null;

  const clauses = named.map((path) => `${path.count} from ${path.title}`).join(", ");
  return named.length > 1 ? `Across every path. ${clauses}.` : `${clauses}.`;
}
