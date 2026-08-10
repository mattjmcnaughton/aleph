import type { SkippedEntry } from "../lib/api";
import { formatBriefDate } from "../lib/beats";

/**
 * A Skipped period's rail row (CONTEXT.md: Skipped, PRD §4.6) — quiet and
 * legible, not an error: `mist` text, a date, one line. **No flame, no
 * badge, no retry affordance, no error styling** — the same neutral tone
 * `routes/index.tsx`'s ordinary path row uses, never the danger/iris
 * treatment a failure or refusal gets. It must not read as a failure,
 * because it isn't one — Skipped means the analyst found nothing, and a
 * quiet period is the feature working correctly.
 */
export function SkippedRow({ entry }: { entry: SkippedEntry }) {
  return (
    <li
      data-testid="beat-rail-skipped"
      data-entry-id={entry.id}
      className="rounded-lg border border-divider bg-surface px-4 py-3"
    >
      <p className="font-mono text-[11px] uppercase tracking-kicker text-slate">
        {formatBriefDate(entry.published_on)}
      </p>
      <p className="mt-1 text-sm leading-6 text-mist">{entry.skip_line}</p>
    </li>
  );
}
