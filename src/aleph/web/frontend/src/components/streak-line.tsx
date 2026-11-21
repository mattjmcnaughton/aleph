// The one line above "Your paths" (PRD §3, Streaks TDD §8):
//   (flame) 5-day streak · best 12 · 1 lesson today
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

  const today =
    completedToday > 0
      ? `${completedToday} ${completedToday === 1 ? "lesson" : "lessons"} today`
      : null;

  // Two lines to the eye, one sentence to a screen reader and to the tests:
  // the separators that would start or split a visual line are `sr-only`, so
  // the text still reads "5-day streak · best 12 · 1 lesson today".
  return (
    <p data-testid="streak-line" className="leading-6 text-porcelain">
      <span className="block whitespace-nowrap text-base font-semibold">
        <FlameIcon />
        {current}-day streak
      </span>
      {showBest || today ? (
        <span className="block text-sm text-mist first-letter:uppercase">
          {showBest ? (
            <span data-testid="streak-best" className="text-mist">
              <span className="sr-only"> · </span>
              best {best}
            </span>
          ) : null}
          {today ? (
            <>
              <span className={showBest ? undefined : "sr-only"}> · </span>
              {today}
            </>
          ) : null}
        </span>
      ) : null}
    </p>
  );
}

/**
 * The streak's flame (CONTEXT: the Daily streak is "the one with the flame").
 * Drawn rather than the emoji: an emoji renders in each platform's own
 * cartoon style and was the one full-colour sticker on the page. `ember` is
 * used for this and nothing else.
 */
function FlameIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="15"
      height="15"
      aria-hidden="true"
      className="mr-1.5 inline-block -translate-y-px text-ember"
    >
      <path
        fill="currentColor"
        d="M8.4 1.2c.3 2-.6 3-1.7 4.1C5.5 6.500 4 7.900 4 10.200 4 12.700 5.800 14.500 8 14.500s4-1.800 4-4.300c0-1.400-.6-2.600-1.400-3.600-.2 1-.7 1.600-1.300 1.900.3-2.500-.2-5-0.900-7.300z"
      />
    </svg>
  );
}
