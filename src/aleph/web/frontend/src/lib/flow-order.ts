// Pure transitions over a `FlowRecord` (flow TDD §5.1-§5.3). Nothing here
// touches `sessionStorage`, the query client, or `Math.random` directly — the
// lesson route and `lib/flow.ts` own I/O and the real rng; this module is
// deterministic given its inputs, which is what makes it directly unit
// testable with a seeded rng (flow TDD §7).

import type { PathSummary } from "./api";
// Type-only: erased at compile time, so this does not create a runtime
// circular import even though `flow.ts` imports values back from this module.
import type { FlowCompletion, FlowOrder, FlowRecord } from "./flow";

/**
 * The scoped paths a flow can still draw from (flow TDD §5.1): kept where
 * `status === "ready" && next_lesson !== null`, in **scope order** (the order
 * the learner picked them in `scope`), not `summaries`' order — interleave and
 * serial both walk scope order, so that order has to survive filtering.
 *
 * A Beat never appears here at all: the setup sheet reads `GET /paths` only
 * (flow TDD §5.1), so there is nothing to exclude — a Beat was never in the
 * candidate list to begin with.
 */
export function eligiblePaths(summaries: PathSummary[], scope: string[]): PathSummary[] {
  const byId = new Map(summaries.map((path) => [path.id, path]));
  const result: PathSummary[] = [];
  for (const pathId of scope) {
    const path = byId.get(pathId);
    if (path && path.status === "ready" && path.next_lesson !== null) {
      result.push(path);
    }
  }
  return result;
}

/** `N left` on the setup sheet and the receipt's ledger (flow TDD §5.1). */
export function lessonsLeft(path: PathSummary): number {
  return path.progress.total_lessons - path.progress.completed_lessons;
}

export interface PickedNext {
  next: { lessonId: string; pathId: string };
  cursor: number;
}

/**
 * Draw the next lesson (flow TDD §5.2). `eligible` must already be `flow.paths
 * ∩` {ready, has a next lesson}, in scope order — callers get that from
 * `eligiblePaths(summaries, flow.paths)`; this function does not re-filter.
 *
 * `null` means the scope is dry — every caller's cue to end the flow with
 * `endedReason: "dry"` rather than treat it as an error.
 */
export function pickNext(
  flow: FlowRecord,
  eligible: PathSummary[],
  rng: () => number,
): PickedNext | null {
  if (eligible.length === 0) return null;

  switch (flow.order) {
    case "interleave": {
      // Starting at `flow.cursor`, the first path in scope order (wrapping)
      // that is eligible; the new cursor is one past it, so the next draw
      // resumes where this one left off rather than restarting the scan.
      const scopeLength = flow.paths.length;
      for (let offset = 0; offset < scopeLength; offset++) {
        const scopeIndex = (flow.cursor + offset) % scopeLength;
        const pathId = flow.paths[scopeIndex];
        const path = eligible.find((candidate) => candidate.id === pathId);
        if (path?.next_lesson) {
          return {
            next: { lessonId: path.next_lesson.id, pathId: path.id },
            cursor: (scopeIndex + 1) % scopeLength,
          };
        }
      }
      return null;
    }
    case "serial": {
      // Always the first eligible path in scope order — a path just drawn
      // from stays the pick until it runs dry, because it is still first.
      // `flow.cursor` plays no part in this rule; it is recorded only as a
      // snapshot of which scope slot is live.
      for (let scopeIndex = 0; scopeIndex < flow.paths.length; scopeIndex++) {
        const pathId = flow.paths[scopeIndex];
        const path = eligible.find((candidate) => candidate.id === pathId);
        if (path?.next_lesson) {
          return { next: { lessonId: path.next_lesson.id, pathId: path.id }, cursor: scopeIndex };
        }
      }
      return null;
    }
    case "random": {
      // Uniform draw, excluding `flow.current`'s path when a second eligible
      // path exists — the anti-repeat rule (D3). With exactly one eligible
      // path left, that same path is drawn again; that is the scope running
      // down to it, not a bug (flow TDD §5.2).
      let pool = eligible;
      if (eligible.length > 1 && flow.current !== null) {
        const withoutCurrent = eligible.filter(
          (candidate) => candidate.id !== flow.current?.pathId,
        );
        if (withoutCurrent.length > 0) pool = withoutCurrent;
      }
      const draw = pool[Math.floor(rng() * pool.length)];
      if (!draw?.next_lesson) return null;
      return { next: { lessonId: draw.next_lesson.id, pathId: draw.id }, cursor: flow.cursor };
    }
    default: {
      // Exhaustiveness guard — `FlowOrder` has exactly three members, so this
      // is unreachable at the type level; kept so a fourth order added to the
      // type without a case here fails loudly instead of drawing nothing.
      const _exhaustive: never = flow.order;
      throw new Error(`pickNext: unhandled order ${_exhaustive as FlowOrder}`);
    }
  }
}

/**
 * The advance (flow TDD §5.3, steps 1-5), as one pure transition. The caller
 * (the lesson route) does the I/O this needs first — appending the just-ended
 * lesson's outcome into `completion`, and awaiting a *fresh*
 * `pathsListQueryOptions` fetch into `freshSummaries` (§9: "stale
 * `next_lesson` after completion" — the cache's invalidated copy may not have
 * refetched yet, so this function is never handed a possibly-stale cache
 * read) — then writes whatever record this returns.
 *
 * Order of preference for the next lesson: the pre-drawn look-ahead
 * (`flow.next`, D8) if it is still eligible against `freshSummaries` (§9:
 * "the pre-drawn `next` can go stale" if the learner finished it from another
 * tab), otherwise a fresh `pickNext`. `flow.next` is routinely `null` here —
 * a look-ahead that would have landed on `flow.current`'s own path is never
 * stored in the first place (D8's reason for a look-ahead is a *different*
 * path; same-path is backend Prefetch (+N)'s job), so this falls through to
 * `pickNext` for exactly that case with no extra branch needed. Ends the flow
 * (`endedReason`) on reaching `length` or running the scope dry; never on
 * anything else.
 */
export function advanceRecord(
  flow: FlowRecord,
  completion: FlowCompletion,
  freshSummaries: PathSummary[],
  rng: () => number,
): FlowRecord {
  const completed = [...flow.completed, completion];

  if (flow.length !== null && completed.length >= flow.length) {
    return { ...flow, completed, current: null, next: null, endedReason: "length" };
  }

  const eligible = eligiblePaths(freshSummaries, flow.paths);
  const staleNext = flow.next;
  const nextStillEligible =
    staleNext !== null &&
    eligible.some(
      (path) => path.id === staleNext.pathId && path.next_lesson?.id === staleNext.lessonId,
    );

  const picked =
    staleNext !== null && nextStillEligible
      ? { next: staleNext, cursor: flow.cursor }
      : pickNext(flow, eligible, rng);

  if (picked === null) {
    return { ...flow, completed, current: null, next: null, endedReason: "dry" };
  }
  return { ...flow, completed, current: picked.next, next: null, cursor: picked.cursor };
}
