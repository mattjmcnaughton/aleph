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
      <FlowSegments states={segments(flow, position)} />
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

export type Segment = "done" | "now" | "empty";

/** How far apart the receipt's cells light, and how long the first one waits. */
const SEG_LIGHT_STEP_MS = 180;
const SEG_LIGHT_LEAD_MS = 250;

/**
 * The segment strip itself — the bar's cells, and the receipt's
 * (`routes/flow.done.tsx`), which shows the finished strip one last time.
 *
 * `lightUp` is the receipt's celebration: each cell lights in turn, left to
 * right, and the last one lands with a pulse. Every cell's resting class is
 * its final colour, so the motion is a `motion-safe:` addition — under
 * `prefers-reduced-motion` the strip simply stands complete.
 */
export function FlowSegments({
  states,
  lightUp = false,
  testid,
}: {
  states: Segment[];
  lightUp?: boolean;
  testid?: string;
}) {
  const last = states.length - 1;
  return (
    <div className="flex flex-1 gap-1" aria-hidden="true" data-testid={testid}>
      {/* The strip is purely positional (done/now/empty cells with no
          identity of their own), so the index is the key's whole meaning. */}
      {states.map((state, index) => {
        const lead = SEG_LIGHT_LEAD_MS + index * SEG_LIGHT_STEP_MS;
        return (
          <i
            // biome-ignore lint/suspicious/noArrayIndexKey: see the comment above this map.
            key={index}
            className={`block h-1 flex-1 rounded-full ${
              state === "done" ? "bg-teal" : state === "now" ? "bg-teal-dim" : "bg-porcelain/10"
            } ${
              lightUp && state === "done"
                ? index === last
                  ? "motion-safe:animate-seg-last"
                  : "motion-safe:animate-seg-light"
                : ""
            }`}
            style={
              lightUp && state === "done"
                ? // `seg-last` is two animations; the pulse starts as the light
                  // finishes (a comma list delays each in turn).
                  { animationDelay: index === last ? `${lead}ms, ${lead + 300}ms` : `${lead}ms` }
                : undefined
            }
          />
        );
      })}
    </div>
  );
}

/**
 * The strip's cells (§5.7, flow-fix plan item 3): `done` for `0..k-1`
 * (`k = flow.completed.length`), `now` for cell `position - 1`, `empty` for
 * the rest of a known length. On the advance screen `position - 1 < k`, so
 * that cell already reads `done` — there is nothing left to mark `now` until
 * the learner is actually on the next lesson. Open-ended (`length === null`)
 * has no "rest" to draw, so it shows exactly `position` cells, capped to the
 * most recent 12.
 */
export function segments(flow: FlowRecord, position: number): Segment[] {
  const k = flow.completed.length;
  const nowIndex = position - 1;
  const count = flow.length ?? position;
  const cells: Segment[] = [];
  for (let i = 0; i < count; i++) {
    cells.push(i < k ? "done" : i === nowIndex ? "now" : "empty");
  }
  return flow.length === null ? cells.slice(-MAX_OPEN_ENDED_SEGMENTS) : cells;
}
