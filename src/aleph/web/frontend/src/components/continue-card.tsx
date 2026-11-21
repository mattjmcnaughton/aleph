// "Continue" — the home screen's answer to the question it was not answering
// (design critique, theme "this is a shelf, not a workbench").
//
// Home showed eight paths, seven of them barely started, and offered no way to
// *resume* any of them: the two prominent actions were `Review` (a different
// activity) and `New path` (start a ninth). Picking up yesterday's work meant
// scanning eight near-identical rows, choosing one, opening it, and finding the
// available lesson — a decision plus two taps, every visit. This is one tap,
// and it is the first thing on the page.

import { Link } from "@tanstack/react-router";
import type { PathSummary } from "../lib/api";

/**
 * `undefined` renders nothing, the same decoration contract `StreakLine` and
 * `DueTodayCard` hold: a learner with no paths, or none with a resume target,
 * sees no card rather than an empty one. The caller looks up the server-selected
 * path so this component never sees the whole list.
 */
export function ContinueCard({ path }: { path: PathSummary | undefined }) {
  if (path === undefined || path.next_lesson === null) return null;

  const { completed_lessons: done, total_lessons: total } = path.progress;
  // A path with no completions is not being *continued*, it is being started —
  // and "Continue" over `0 of 48 complete` is the kind of small lie that makes
  // a screen feel like it is not reading the same data the learner is.
  const resuming = path.last_activity_at !== null;

  const percent = total === 0 ? 0 : Math.round((done / total) * 100);

  // The hero of home: the one thing a returning learner came to do. The whole
  // card is the link; the pill at its foot is drawn as the button it behaves
  // like, so the primary action on the page looks like one.
  return (
    <Link
      to="/lessons/$lessonId"
      params={{ lessonId: path.next_lesson.id }}
      data-testid="continue-card"
      data-path-id={path.id}
      className="group mt-6 block rounded-lg border border-divider bg-surface p-5 transition-colors hover:border-faint lg:p-6"
    >
      <p data-testid="continue-card-path" className="text-sm leading-5 text-mist">
        {path.title}
        {total > 0 ? ` · ${done} of ${total} complete` : null}
      </p>
      <p
        data-testid="continue-card-lesson"
        className="mt-2 font-serif text-[1.375rem] font-medium leading-snug tracking-[-0.01em] lg:text-2xl"
      >
        {path.next_lesson.title}
      </p>
      <div className="mt-5 flex items-center gap-4">
        <span
          aria-hidden="true"
          className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-porcelain/10"
        >
          <span className="block h-full rounded-full bg-teal" style={{ width: `${percent}%` }} />
        </span>
        <span className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-md bg-teal px-4 text-[15px] font-semibold text-night transition-colors group-hover:bg-teal-bright">
          {resuming ? "Continue" : "Start"}
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
            <path
              d="M3 8h9.5M8.5 4l4 4-4 4"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </div>
    </Link>
  );
}
