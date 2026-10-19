// The auto-advance after Mark complete (flow TDD §5.3 step 5, D6, mock screen
// 03). Replaces `CompletedState` inside a flow (or sits beneath
// `PathCompleteCard` on a path-finishing completion, §9 "Path completion
// inside a flow" — paused either way). The card owns the count: it is the one
// place `FLOW_ADVANCE_SECONDS` actually ticks.

import { useEffect, useState } from "react";
import { FLOW_ADVANCE_SECONDS } from "../../lib/flow";

export function FlowAdvanceCard({
  nextTitle,
  nextPathTitle,
  startPaused,
  tutorOpen,
  onGoNow,
  onEnd,
}: {
  /** The lesson the count is opening. */
  nextTitle: string;
  /** Named beside the title when the next lesson is on a different path
   *  (interleave/random can land anywhere) — omitted (null) when it is the
   *  same path, matching the mock's "Reading an EXPLAIN plan · SQL
   *  performance" only naming the path when it is worth naming. */
  nextPathTitle: string | null;
  /** The count starts paused when this completion finished a path (D6) — the
   *  celebration deserves the tap, not a countdown racing it. A one-time
   *  initial value: the card remounts (`key={lessonId}`) each time a new
   *  completion mounts it, so there is nothing to resync mid-life. */
  startPaused: boolean;
  /** Paused for as long as the tutor rail is open (D6), reactively — not just
   *  at mount, since the learner can open the rail after the count starts. */
  tutorOpen: boolean;
  onGoNow: () => void;
  onEnd: () => void;
}) {
  const [remaining, setRemaining] = useState(FLOW_ADVANCE_SECONDS);
  const [manuallyPaused, setManuallyPaused] = useState(startPaused);
  const paused = manuallyPaused || tutorOpen;

  useEffect(() => {
    if (paused) return;
    const interval = setInterval(() => {
      setRemaining((current) => Math.max(current - 1, 0));
    }, 1000);
    return () => clearInterval(interval);
  }, [paused]);

  // A separate effect from the tick above: reaching zero fires `onGoNow`
  // exactly once, regardless of whether the zero came from the interval or
  // from `Go now` racing it to the same value.
  useEffect(() => {
    if (remaining <= 0) onGoNow();
  }, [remaining, onGoNow]);

  const fraction = remaining / FLOW_ADVANCE_SECONDS;

  return (
    <section
      data-testid="flow-advance"
      className="rounded-lg border border-teal/40 bg-teal/10 p-5 text-center shadow-sm"
    >
      <div className="relative mx-auto h-16 w-16">
        <svg viewBox="0 0 64 64" className="h-16 w-16 -rotate-90" aria-hidden="true">
          <circle
            cx="32"
            cy="32"
            r="28"
            fill="none"
            stroke="currentColor"
            strokeWidth="4"
            className="text-porcelain/10"
          />
          <circle
            cx="32"
            cy="32"
            r="28"
            fill="none"
            stroke="currentColor"
            strokeWidth="4"
            strokeLinecap="round"
            className={`text-teal ${paused ? "text-slate" : "transition-[stroke-dashoffset] duration-1000 ease-linear motion-reduce:transition-none"}`}
            strokeDasharray={175.93}
            strokeDashoffset={175.93 * (1 - fraction)}
          />
        </svg>
        <span
          data-testid="flow-advance-count"
          className="absolute inset-0 grid place-items-center text-xl font-semibold tabular-nums"
        >
          {remaining}
        </span>
      </div>

      <h2 className="mt-3 text-lg font-semibold text-porcelain">Lesson complete.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        Next: <span className="text-porcelain">{nextTitle}</span>
        {nextPathTitle ? ` · ${nextPathTitle}` : ""}
      </p>

      <div className="mt-5 grid grid-cols-2 gap-2">
        <button
          type="button"
          data-testid="flow-advance-go"
          onClick={onGoNow}
          className="rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright"
        >
          Go now
        </button>
        <button
          type="button"
          data-testid="flow-advance-pause"
          aria-pressed={manuallyPaused}
          onClick={() => setManuallyPaused((current) => !current)}
          className="rounded-md border border-faint px-4 py-3 text-sm font-medium text-mist transition-colors hover:text-porcelain"
        >
          {manuallyPaused ? "Resume" : "Pause"}
        </button>
      </div>

      {/* "End flow", not "End flow · back to your path" (flow-fix plan item
          4): `onEnd` is the route's `endFlow`, which always lands on the
          receipt from here — a completion has just been recorded, so
          `completed.length > 0` (§5.7). Matches the flow bar's own label. */}
      <button
        type="button"
        data-testid="flow-advance-end"
        onClick={onEnd}
        className="mt-4 text-sm text-mist transition-colors hover:text-porcelain"
      >
        End flow
      </button>
    </section>
  );
}
