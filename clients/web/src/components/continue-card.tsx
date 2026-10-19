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
 * The path to resume: the most recently worked one that still has somewhere to
 * go.
 *
 * `GET /paths` is ordered by last activity descending with never-worked paths
 * last, so "the first row with a resume target" *is* the answer and no sorting
 * is repeated client-side. `next_lesson` is null for a finished path, for a
 * refusal, for a failure, and for one whose outline has not landed yet — every
 * case where there is nothing to continue to — so this walks past all of them
 * without needing to know which is which.
 */
export function pickResumeTarget(paths: PathSummary[] | undefined): PathSummary | undefined {
  return paths?.find((path) => path.next_lesson !== null);
}

/**
 * `undefined` renders nothing, the same decoration contract `StreakLine` and
 * `DueTodayCard` hold: a learner with no paths, or none with a resume target,
 * sees no card rather than an empty one. The caller resolves the target with
 * `pickResumeTarget` so this component never sees the whole list.
 */
export function ContinueCard({ path }: { path: PathSummary | undefined }) {
  if (path === undefined || path.next_lesson === null) return null;

  const { completed_lessons: done, total_lessons: total } = path.progress;
  // A path with no completions is not being *continued*, it is being started —
  // and "Continue" over `0 of 48 complete` is the kind of small lie that makes
  // a screen feel like it is not reading the same data the learner is.
  const resuming = path.last_activity_at !== null;

  return (
    <Link
      to="/lessons/$lessonId"
      params={{ lessonId: path.next_lesson.id }}
      data-testid="continue-card"
      data-path-id={path.id}
      className="mt-6 flex items-center gap-4 rounded-lg border border-teal/40 bg-surface p-4 shadow-sm transition-colors hover:border-teal focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal"
    >
      <div className="min-w-0 flex-1">
        <p className="kicker text-teal-bright">{resuming ? "Continue" : "Start"}</p>
        <p
          data-testid="continue-card-lesson"
          className="mt-1.5 truncate text-lg font-semibold leading-snug"
        >
          {path.next_lesson.title}
        </p>
        <p data-testid="continue-card-path" className="mt-1 truncate text-sm text-mist">
          {path.title}
          {total > 0 ? ` · ${done} of ${total} complete` : null}
        </p>
      </div>
      <span aria-hidden="true" className="shrink-0 text-2xl leading-none text-teal">
        ›
      </span>
    </Link>
  );
}
