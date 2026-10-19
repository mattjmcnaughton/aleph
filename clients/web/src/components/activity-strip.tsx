// The 49-day activity heatmap above the paths list (D12, Streaks TDD §8): a
// 7-row × 7-column week grid — the habagou heatmap, ported. 49 cells in one row
// is ~5px each on a 390px viewport; the week grid lands at ~14–16px, and the
// weekly rhythm ("never on Wednesdays") is legible, which a flat row hides.
//
// The window is 49 rather than 45 (TDD §15's open question, settled in
// `config.py`), so 7 rows × 7 columns is exactly full: every column is one
// whole week, and there is no leading pad — the rule that produced four blank
// cells is gone rather than merely tidied.

import type { ActivityCell } from "../lib/api";

const ROWS = 7;

type Intensity = "empty" | "dim" | "mid" | "bright";

/** Three teal intensities plus empty (D12): 1 lesson / 2–3 / 4+ / nothing. */
function intensityFor(count: number): Intensity {
  if (count === 0) return "empty";
  if (count === 1) return "dim";
  if (count <= 3) return "mid";
  return "bright";
}

const INTENSITY_CLASS: Record<Intensity, string> = {
  empty: "bg-surface",
  dim: "bg-teal-dim",
  mid: "bg-teal",
  bright: "bg-teal-bright",
};

/**
 * Parse a `YYYY-MM-DD` wire date (Streaks TDD §6) into a local midnight `Date`
 * — never `new Date(iso)`, which parses as UTC and can print the wrong weekday
 * for a learner west of it. Display-only: the streak math itself never touches
 * this file.
 */
function localDateFromISO(iso: string): Date {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day);
}

const WEEKDAY_LABEL = new Intl.DateTimeFormat(undefined, { weekday: "long" });
const DATE_LABEL = new Intl.DateTimeFormat(undefined, { month: "long", day: "numeric" });

/**
 * `count` is no longer lesson-specific (Phase 3 TDD D11): a review-only day is
 * marked `count = 1` too, so a per-cell readout of "1 lesson" would misname a
 * day the learner spent reviewing, not completing anything. Neither signal is
 * distinguishable from `count` alone — nor should a caller need to reach past
 * this component to explain that — so the label states plainly whether the
 * day was active, which is what the count is actually *for*, and states no
 * more than that. The summary `aria-label` above already reads "N active
 * days"; this brings the per-cell copy into line with it, keeping the date.
 */
function cellLabel(cell: ActivityCell): string {
  const date = localDateFromISO(cell.date);
  const when = `${WEEKDAY_LABEL.format(date)}, ${DATE_LABEL.format(date)}`;
  return cell.count === 0 ? `${when}: no activity` : `${when}: active`;
}

/**
 * The strip (D12): the window's cells laid out 7 rows tall, filling columns
 * left to right, oldest first. At the shipped window of 49
 * (`STREAK_ACTIVITY_WINDOW_DAYS`) that is exactly 7 columns of 7 — the grid is
 * full, with no pad cells at all.
 *
 * **Rows are weekday-consistent within a render**, which is the whole of what
 * "the weekly rhythm is legible" needs: a calendar day advances exactly one
 * array slot, and the grid is 7 rows tall, so every cell in a given row shares
 * one weekday and "never on Wednesdays" reads as a consistently empty row.
 * That does not hold *across* renders — the row-to-weekday mapping is
 * `(today.getDay() + 1 + row) mod 7`, so it rotates by one every day. Fine, and
 * deliberate: the grid carries no weekday labels, so nothing needs an absolute
 * Sun..Sat axis, and not anchoring to one is what keeps the geometry invariant
 * (an anchored strip would need 7 *or* 8 columns depending on the date).
 *
 * Nothing here hard-codes 49. A reconfigured window simply produces a different
 * number of columns, still 7 rows tall and still weekday-consistent per row —
 * it just stops being exactly full, which is a cosmetic loss rather than a
 * broken layout.
 *
 * `role="img"` with one summary `aria-label` — TDD §8 is explicit that a grid
 * of individually-announced cells is a screen-reader denial of service.
 * Per-cell `aria-label`s still carry the date + count (TDD §8), for anything
 * that reads them directly (a test, a title-on-hover affordance); `role="img"`
 * on the container is what stops a screen reader from walking them one by one.
 */
export function ActivityStrip({ activity }: { activity: ActivityCell[] | undefined }) {
  if (activity === undefined) return null;

  const activeDays = activity.filter((cell) => cell.count > 0).length;
  const summaryLabel = `Activity for the last ${activity.length} days: ${activeDays} active ${
    activeDays === 1 ? "day" : "days"
  }`;

  return (
    <div
      data-testid="activity-strip"
      role="img"
      aria-label={summaryLabel}
      className="mt-4 grid gap-1"
      style={{
        gridTemplateRows: `repeat(${ROWS}, minmax(0, 1fr))`,
        gridAutoFlow: "column",
        gridAutoColumns: "minmax(0, 1fr)",
      }}
    >
      {activity.map((cell) => (
        <span
          key={cell.date}
          data-testid="activity-cell"
          data-intensity={intensityFor(cell.count)}
          // The weekday a cell lands on — exposed so a test can assert the
          // per-row consistency documented above directly, rather than
          // re-deriving it from `aria-label` prose.
          data-weekday={localDateFromISO(cell.date).getDay()}
          aria-label={cellLabel(cell)}
          className={`h-3.5 w-3.5 rounded-sm ${INTENSITY_CLASS[intensityFor(cell.count)]}`}
        />
      ))}
    </div>
  );
}
