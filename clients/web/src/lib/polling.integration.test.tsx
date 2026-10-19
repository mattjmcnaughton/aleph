import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { type ReactNode, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makePollingRefetchInterval } from "./polling";

// Integration-style proof that the refetch adapter drives a *real* TanStack
// Query on the documented cadence (TDD §14: "2s -> backoff to 5s"). The pure
// unit tests can encode any completed-poll number they like; only a live query
// reveals that TanStack's `dataUpdateCount` is already 1 at the first interval
// decision, so the adapter must subtract the initial fetch to hit a 2s poll.

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Number.POSITIVE_INFINITY } },
  });
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
}

describe("makePollingRefetchInterval driving a live TanStack Query", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("polls on the real 2s,3s,4s,5s,5s cadence off a live query", async () => {
    const fetchTimes: number[] = [];
    const refetchInterval = makePollingRefetchInterval<string>({
      isTerminal: (d) => d === "ready",
    });

    renderHook(
      () =>
        useQuery({
          queryKey: ["path-status"],
          queryFn: async () => {
            fetchTimes.push(Date.now());
            return "generating";
          },
          refetchInterval,
        }),
      { wrapper: makeWrapper() },
    );

    // Walk fake time forward in fine steps so each scheduled refetch fires and
    // reschedules; 25s covers the full ramp to the ceiling.
    for (let i = 0; i < 25; i++) {
      await vi.advanceTimersByTimeAsync(1000);
    }

    const gaps = fetchTimes.slice(1).map((t, i) => t - fetchTimes[i]);
    expect(gaps.slice(0, 5)).toEqual([2000, 3000, 4000, 5000, 5000]);
  });

  it("stops polling once the live query reaches a terminal state", async () => {
    const fetchTimes: number[] = [];
    let calls = 0;
    const refetchInterval = makePollingRefetchInterval<string>({
      isTerminal: (d) => d === "ready",
    });

    renderHook(
      () =>
        useQuery({
          queryKey: ["path-status-terminal"],
          queryFn: async () => {
            fetchTimes.push(Date.now());
            calls += 1;
            // Third fetch lands the outline in a terminal state.
            return calls >= 3 ? "ready" : "generating";
          },
          refetchInterval,
        }),
      { wrapper: makeWrapper() },
    );

    for (let i = 0; i < 60; i++) {
      await vi.advanceTimersByTimeAsync(1000);
    }

    // Exactly three fetches: initial + two polls, then the terminal state halts it.
    expect(fetchTimes.length).toBe(3);
  });
});
