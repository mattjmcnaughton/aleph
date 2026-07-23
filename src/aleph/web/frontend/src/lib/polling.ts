// THE shared polling helper for every trigger+poll surface (TDD §5.4, §14).
//
// Every poll target — path outline status, lesson generation state, and future
// ones (AL-061..065) — drives TanStack Query's `refetchInterval` through this
// module so they all share one cadence: poll on a 2s -> 5s backoff, and stop
// the moment the state is terminal. The core is a pure function so the interval
// and stop-on-terminal behaviour are unit-testable without a live query.

/** First poll fires after this delay (TDD §14: "2s -> backoff to 5s"). */
export const POLL_START_MS = 2000;
/** Backoff ceiling; the interval never grows past this. */
export const POLL_MAX_MS = 5000;
/** Added to the interval per completed poll until the ceiling. */
export const POLL_STEP_MS = 1000;

/**
 * Pure backoff ramp: 0 completed polls -> 2s, then +1s per completed poll,
 * clamped to 5s (TDD §14). Negative counts are treated as the first poll.
 * The numbers are fixed from §14 — no per-caller override (nobody sets one, and
 * the real-TanStack cadence test uses fake timers rather than shrunk delays).
 */
export function pollBackoffMs(completedPolls: number): number {
  const safeCompleted = Math.max(0, completedPolls);
  return Math.min(POLL_MAX_MS, POLL_START_MS + safeCompleted * POLL_STEP_MS);
}

export interface PollingConfig<T> {
  /** True once the polled resource has reached a state that won't change. */
  isTerminal: (data: T | undefined) => boolean;
}

/**
 * Pure poll decision: given the latest data and how many polls have completed,
 * return the next interval in ms, or `false` to stop (terminal state reached).
 */
export function nextPollInterval<T>(
  data: T | undefined,
  completedPolls: number,
  config: PollingConfig<T>,
): number | false {
  if (config.isTerminal(data)) {
    return false;
  }
  return pollBackoffMs(completedPolls);
}

/** Minimal structural view of a TanStack Query the refetch adapter reads. */
export interface PollableQuery<T> {
  state: {
    data: T | undefined;
    dataUpdateCount: number;
  };
}

/**
 * Build a `refetchInterval` function for TanStack Query. Wire it straight into
 * `useQuery({ ..., refetchInterval })`; TanStack passes the live Query, which
 * structurally satisfies PollableQuery. A terminal state returns `false`, which
 * stops the polling loop.
 *
 * `dataUpdateCount` is already 1 at the first interval decision (the initial
 * fetch counts), so we subtract it to make the *first poll* fire at 2s rather
 * than 3s. Advisory: `dataUpdateCount` never resets across a query's lifetime,
 * so a retry after a failed/refused terminal state would resume at the 5s
 * ceiling instead of 2s. That is fine for Phase 1 (terminal states stop the
 * loop and surfaces remount a fresh query on retry); revisit if an in-place
 * "resume polling" path is ever added.
 */
export function makePollingRefetchInterval<T>(config: PollingConfig<T>) {
  return (query: PollableQuery<T>): number | false =>
    nextPollInterval<T>(query.state.data, Math.max(0, query.state.dataUpdateCount - 1), config);
}
