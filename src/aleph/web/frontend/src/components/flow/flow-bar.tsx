// The strip under the app bar saying where a learner is in a flow (flow TDD
// §5.7, mock screen 03). Sits above `Breadcrumbs`, sticky under
// `--app-header-h` the same way the mock's `.flowbar` sits under its appbar.

import type { FlowRecord } from "../../lib/flow";

/** Capped at 12 cells for an open-ended flow, showing the most recent 12 —
 *  the segment strip is a "where am I" glance, not an infinite ledger. */
const MAX_OPEN_ENDED_SEGMENTS = 12;

export function FlowBar({
  flow,
  position,
  onEndFlow,
}: {
  flow: FlowRecord;
  /** 1-based: `flow.completed.length`, plus one unless this lesson *is* the
   *  last completion (the advance screen) — computed by the route (flow-fix
   *  plan item 3, TDD §5.4/§5.7). Position-based, not `completed.length`
   *  directly, so the bar reads "1 of 3" on the very first lesson instead of
   *  "0 of 3". */
  position: number;
  onEndFlow: () => void;
}) {
  const n = flow.length;
  const label = n === null ? `Flow · lesson ${position}` : `Flow · ${position} of ${n}`;

  return (
    <div
      data-testid="flow-bar"
      className="sticky top-[var(--app-header-h)] z-20 -mx-4 mb-4 flex items-center gap-2.5 border-b border-divider bg-night px-4 py-2 lg:-mx-10 lg:px-10"
    >
      <span className="whitespace-nowrap font-mono text-[11.5px] font-semibold tabular-nums text-teal-bright">
        {label}
      </span>
      <div className="flex flex-1 gap-1" aria-hidden="true">
        {/* The strip is purely positional (done/now/empty cells with no
            identity of their own), so the index is the key's whole meaning. */}
        {segments(flow, position).map((state, index) => (
          <i
            // biome-ignore lint/suspicious/noArrayIndexKey: see the comment above this map.
            key={index}
            className={`block h-1 flex-1 rounded-full ${
              state === "done" ? "bg-teal" : state === "now" ? "bg-teal-dim" : "bg-porcelain/10"
            }`}
          />
        ))}
      </div>
      <button
        type="button"
        data-testid="flow-bar-end"
        onClick={onEndFlow}
        className="whitespace-nowrap text-[11.5px] text-slate transition-colors hover:text-porcelain"
      >
        End flow
      </button>
    </div>
  );
}

type Segment = "done" | "now" | "empty";

/**
 * The strip's cells (§5.7, flow-fix plan item 3): `done` for `0..k-1`
 * (`k = flow.completed.length`), `now` for cell `position - 1`, `empty` for
 * the rest of a known length. On the advance screen `position - 1 < k`, so
 * that cell already reads `done` — there is nothing left to mark `now` until
 * the learner is actually on the next lesson. Open-ended (`length === null`)
 * has no "rest" to draw, so it shows exactly `position` cells, capped to the
 * most recent 12.
 */
function segments(flow: FlowRecord, position: number): Segment[] {
  const k = flow.completed.length;
  const nowIndex = position - 1;
  const count = flow.length ?? position;
  const cells: Segment[] = [];
  for (let i = 0; i < count; i++) {
    cells.push(i < k ? "done" : i === nowIndex ? "now" : "empty");
  }
  return flow.length === null ? cells.slice(-MAX_OPEN_ENDED_SEGMENTS) : cells;
}
