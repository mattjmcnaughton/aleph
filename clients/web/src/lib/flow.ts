// The Flow record and its store (flow TDD §4, D4). A Flow is 100% client-side
// (D1) — no table, no route, no migration — so this module plus
// `lib/flow-order.ts` is the entire "backend" a Flow has.
//
// `sessionStorage`, not `localStorage`: a Flow is a Session (CONTEXT.md), and
// leaving *is* ending (D5) — a record that outlived the tab would resume
// itself hours later, which is exactly the bug D5 exists to rule out.
//
// Every storage access is wrapped in try/catch (D4's store rules, flow TDD
// §9 "storage blocked"): a browser with storage disabled or blocked must
// behave as "no flow", never throw. This module owns reading, writing and
// notifying; `lib/flow-order.ts` owns every pure transition over the record
// it reads and writes.

import { useSyncExternalStore } from "react";
import type { PathSummary } from "./api";
import { eligiblePaths, pickNext } from "./flow-order";

/** The flag that gates the whole surface (D10) — read with `useFeatureFlag`.
 *  Client-only: there is no route to gate `404`, unlike every flag before it. */
export const FLOW_FLAG = "flow";

/** The one sessionStorage key (D4), versioned so a future shape change can
 *  refuse to read an old record rather than guess at it. */
export const FLOW_STORAGE_KEY = "aleph.flow.v1";

/** Flow length choices (D13). `null` (on the record, and as `length` here
 *  when the learner picks "Until I stop") means open-ended. */
export const FLOW_LENGTHS = [3, 5, 8] as const;

/** The advance's count, in seconds (D6). */
export const FLOW_ADVANCE_SECONDS = 5;

/** Flow scope's order (CONTEXT.md: Flow scope; D3 — Interleave is default). */
export type FlowOrder = "interleave" | "random" | "serial";

/** One completed lesson's ledger row — the receipt's per-lesson line and the
 *  "k of n" count (`completed.length` is k). */
export interface FlowCompletion {
  lessonId: string;
  pathId: string;
  /** Lesson title, for the receipt's ledger — carried here so the receipt
   *  never has to re-fetch a lesson that may no longer be `current`. */
  title: string;
  /** The Quick check's outcome, for the receipt's "4/5 Quick checks" stat;
   *  `null` when the lesson had none to attempt. */
  outcome: "correct" | "incorrect" | null;
}

export interface FlowRecord {
  version: 1;
  /** ISO timestamp; receipt-only, never used to decide anything. */
  startedAt: string;
  /** Flow length: a count, or `null` for "until I stop". */
  length: number | null;
  order: FlowOrder;
  /** Flow scope: path ids in the order the learner picked them. */
  paths: string[];
  /** interleave/serial: index into `paths` of the path to draw from next. */
  cursor: number;
  /** The lesson the flow is on right now. */
  current: { lessonId: string; pathId: string } | null;
  /** The pre-drawn look-ahead (D8), or `null` when not yet drawn. */
  next: { lessonId: string; pathId: string } | null;
  /** In order; `completed.length` is "k" in "k of n". */
  completed: FlowCompletion[];
  /** Set once, by whichever ending fired first; the receipt reads it. */
  endedReason: "length" | "dry" | "ended" | null;
}

type Listener = () => void;
const listeners = new Set<Listener>();

function notify(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  // `storage` only ever fires for *another* tab's write to the same origin,
  // which `sessionStorage` (per-tab) never produces for this key — added
  // anyway so a future storage choice is covered by construction rather than
  // by remembering to add a listener the day it changes.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function isFlowOrder(value: unknown): value is FlowOrder {
  return value === "interleave" || value === "random" || value === "serial";
}

function isLessonRef(value: unknown): value is { lessonId: string; pathId: string } {
  if (typeof value !== "object" || value === null) return false;
  const ref = value as Record<string, unknown>;
  return typeof ref.lessonId === "string" && typeof ref.pathId === "string";
}

