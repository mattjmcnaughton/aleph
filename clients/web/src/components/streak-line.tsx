// The one line above "Your paths" (PRD §3, Streaks TDD §8):
//   🔥 5-day streak · best 12 · 1 lesson today
// Home-only (TDD §14 R6) — this is the sole caller of `progressSummaryQueryOptions`
// on any route besides `lessons.$lessonId.tsx`'s D10 patch.

import type { ProgressSummary } from "../lib/api";

/**
 * Takes the whole summary rather than three numbers so a caller can pass
 * `undefined` on load, on a flag-off `skipToken` idle, or on a failed
 * `GET /progress/summary` — this line is **decoration** on the home screen
 * (TDD §5.4's last row: a failed summary query must never prevent the paths
 * list from rendering), and rendering nothing here *is* failing as decoration.
 * `routes/index.tsx` never branches on `progressQuery.isError` itself; it just
 * hands this component whatever `progressQuery.data` currently is.
 */
export function StreakLine({ summary }: { summary: ProgressSummary | undefined }) {
  if (summary === undefined) return null;

  const { current_streak: current, best_streak: best, completed_today: completedToday } = summary;

  if (current === 0) {
    // An invitation, never a scold (PRD §3): no flame, no number at zero.
    // `current === 0` also means nothing was completed today (a same-day
    // completion always puts today inside the run), so there is no "0 lessons
    // today" clause to suppress here — the domain guarantees it away.
    return (
      <p data-testid="streak-line" className="text-sm leading-6 text-mist">
        Complete a lesson to start a streak
      </p>
    );
  }

  // `best` renders only when it exceeds `current` (TDD §14 R5, PRD §3): naming
  // an equal best beside the current streak is noise, and naming a bigger one
  // beside a broken streak is the one place this feature could read as a
  // scold. So it is stated as an aim — `mist`, never the flame's teal.
  const showBest = best > current;

  return (
    <p data-testid="streak-line" className="text-sm leading-6 text-porcelain">
      <span aria-hidden="true">🔥</span> {current}-day streak
      {showBest ? (
        <span data-testid="streak-best" className="text-mist">
          {" "}
          · best {best}
        </span>
      ) : null}
      {completedToday > 0
        ? ` · ${completedToday} ${completedToday === 1 ? "lesson" : "lessons"} today`
        : null}
    </p>
  );
}
