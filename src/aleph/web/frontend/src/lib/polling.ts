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
  /**
   * Optional: given the query's latest error, true when that error is *terminal*
   * — it will never resolve, so polling must stop (e.g. a `404`). Transient
   * errors (a network blip, a `500`) don't match and keep polling through the
   * backoff, since TanStack keeps the last-good data while the query retries.
   */
  isErrorTerminal?: (error: unknown) => boolean;
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
    /** The query's latest error (`null` when the last fetch succeeded). */
    error?: unknown;
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
 * than 3s. Note: `dataUpdateCount` never resets across a query's lifetime, so a
 * retry that reused the same cached query would resume at the 5s ceiling. The
 * onboarding retry therefore `resetQueries` (not `invalidateQueries`) to clear
 * the count and restore the 2s cadence; any future in-place "resume polling"
 * path must do the same.
 *
 * A query in a *terminal error* state (per `config.isErrorTerminal`) stops the
 * loop just like a terminal success — otherwise an errored query keeps firing
 * `refetchInterval` forever (each firing a fresh, still-failing fetch).
 */
export function makePollingRefetchInterval<T>(config: PollingConfig<T>) {
  return (query: PollableQuery<T>): number | false => {
    const { data, error, dataUpdateCount } = query.state;
    if (error != null && config.isErrorTerminal?.(error)) {
      return false;
    }
    return nextPollInterval<T>(data, Math.max(0, dataUpdateCount - 1), config);
  };
}
