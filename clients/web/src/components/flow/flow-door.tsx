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
      className="mt-3 flex items-center gap-3 rounded-lg border border-divider bg-transparent px-4 py-3 text-porcelain no-underline transition-colors hover:border-teal-dim focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal"
    >
      <span
        aria-hidden="true"
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-teal/10 text-teal-bright"
      >
        <svg
          viewBox="0 0 16 16"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M2 8h9M8 4l4 4-4 4" />
          <path d="M2 3.5h4M2 12.5h4" opacity={0.5} />
        </svg>
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">Start a flow</p>
        <p className="mt-0.5 truncate text-xs text-mist">
          A few lessons, back to back. You pick how many.
        </p>
      </div>
      <span aria-hidden="true" className="shrink-0 text-lg text-slate">
        ›
      </span>
    </Link>
  );
}
