// Home's door into Flow (flow TDD mock screen 01, pin 1): a second door
// directly under Continue, not a mode toggle on it. Continue answers "pick up
// where I left off"; Flow is a different promise ("I have a stretch of
// time"), so it gets its own row rather than crowding Continue's.

import { Link } from "@tanstack/react-router";
import type { PathSummary } from "../../lib/api";

/**
 * `undefined`/no flowable path renders nothing — the same decoration contract
 * `ContinueCard` holds. "Flowable" is exactly `pickResumeTarget`'s own test
 * (`next_lesson !== null`): a path with nothing available has nothing for a
 * flow to open either, so a learner with only finished/refused/failed paths
 * sees no door.
 */
export function FlowDoor({ paths }: { paths: PathSummary[] | undefined }) {
  const hasFlowable = paths?.some((path) => path.next_lesson !== null) ?? false;
  if (!hasFlowable) return null;

  return (
    <Link
      to="/flow/new"
      data-testid="flow-door"
      className="mt-2 flex min-h-14 items-center gap-3 rounded-lg px-5 py-3 text-porcelain no-underline transition-colors hover:bg-surface lg:px-6"
    >
      <div className="min-w-0 flex-1">
        <p className="text-[15px] font-semibold">Start a flow</p>
        <p className="mt-0.5 text-sm leading-5 text-mist">
          A few lessons, back to back. You pick how many.
        </p>
      </div>
      <svg
        viewBox="0 0 16 16"
        width="14"
        height="14"
        fill="none"
        aria-hidden="true"
        className="shrink-0 text-slate"
      >
        <path
          d="M6 3.5L10.5 8 6 12.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </Link>
  );
}
