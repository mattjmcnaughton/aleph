// Shared display helpers for the analyst surfaces (Beats & Briefs, AL-530,
// Phase 6 TDD §8) — small, presentation-only functions with more than one
// call site, mirroring `lib/onboarding.ts`'s role for the path flow.

import type { Cadence } from "./api";

/**
 * The seven Anchor day choices, Python's Monday==0 convention (CONTEXT.md:
 * Anchor day; `AnchorWeekday` in `dtos/beats.py`) — `routes/beats.new.tsx`'s
 * "Reports on ▾ Monday" dropdown, in display order.
 */
export const ANCHOR_WEEKDAYS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
] as const;

/** Default Anchor day for a fresh deploy form — Monday, the PRD's own
 *  running example (`Reports on ▾ Monday`). */
export const DEFAULT_ANCHOR_WEEKDAY = 0;

/** Display label for a Cadence — capitalised, `standing-orders.tsx`'s own
 *  reading of it (`Weekly · EU AI regulation · …`, PRD §3). The only value
 *  this slice ships is `"weekly"` (TDD §4.11), but the switch keeps the
 *  fallback honest rather than assuming the literal. */
export function cadenceLabel(cadence: Cadence): string {
  switch (cadence) {
    case "weekly":
      return "Weekly";
    default: {
      const unhandled: never = cadence;
      return unhandled;
    }
  }
}

/**
 * Parse a `YYYY-MM-DD` wire date (a Brief's `published_on`) into a local
 * midnight `Date` — never `new Date(iso)`, which parses as UTC and can shift
 * the calendar day for a learner west of UTC (`card-row.tsx`'s and
 * `activity-strip.tsx`'s own rule, restated here for the rail).
 */
function localDateFromISO(iso: string): Date {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day);
}

/**
 * A rail row's date, e.g. `3 Aug` (PRD §3's own example: `Brief #7 · 3 Aug`).
 * The year appears only when it is not the current one —
 * `change-history-sheet.tsx`'s identical rule, restated here for the rail:
 * within this year the year is noise on a row that already reads newest
 * first.
 */
export function formatBriefDate(publishedOn: string, now: Date = new Date()): string {
  const when = localDateFromISO(publishedOn);
  const thisYear = when.getFullYear() === now.getFullYear();
  return when.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(thisYear ? {} : { year: "numeric" }),
  });
}

/**
 * "started 30s ago" (PRD §3's own example) — the home card's researching
 * copy. `startedAt` is a real instant (`research_started_at`, UTC), unlike
 * `published_on` above, so `new Date(iso)` is the correct parse here.
 * Coarse on purpose: the Beats list is never polled (TDD §7 — "nothing polls
 * the beats list"), so this number only moves when the list itself refetches
 * (a remount, a refocus), not every second.
 */
export function formatElapsed(startedAt: string, now: Date = new Date()): string {
  const start = new Date(startedAt);
  const seconds = Math.max(0, Math.round((now.getTime() - start.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
}
