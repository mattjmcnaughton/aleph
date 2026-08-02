import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StreakChip } from "./streak-chip";

// The neutral per-path chip inside each `PathRow` (PRD §4.3, Streaks TDD §8):
// hidden below 2 days, plain `N-day` at 2+.

describe("StreakChip", () => {
  it("renders nothing at 0 days (a path absent from the summary, D5)", () => {
    render(<StreakChip days={0} />);
    expect(screen.queryByTestId("streak-chip")).toBeNull();
  });

  it("renders nothing at 1 day (PRD §4.3 — not a streak yet)", () => {
    render(<StreakChip days={1} />);
    expect(screen.queryByTestId("streak-chip")).toBeNull();
  });

  it("shows the plain count at 2 days, with no flame and no colour escalation", () => {
    render(<StreakChip days={2} />);

    const chip = screen.getByTestId("streak-chip");
    expect(chip.textContent).toBe("2-day");
    expect(chip.textContent).not.toMatch(/🔥/);
    expect(chip.className).toContain("text-mist");
    expect(chip.className).not.toContain("text-teal");
  });

  it("keeps counting past 2 days", () => {
    render(<StreakChip days={30} />);
    expect(screen.getByTestId("streak-chip").textContent).toBe("30-day");
  });
});
