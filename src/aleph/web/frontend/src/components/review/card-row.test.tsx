import { describe, expect, it } from "vitest";
import { formatDueLabel } from "./card-row";

// `formatDueLabel` alone — the pure due-date label AL-410's product call #2
// puts on a card row (`rung` ships on the DTO but is never rendered). No
// render here: `CardRow` itself pulls in `CardSource`'s `<Link>`, which needs
// a router context the way `review-card.tsx` does (`draft-list.test.tsx`'s own
// note on why Link-bearing review components are covered end to end instead,
// in `src/app/flashcards-cards.test.tsx`) — this file is the pure-function
// half that does not need any of that.

// A fixed "now" rather than the real clock, so the label a run prints does
// not depend on which day the suite happens to execute.
const NOW = new Date(2026, 7, 4); // 2026-08-04, a Tuesday

function isoDaysFrom(base: Date, days: number): string {
  const shifted = new Date(base.getFullYear(), base.getMonth(), base.getDate() + days);
  const year = shifted.getFullYear();
  const month = String(shifted.getMonth() + 1).padStart(2, "0");
  const day = String(shifted.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

describe("formatDueLabel", () => {
  it("reads 'Due today' at zero days out", () => {
    expect(formatDueLabel(isoDaysFrom(NOW, 0), NOW)).toBe("Due today");
  });

  it("reads 'Due tomorrow' at exactly one day out", () => {
    expect(formatDueLabel(isoDaysFrom(NOW, 1), NOW)).toBe("Due tomorrow");
  });

  it("reads 'Due in N days' beyond tomorrow", () => {
    expect(formatDueLabel(isoDaysFrom(NOW, 3), NOW)).toBe("Due in 3 days");
    expect(formatDueLabel(isoDaysFrom(NOW, 30), NOW)).toBe("Due in 30 days");
  });

  it("reads 'Due yesterday' at exactly one day past", () => {
    expect(formatDueLabel(isoDaysFrom(NOW, -1), NOW)).toBe("Due yesterday");
  });

  it("reads 'Due N days ago' further in the past (an uncapped overdue card)", () => {
    expect(formatDueLabel(isoDaysFrom(NOW, -5), NOW)).toBe("Due 5 days ago");
  });
});
