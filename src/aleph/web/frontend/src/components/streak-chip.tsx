// The neutral per-path chip inside each `PathRow` (PRD §4.3, Streaks TDD §8):
// `3-day`. Home-only, one surface (TDD §14 R6) — no path-view or sidebar
// counterpart, by owner's call.

/**
 * The path streak is a stat, not a game (PRD §4.3): with multiple paths a
 * learner naturally alternates between them — exactly the behaviour the
 * Breadth metric wants — and a per-path streak breaks every time they do.
 * Celebrating it would punish the product's own goal, so this chip is
 * deliberately quiet: `mist` on `elevated`, no flame, no colour escalation,
 * and hidden below 2 days (PRD §4.3) — at 0 or 1 it is not a streak yet, and
 * showing "1-day" beside every freshly-touched path would be noise on every
 * row, every day.
 *
 * `days` is the path's `current_streak` (Streaks TDD §6's `PathStreak`), or
 * absent when the summary carries no row for this path (D5: "absent means
 * zero"). The caller (`routes/index.tsx`) passes `0` for that case rather than
 * omitting the component, which is equivalent — `0 < 2` renders nothing either
 * way — but keeps the lookup at the call site a plain `?? 0`.
 */
export function StreakChip({ days }: { days: number }) {
  if (days < 2) return null;

  return (
    <span
      data-testid="streak-chip"
      className="inline-flex items-center rounded-full bg-elevated px-2 py-0.5 text-xs font-medium text-mist"
    >
      {days}-day
    </span>
  );
}
