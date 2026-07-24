import { describe, expect, it } from "vitest";
import {
  makePollingRefetchInterval,
  nextPollInterval,
  POLL_MAX_MS,
  POLL_START_MS,
  pollBackoffMs,
} from "./polling";

describe("pollBackoffMs", () => {
  it("starts at 2s and ramps by 1s per poll up to a 5s ceiling (TDD §14)", () => {
    expect(pollBackoffMs(0)).toBe(2000);
    expect(pollBackoffMs(1)).toBe(3000);
    expect(pollBackoffMs(2)).toBe(4000);
    expect(pollBackoffMs(3)).toBe(5000);
    expect(pollBackoffMs(50)).toBe(5000);
  });

  it("exposes the documented 2s -> 5s bounds (spec pin against silent drift)", () => {
    expect(POLL_START_MS).toBe(2000);
    expect(POLL_MAX_MS).toBe(5000);
  });

  it("treats a negative completed-poll count as the first poll", () => {
    expect(pollBackoffMs(-3)).toBe(2000);
  });
});

describe("nextPollInterval", () => {
  const isTerminal = (state?: string) => state === "ready" || state === "failed";

  it("keeps polling with backoff while the state is non-terminal", () => {
    expect(nextPollInterval("generating", 0, { isTerminal })).toBe(2000);
    expect(nextPollInterval("generating", 3, { isTerminal })).toBe(5000);
  });

  it("stops (returns false) as soon as a terminal state is reached", () => {
    expect(nextPollInterval("ready", 5, { isTerminal })).toBe(false);
    expect(nextPollInterval("failed", 0, { isTerminal })).toBe(false);
  });

  it("keeps polling while data is still undefined (nothing terminal yet)", () => {
    expect(nextPollInterval(undefined, 0, { isTerminal })).toBe(2000);
  });
});

describe("makePollingRefetchInterval", () => {
  const isTerminal = (state?: string) => state === "done";

  // NB: TanStack's `dataUpdateCount` is 1 after the initial fetch, so the
  // adapter subtracts it — the first live poll must be 2s, not 3s. The live
  // cadence is proven end-to-end in polling.integration.test.tsx.
  it("subtracts the initial fetch so the first live poll is 2s", () => {
    const refetchInterval = makePollingRefetchInterval<string>({ isTerminal });
    expect(refetchInterval({ state: { data: "pending", dataUpdateCount: 1 } })).toBe(2000);
    expect(refetchInterval({ state: { data: "pending", dataUpdateCount: 2 } })).toBe(3000);
    expect(refetchInterval({ state: { data: "pending", dataUpdateCount: 4 } })).toBe(5000);
  });

  it("clamps a zero dataUpdateCount to the first poll rather than going negative", () => {
    const refetchInterval = makePollingRefetchInterval<string>({ isTerminal });
    expect(refetchInterval({ state: { data: "pending", dataUpdateCount: 0 } })).toBe(2000);
  });

  it("returns false at a terminal state so TanStack Query stops polling", () => {
    const refetchInterval = makePollingRefetchInterval<string>({ isTerminal });
    expect(refetchInterval({ state: { data: "done", dataUpdateCount: 9 } })).toBe(false);
  });

  it("stops polling on a terminal error (e.g. a 404 that will never resolve)", () => {
    const refetchInterval = makePollingRefetchInterval<string>({
      isTerminal,
      isErrorTerminal: (error) => (error as { status?: number })?.status === 404,
    });
    expect(
      refetchInterval({ state: { data: undefined, error: { status: 404 }, dataUpdateCount: 2 } }),
    ).toBe(false);
  });

  it("keeps polling through a transient (non-terminal) error", () => {
    const refetchInterval = makePollingRefetchInterval<string>({
      isTerminal,
      isErrorTerminal: (error) => (error as { status?: number })?.status === 404,
    });
    // A 500 is transient: last-good data may still resolve, so keep the backoff.
    expect(
      refetchInterval({ state: { data: "pending", error: { status: 500 }, dataUpdateCount: 2 } }),
    ).toBe(3000);
  });

  it("ignores errors when no isErrorTerminal predicate is configured", () => {
    const refetchInterval = makePollingRefetchInterval<string>({ isTerminal });
    expect(
      refetchInterval({ state: { data: "pending", error: { status: 404 }, dataUpdateCount: 1 } }),
    ).toBe(2000);
  });
});
