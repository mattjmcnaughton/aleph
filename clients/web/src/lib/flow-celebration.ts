// Whether the receipt (`routes/flow.done.tsx`) throws its celebration.
//
// Two rules, mirroring the path seal's (`components/path-complete.tsx`):
//
// * **Earned, not congratulated.** Only a flow that reached its length is
//   celebrated. *Flow ended* and *Flow ran dry* get the plain receipt: the
//   learner stopped, or the paths did — either way the contract set at the
//   door ("5 of 5") was not what happened, and a fanfare would say it was.
// * **Once, on the arrival that earned it.** The record stays in
//   `sessionStorage` while the learner is on the receipt and the setup sheet
//   (flow TDD D5), so a *Go again* → back, or a refresh, would replay the
//   motion. This module remembers the last flow celebrated — keyed on
//   `startedAt`, which a new flow always rewrites — so the same flow's receipt
//   stands still the second time. Module memory, not storage: a genuinely new
//   tab is a genuinely new arrival, and a tab reload is rare enough to accept.

import type { FlowRecord } from "./flow";

let lastCelebratedStartedAt: string | null = null;

/** True when this flow earned the celebration and has not yet had it. */
export function shouldCelebrateFlow(flow: FlowRecord): boolean {
  return flow.endedReason === "length" && flow.startedAt !== lastCelebratedStartedAt;
}

/** Records that this flow's receipt has thrown its celebration. */
export function markFlowCelebrated(flow: FlowRecord): void {
  lastCelebratedStartedAt = flow.startedAt;
}

/** Test seam: forget every celebration, so one test's receipt cannot silence the next's. */
export function resetFlowCelebrations(): void {
  lastCelebratedStartedAt = null;
}
