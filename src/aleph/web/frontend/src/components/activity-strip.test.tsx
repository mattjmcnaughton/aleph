import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ActivityCell } from "../lib/api";
import { ActivityStrip } from "./activity-strip";

// The 45-day heatmap (D12, Streaks TDD §8): a 7-row × 7-column grid, weekday
// aligned, three teal intensities, `role="img"` with one summary label rather
// than 45 individually-announced cells.

/** 45 cells, oldest first, ending at `today` — the wire's own shape (TDD §6). */
function buildActivity(counts: number[], today = "2026-08-02"): ActivityCell[] {
  const anchor = new Date(today);
  return counts.map((count, i) => {
    const date = new Date(anchor);
    date.setDate(date.getDate() - (counts.length - 1 - i));
    return { date: date.toISOString().slice(0, 10), count };
  });
}

const ZERO_45 = buildActivity(Array.from({ length: 45 }, () => 0));

describe("ActivityStrip", () => {
  it("renders nothing for undefined (loading, flag off, or a failed GET — TDD §5.4)", () => {
    render(<ActivityStrip activity={undefined} />);
    expect(screen.queryByTestId("activity-strip")).toBeNull();
  });

  it("[D12] renders exactly 49 cells: 45 live + a fixed 4-cell leading pad", () => {
    render(<ActivityStrip activity={ZERO_45} />);

    const live = screen.getAllByTestId("activity-cell");
    const pad = screen.getAllByTestId("activity-cell-pad");
    expect(live).toHaveLength(45);
    expect(pad).toHaveLength(4);
    expect(live.length + pad.length).toBe(49);
  });

  it("[D12] weekday-aligned: live cells 7 apart land in the same row", () => {
    render(<ActivityStrip activity={ZERO_45} />);

    const live = screen.getAllByTestId("activity-cell");
    // Every cell 7 positions later is exactly 7 calendar days later, so it must
    // read the same weekday — the leading-pad rule's whole point (component
    // doc comment). Checked across the full run, not just one pair.
    for (let i = 0; i + 7 < live.length; i++) {
      expect(live[i].getAttribute("data-weekday")).toBe(live[i + 7].getAttribute("data-weekday"));
    }
  });

  it("[D12] intensity buckets: 0 empty, 1 dim, 2-3 mid, 4+ bright", () => {
    const counts = [0, 1, 2, 3, 4, 9, ...Array.from({ length: 39 }, () => 0)];
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
    // always 45) — the label is what's under test here.
    const cell = screen.getByTestId("activity-cell");
    expect(cell.getAttribute("aria-label")).toMatch(/august 2/i);
    expect(cell.getAttribute("aria-label")).toMatch(/2 lessons/);
  });

  it("[TDD §8] is a single role=img with a summary label — not 45 announced cells", () => {
    const counts = [3, 3, ...Array.from({ length: 43 }, () => 0)];
    render(<ActivityStrip activity={buildActivity(counts)} />);

    const strip = screen.getByRole("img", { name: /2 active days/i });
    expect(strip).toBe(screen.getByTestId("activity-strip"));
    // The individual cells are not exposed as separate accessible objects —
    // there is exactly one `img` in this subtree.
    expect(screen.getAllByRole("img")).toHaveLength(1);
  });
});
