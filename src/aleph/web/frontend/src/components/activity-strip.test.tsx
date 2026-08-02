import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ActivityCell } from "../lib/api";
import { ActivityStrip } from "./activity-strip";

// The 49-day heatmap (D12, Streaks TDD §8): a 7-row × 7-column grid — exactly
// full at the shipped window, so there are no pad cells — weekday-consistent
// per row, three teal intensities, `role="img"` with one summary label rather
// than a grid of individually-announced cells.

/** `STREAK_ACTIVITY_WINDOW_DAYS` cells, oldest first, ending at `today` — the
 *  wire's own shape (TDD §6). */
function buildActivity(counts: number[], today = "2026-08-02"): ActivityCell[] {
  const anchor = new Date(today);
  return counts.map((count, i) => {
    const date = new Date(anchor);
    date.setDate(date.getDate() - (counts.length - 1 - i));
    return { date: date.toISOString().slice(0, 10), count };
  });
}

/** The shipped window (`STREAK_ACTIVITY_WINDOW_DAYS`): 7×7, exactly full. */
const WINDOW_DAYS = 49;
const ZERO_WINDOW = buildActivity(Array.from({ length: WINDOW_DAYS }, () => 0));

describe("ActivityStrip", () => {
  it("renders nothing for undefined (loading, flag off, or a failed GET — TDD §5.4)", () => {
    render(<ActivityStrip activity={undefined} />);
    expect(screen.queryByTestId("activity-strip")).toBeNull();
  });

  it("[D12] renders one cell per window day and nothing else — 7×7, exactly full", () => {
    render(<ActivityStrip activity={ZERO_WINDOW} />);

    // 49 = 7 rows × 7 columns, so the grid is full with no pad. The pad rule
    // the 45-day window needed is gone, not merely producing zero cells.
    expect(screen.getAllByTestId("activity-cell")).toHaveLength(WINDOW_DAYS);
    expect(screen.queryAllByTestId("activity-cell-pad")).toHaveLength(0);
    expect(WINDOW_DAYS % 7).toBe(0);
  });

  it("[D12] rows are weekday-consistent: cells 7 apart read the same weekday", () => {
    render(<ActivityStrip activity={ZERO_WINDOW} />);

    const live = screen.getAllByTestId("activity-cell");
    // Every cell 7 positions later is exactly 7 calendar days later and lands
    // in the same grid row, so it must read the same weekday — which is what
    // makes a weekly rhythm legible (component doc comment). Checked across the
    // full run, not just one pair.
    for (let i = 0; i + 7 < live.length; i++) {
      expect(live[i].getAttribute("data-weekday")).toBe(live[i + 7].getAttribute("data-weekday"));
    }
  });

  it("[D12] intensity buckets: 0 empty, 1 dim, 2-3 mid, 4+ bright", () => {
    const counts = [0, 1, 2, 3, 4, 9, ...Array.from({ length: WINDOW_DAYS - 6 }, () => 0)];
    render(<ActivityStrip activity={buildActivity(counts)} />);

    const live = screen.getAllByTestId("activity-cell");
    expect(live[0].getAttribute("data-intensity")).toBe("empty");
    expect(live[1].getAttribute("data-intensity")).toBe("dim");
    expect(live[2].getAttribute("data-intensity")).toBe("mid");
    expect(live[3].getAttribute("data-intensity")).toBe("mid");
    expect(live[4].getAttribute("data-intensity")).toBe("bright");
    expect(live[5].getAttribute("data-intensity")).toBe("bright");
  });

  it("carries a per-cell aria-label with the date and count", () => {
    render(<ActivityStrip activity={buildActivity([2], "2026-08-02")} />);

    // A single-cell window still renders (defensive; the real contract is
    // always `STREAK_ACTIVITY_WINDOW_DAYS`) — the label is what's under test.
    const cell = screen.getByTestId("activity-cell");
    expect(cell.getAttribute("aria-label")).toMatch(/august 2/i);
    expect(cell.getAttribute("aria-label")).toMatch(/2 lessons/);
  });

  it("[TDD §8] is a single role=img with a summary label — not 49 announced cells", () => {
    const counts = [3, 3, ...Array.from({ length: WINDOW_DAYS - 2 }, () => 0)];
    render(<ActivityStrip activity={buildActivity(counts)} />);

    const strip = screen.getByRole("img", { name: /2 active days/i });
    expect(strip).toBe(screen.getByTestId("activity-strip"));
    // The individual cells are not exposed as separate accessible objects —
    // there is exactly one `img` in this subtree.
    expect(screen.getAllByRole("img")).toHaveLength(1);
  });
});
