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
    <li data-testid="beat-rail-skipped" data-entry-id={entry.id} className="py-3.5 pl-6 pr-1">
      <p className="text-sm text-slate">{formatBriefDate(entry.published_on)}</p>
      <p className="mt-0.5 text-[15px] leading-6 text-mist">{entry.skip_line}</p>
    </li>
  );
}
