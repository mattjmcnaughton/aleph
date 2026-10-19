import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ProgressSummary } from "../lib/api";
import { StreakLine } from "./streak-line";

// The one line above "Your paths" (PRD §3, Streaks TDD §8). A plain
// presentational component — no query, no flag — so these are driven directly
// with the shapes `routes/index.tsx` can hand it: `undefined` (loading, flag
// off, or a failed GET, TDD §5.4's last row) and every `ProgressSummary` shape
// the copy branches on.

function summary(overrides: Partial<ProgressSummary> = {}): ProgressSummary {
  return {
    today: "2026-08-02",
    current_streak: 0,
    best_streak: 0,
    completed_today: 0,
    activity: [],
    paths: [],
    ...overrides,
  };
}

describe("StreakLine", () => {
  it("renders nothing for undefined — decoration must fail as decoration (TDD §5.4)", () => {
    render(<StreakLine summary={undefined} />);
    expect(screen.queryByTestId("streak-line")).toBeNull();
  });

  it("at zero: an invitation, no flame, no number (PRD §3)", () => {
    render(<StreakLine summary={summary({ current_streak: 0 })} />);

    const line = screen.getByTestId("streak-line");
    expect(line.textContent).toBe("Complete a lesson to start a streak");
    expect(line.textContent).not.toMatch(/🔥/);
  });

  it("at one: the flame, the count, and today's lesson", () => {
    render(
      <StreakLine summary={summary({ current_streak: 1, best_streak: 1, completed_today: 1 })} />,
    );

    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 1-day streak · 1 lesson today");
    // best never shows when it equals current — naming it would be noise.
    expect(screen.queryByTestId("streak-best")).toBeNull();
  });

  it("at many, with more than one lesson today: pluralises the count", () => {
    render(
      <StreakLine summary={summary({ current_streak: 5, best_streak: 5, completed_today: 2 })} />,
    );

    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 5-day streak · 2 lessons today");
  });

  it("with best above current: names the aim, in `mist`, never the flame's colour (TDD §14 R5)", () => {
    render(
      <StreakLine summary={summary({ current_streak: 5, best_streak: 12, completed_today: 0 })} />,
    );

    const line = screen.getByTestId("streak-line");
    expect(line.textContent).toBe("🔥 5-day streak · best 12");
    const best = screen.getByTestId("streak-best");
    expect(best.className).toContain("text-mist");
    expect(best.className).not.toContain("text-teal");
  });

  it("names best AND today's count together, in the PRD's own order", () => {
    render(
      <StreakLine summary={summary({ current_streak: 5, best_streak: 12, completed_today: 1 })} />,
    );

    expect(screen.getByTestId("streak-line").textContent).toBe(
      "🔥 5-day streak · best 12 · 1 lesson today",
    );
  });
});
