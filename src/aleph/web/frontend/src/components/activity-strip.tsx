// The 45-day activity heatmap above the paths list (D12, Streaks TDD §8): a
// 7-row × 7-column week grid — the habagou heatmap, ported. 45 cells in one row
// is ~6px each on a 390px viewport; the week grid lands at ~14–16px, and the
// weekly rhythm ("never on Wednesdays") is legible, which a flat row hides.

import type { ActivityCell } from "../lib/api";

/** D12: 45 live days rendered inside a 49-cell (7×7) grid. */
const CELLS_TOTAL = 49;
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

function cellLabel(cell: ActivityCell): string {
  const date = localDateFromISO(cell.date);
  const when = `${WEEKDAY_LABEL.format(date)}, ${DATE_LABEL.format(date)}`;
  return cell.count === 0
    ? `${when}: no lessons`
    : `${when}: ${cell.count} ${cell.count === 1 ? "lesson" : "lessons"}`;
}

/**
 * The strip (D12): exactly 49 cells laid out 7 rows tall, filling columns
 * left to right, oldest first. The backend always sends exactly 45 entries
 * (Streaks TDD §6), so the **leading pad is a fixed 4 cells** — `CELLS_TOTAL -
 * activity.length` — never computed from today's real weekday. That is
 * deliberate, not an approximation: because the pad count is constant and a
 * calendar day advances exactly one array slot at a time, `row = index % 7`
 * lands on the same weekday in every column *within a single render* —
 * which is all "the weekly rhythm is legible" needs, since a rhythm only has
 * to hold still long enough for one look at the grid. It is not stable
 * *across* renders: the row-to-weekday mapping is `(today.getDay() + 1 +
 * row) mod 7`, so it rotates by one every day. That's fine — the grid is
 * deliberately not anchored to an absolute Sun..Sat axis (there are no
 * weekday labels, so nothing needs one), and giving up that anchoring is
 * exactly what buys the fixed pad and the invariant 7×7 geometry. (An
 * alignment computed from today's real weekday would need 7 *or 8* columns
 * depending on the date, which is the variable geometry D12's fixed 49-cell
 * grid exists to avoid.)
 *
 * `role="img"` with one summary `aria-label` — TDD §8 is explicit that 45
 * individually-announced cells is a screen-reader denial of service. Per-cell
 * `aria-label`s still carry the date + count (TDD §8), for anything that reads
 * them directly (a test, a title-on-hover affordance); `role="img"` on the
 * container is what stops a screen reader from walking them one by one.
 */
export function ActivityStrip({ activity }: { activity: ActivityCell[] | undefined }) {
  if (activity === undefined) return null;

  const pad = Math.max(CELLS_TOTAL - activity.length, 0);
  const cells: Array<ActivityCell | null> = [
    ...Array.from({ length: pad }, () => null),
    ...activity,
  ];

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
      {cells.map((cell, index) =>
        cell === null ? (
          <span
            // biome-ignore lint/suspicious/noArrayIndexKey: pad cells carry no date to key on, and their position within a render never changes.
            key={`pad-${index}`}
            data-testid="activity-cell-pad"
            className="h-3.5 w-3.5 rounded-sm bg-transparent"
          />
        ) : (
          <span
            key={cell.date}
            data-testid="activity-cell"
            data-intensity={intensityFor(cell.count)}
            // The row a live cell lands in (Streaks TDD §8's leading-pad rule,
            // documented above) — exposed so a test can assert the alignment
            // directly rather than re-deriving it from `aria-label` prose.
            data-weekday={localDateFromISO(cell.date).getDay()}
            aria-label={cellLabel(cell)}
            className={`h-3.5 w-3.5 rounded-sm ${INTENSITY_CLASS[intensityFor(cell.count)]}`}
          />
        ),
      )}
    </div>
  );
}