function isCompletion(value: unknown): value is FlowCompletion {
  if (typeof value !== "object" || value === null) return false;
  const completion = value as Record<string, unknown>;
  return (
    typeof completion.lessonId === "string" &&
    typeof completion.pathId === "string" &&
    typeof completion.title === "string" &&
    (completion.outcome === "correct" ||
      completion.outcome === "incorrect" ||
      completion.outcome === null)
  );
}

/**
 * Shape validation for a parsed record (D4). Deliberately permissive about
 * *values* — a stale path id or a cursor past the scope's end is
 * `flow-order.ts`'s problem to resolve at the next pick, not a reason to
 * discard the record — and strict about *shape*: a shape mismatch means a
 * different version or a corrupted write, and the only honest reading of
 * either is "no flow" (`readFlow` removes it rather than returning it).
 */
function isValidRecord(value: unknown): value is FlowRecord {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    record.version === 1 &&
    typeof record.startedAt === "string" &&
    (record.length === null || typeof record.length === "number") &&
    isFlowOrder(record.order) &&
    Array.isArray(record.paths) &&
    record.paths.every((path) => typeof path === "string") &&
    typeof record.cursor === "number" &&
    (record.current === null || isLessonRef(record.current)) &&
    (record.next === null || isLessonRef(record.next)) &&
    Array.isArray(record.completed) &&
    record.completed.every(isCompletion) &&
    (record.endedReason === null ||
      record.endedReason === "length" ||
      record.endedReason === "dry" ||
      record.endedReason === "ended")
  );
}

function readRaw(): string | null {
  try {
    return window.sessionStorage.getItem(FLOW_STORAGE_KEY);
  } catch {
    return null;
  }
}

function removeRawSilently(): void {
  try {
    window.sessionStorage.removeItem(FLOW_STORAGE_KEY);
  } catch {
    // Same "storage blocked" posture as every other access here (§9).
  }
}

// `useSyncExternalStore`'s `getSnapshot` contract requires a referentially
// **stable** result across calls whose underlying data has not changed — React
// calls it more than once per render to check for tearing, and a fresh object
// every time (which `JSON.parse` would hand back even for byte-identical
// content) reads as "the store changed" on every single check, which is an
// infinite render loop, not a subtle perf issue. So this module caches the
// last-seen raw string alongside its parsed result and only re-parses when
// the raw string itself has actually changed.
let cachedRaw: string | null | undefined; // undefined = never read yet
let cachedRecord: FlowRecord | null = null;

/**
 * The parsed, validated record, or `null` for "no flow" — including a
 * malformed one, which is removed here rather than handed back to be
 * misread again (D4). This is the `getSnapshot` `useFlow` passes to
 * `useSyncExternalStore`, so it must stay side-effect-free with respect to
 * *notifying* — it silently drops a bad record but never calls `notify()`,
 * which would mean triggering a state update from inside a render's own
 * snapshot read.
 */
function snapshot(): FlowRecord | null {
  const raw = readRaw();
  if (raw === cachedRaw) return cachedRecord;
  cachedRaw = raw;

  if (raw === null) {
    cachedRecord = null;
    return null;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    removeRawSilently();
    cachedRaw = null;
    cachedRecord = null;
    return null;
  }

  if (!isValidRecord(parsed)) {
    removeRawSilently();
    cachedRaw = null;
    cachedRecord = null;
    return null;
  }

  cachedRecord = parsed;
  return parsed;
}

/**
 * The record, or `null` for "no flow". Wrapped in try/catch at every step
 * underneath: a browser with storage blocked (private mode, a locked-down
 * profile) reads as "no flow", never throws (flow TDD §9).
 */
export function readFlow(): FlowRecord | null {
  return snapshot();
}

/**
 * Write the record and notify subscribers. The only writers of a record that
 * already exists are the pure transitions in `lib/flow-order.ts`, called from
 * the lesson route — nothing else in the app calls this directly except
 * `startFlow` below.
 */
export function writeFlow(record: FlowRecord): void {
  try {
    window.sessionStorage.setItem(FLOW_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Storage blocked or full: the write silently fails, the same posture as
    // every other access here (flow TDD §9) — the flow just does not persist.
  }
  notify();
}

/** Clear the record — leaving *is* ending (D5) — and notify subscribers. */
export function clearFlow(): void {
  removeRawSilently();
  notify();
}

/** The live record, or `null`. `useSyncExternalStore` over the store above —
 *  `snapshot` is the memoized `getSnapshot` React's tearing checks require. */
export function useFlow(): FlowRecord | null {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}

/**
 * Thrown by `startFlow` when no path in the chosen scope is eligible. A
 * guard, not a learner-facing error path: the setup sheet's Start button is
 * disabled in exactly this state (flow TDD §5.6), so a caller reaching this
 * has a bug to fix, not a message to show.
 */
export class NoEligiblePathError extends Error {
  constructor() {
    super("startFlow: no path in scope is eligible");
    this.name = "NoEligiblePathError";
  }
}

export interface StartFlowInput {
  length: number | null;
  order: FlowOrder;
  /** Flow scope: path ids in the order the learner picked them. */
  paths: string[];
}

/**
 * Build the first record (D4, §5.2 with `cursor = 0`), pre-draw the
 * look-ahead (D8) so the lesson route's open effect finds one already
 * waiting, write it, and return the first lesson id to navigate to.
 *
 * `summaries` is the setup sheet's own already-fetched `pathsListQueryOptions`
 * data — this never issues a request of its own.
 */
export function startFlow(input: StartFlowInput, summaries: PathSummary[]): string {
  const eligible = eligiblePaths(summaries, input.paths);
  const seed: FlowRecord = {
    version: 1,
    startedAt: new Date().toISOString(),
    length: input.length,
    order: input.order,
    paths: input.paths,
    cursor: 0,
    current: null,
    next: null,
    completed: [],
    endedReason: null,
  };

  const first = pickNext(seed, eligible, Math.random);
  if (first === null) throw new NoEligiblePathError();

  const withCurrent: FlowRecord = { ...seed, current: first.next, cursor: first.cursor };
  // The same `eligible` pool as the first draw — nothing has been completed
  // yet, so no path's `next_lesson` has moved. `pickNext`'s own random-order
  // exclusion (now that `current` is set) is what keeps this from repeating
  // the first pick when a second path is available.
  const second = pickNext(withCurrent, eligible, Math.random);
  // A look-ahead only means anything on a *different* path — D8's reason for
  // it is that an interleaved flow's next lesson is elsewhere; a serial
  // scope, or a single-path one, always draws back onto the path `current`
  // is already on, which is just the lesson already on screen (same-path is
  // covered by backend Prefetch (+N), not this GET). Such a draw is dropped
  // (`next: null`), not stored.
  const sameAsCurrentPath = second !== null && second.next.pathId === first.next.pathId;
  // The second draw's own `cursor` is kept either way, not just its `next` —
  // interleave and serial each consume one step of the scope per draw, and
  // the look-ahead is a draw whether or not it produced a usable `next`.
  // Dropping it here would silently rewind the cursor: the *first* advance
  // (§5.3, which trusts a still-eligible `flow.next` and leaves `cursor`
  // untouched, or falls through to a fresh `pickNext` when `next` is null)
  // would look correct, but the draw *after* that would re-run `pickNext`
  // from the stale, one-behind cursor and repeat a path interleave had
  // already moved past.
  const record: FlowRecord = {
    ...withCurrent,
    next: second !== null && !sameAsCurrentPath ? second.next : null,
    cursor: second?.cursor ?? withCurrent.cursor,
  };

  writeFlow(record);
  return first.next.lessonId;
}
