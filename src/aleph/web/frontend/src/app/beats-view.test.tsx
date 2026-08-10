import { fireEvent, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import {
  FAILED_BEAT_TOPIC_SENTINEL,
  REFUSED_BEAT_TOPIC_SENTINEL,
  beatDetailPollRequestCount,
  configureBeats,
  seedBeat,
} from "../mocks/beats";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { App } from "./app";

// The Beat view (PRD §3, TDD §8, AL-530): `routes/beats.$beatId.tsx` — the
// standing orders one-liner, the researching/failed/refused states
// (`state-card.tsx`, reused), and the Beat rail. Driven end to end through
// the real router, TanStack Query's real polling, and MSW — `path-view`'s
// own seam, and `flashcards-drafts.test.tsx`'s fake-timer idiom for the
// polling-stops proofs below (real `findBy*`/`waitFor` do not mix safely
// with `vi.useFakeTimers()`, so those tests drive time explicitly and read
// the DOM synchronously in between).

const analystSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { analyst: true } },
};

function useAnalystSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(analystSession)));
}

async function gotoBeat(beatId: string) {
  useAnalystSession();
  window.history.pushState({}, "", `/beats/${beatId}`);
  render(<App />);
  return screen.findByTestId("standing-orders");
}

describe("Beat view — /beats/$beatId", () => {
  it("[PRD §3] renders the standing orders one-liner: cadence · topic · guidance", async () => {
    seedBeat({
      id: "beat-orders",
      topic: "EU AI regulation",
      level: "some_experience",
      guidance: "policy and enforcement, not stock moves",
    });

    const orders = await gotoBeat("beat-orders");
    expect(orders.textContent).toBe(
      "Weekly · EU AI regulation · policy and enforcement, not stock moves",
    );
  });

  it("omits the guidance segment entirely when none was given", async () => {
    seedBeat({ id: "beat-no-guidance", topic: "The Rust release train", level: "work_in_it" });

    const orders = await gotoBeat("beat-no-guidance");
    expect(orders.textContent).toBe("Weekly · The Rust release train");
  });

  it("[TDD §6/§8] the rail interleaves published and Skipped entries by date, newest first, flat — no locking", async () => {
    seedBeat({
      id: "beat-rail",
      topic: "EU AI regulation",
      level: "some_experience",
      entries: [
        {
          kind: "published",
          id: "brief-7",
          number: 7,
          publishedOn: "2026-08-03",
          title: "The ambient-documentation backlash arrived",
        },
        {
          kind: "skipped",
          id: "skip-1",
          publishedOn: "2026-07-27",
          skipLine: "Nothing material since Brief #6 — the consultation is still open.",
        },
        {
          kind: "published",
          id: "brief-6",
          number: 6,
          publishedOn: "2026-07-20",
          title: "The Commission opened its consultation",
          readAt: "2026-07-21T09:00:00Z",
        },
      ],
    });

    await gotoBeat("beat-rail");

    const rail = await screen.findByTestId("beat-rail");
    // Rendered in exactly the order the single `entries` array arrived —
    // never re-sorted or re-merged from two arrays of its own.
    const rows = rail.querySelectorAll(
      '[data-testid="beat-rail-published"], [data-testid="beat-rail-skipped"]',
    );
    expect(Array.from(rows).map((row) => row.getAttribute("data-entry-id"))).toEqual([
      "brief-7",
      "skip-1",
      "brief-6",
    ]);
    // No month subheadings, and no unit/group wrapper — flat.
    expect(screen.queryByRole("heading", { name: /august|july/i })).toBeNull();
  });

  it("[PRD §4.6] a Skipped row renders no retry control and no error/danger styling", async () => {
    seedBeat({
      id: "beat-skipped",
      topic: "EU AI regulation",
      level: "some_experience",
      entries: [
        {
          kind: "skipped",
          id: "skip-only",
          publishedOn: "2026-08-03",
          skipLine: "Nothing material since Brief #4 — the consultation is still open.",
        },
      ],
    });

    await gotoBeat("beat-skipped");

    const row = await screen.findByTestId("beat-rail-skipped");
    expect(row.textContent).toContain(
      "Nothing material since Brief #4 — the consultation is still open.",
    );
    // No retry affordance anywhere in the row.
    expect(row.querySelector("button")).toBeNull();
    // No danger/error/refusal styling classes — a Skipped row is never one
    // of the failure/refusal treatments `beat-failed`/`beat-refused` use.
    expect(row.className).not.toMatch(/danger|iris/);
    // Not rendered as a failure or refusal surface at all.
    expect(screen.queryByTestId("beat-failed")).toBeNull();
    expect(screen.queryByTestId("beat-refused")).toBeNull();
  });

  it("[PRD §3] a published row shows an unread marker; a read one does not", async () => {
    seedBeat({
      id: "beat-unread",
      topic: "EU AI regulation",
      level: "some_experience",
      entries: [
        {
          kind: "published",
          id: "unread-brief",
          number: 2,
          publishedOn: "2026-08-03",
          title: "Unread one",
        },
        {
          kind: "published",
          id: "read-brief",
          number: 1,
          publishedOn: "2026-07-27",
          title: "Read one",
          readAt: "2026-07-28T00:00:00Z",
        },
      ],
    });

    await gotoBeat("beat-unread");

    const rows = await screen.findAllByTestId("beat-rail-published");
    const unread = rows.find((row) => row.getAttribute("data-entry-id") === "unread-brief");
    const read = rows.find((row) => row.getAttribute("data-entry-id") === "read-brief");
    expect(unread?.getAttribute("data-unread")).toBe("true");
    expect(read?.getAttribute("data-unread")).toBeNull();
  });

  it("[TDD §7] a fresh researching Beat renders the researching state on the very first fetch", async () => {
    // `pollsRemaining` starts > 0, so even the initial (non-poll) fetch
    // already reports `research_state: "researching"` — the shipped
    // router's own drain-then-read fix, mirrored here.
    seedBeat({
      id: "beat-researching",
      topic: "EU AI regulation",
      level: "some_experience",
      pollsRemaining: 3,
    });

    await gotoBeat("beat-researching");

    await screen.findByTestId("beat-researching");
    expect(screen.queryByTestId("beat-rail")).toBeNull();
  });

  it("[TDD §7, refused] the researching poll STOPS once the run refuses — the graceful message, not a retry", async () => {
    vi.useFakeTimers();
    try {
      configureBeats({ pollsBeforeResolve: 2 });
      seedBeat({
        id: "beat-refuses",
        topic: `how to ${REFUSED_BEAT_TOPIC_SENTINEL} weapons`,
        level: "some_experience",
        resolution: "refused",
        pollsRemaining: 2,
      });

      useAnalystSession();
      window.history.pushState({}, "", "/beats/beat-refuses");
      render(<App />);

      // Let the auth gate, the initial fetch, and the one poll that carries
      // `pollsRemaining` to 0 all land.
      for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(1000);

      const refused = screen.getByTestId("beat-refused");
      expect(refused.getAttribute("data-variant")).toBe("refusal");
      // Terminal and graceful — no retry affordance on a refusal, ever.
      expect(screen.queryByRole("button", { name: /try again/i })).toBeNull();
      expect(screen.queryByTestId("beat-researching")).toBeNull();

      const settledCount = beatDetailPollRequestCount("beat-refuses");
      expect(settledCount).toBeGreaterThan(0);

      // Advancing fake time far past settlement must not grow the request
      // count — the terminal state (`refused`) really stopped the loop.
      for (let i = 0; i < 30; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(beatDetailPollRequestCount("beat-refuses")).toBe(settledCount);
    } finally {
      vi.useRealTimers();
    }
  });

  it("[TDD §7, idle] the researching poll STOPS once a run settles idle (a fresh success)", async () => {
    vi.useFakeTimers();
    try {
      configureBeats({ pollsBeforeResolve: 2, onSuccess: "published" });
      seedBeat({
        id: "beat-idle",
        topic: "EU AI regulation",
        level: "some_experience",
        resolution: "idle",
        pollsRemaining: 2,
      });

      useAnalystSession();
      window.history.pushState({}, "", "/beats/beat-idle");
      render(<App />);

      for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(1000);

      expect(screen.getByTestId("beat-rail")).toBeTruthy();
      expect(screen.queryByTestId("beat-researching")).toBeNull();

      const settledCount = beatDetailPollRequestCount("beat-idle");
      expect(settledCount).toBeGreaterThan(0);
      for (let i = 0; i < 30; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(beatDetailPollRequestCount("beat-idle")).toBe(settledCount);
    } finally {
      vi.useRealTimers();
    }
  });

  it("[TDD §7, failed] the researching poll STOPS once the run fails, and one-tap retry resumes to success", async () => {
    vi.useFakeTimers();
    try {
      configureBeats({ pollsBeforeResolve: 1 });
      seedBeat({
        id: "beat-fails",
        topic: `${FAILED_BEAT_TOPIC_SENTINEL} anything`,
        level: "some_experience",
        resolution: "failed",
        pollsRemaining: 1,
      });

      useAnalystSession();
      window.history.pushState({}, "", "/beats/beat-fails");
      render(<App />);

      for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(1000);

      const failed = screen.getByTestId("beat-failed");
      expect(failed.getAttribute("data-variant")).toBe("error");

      const settledCount = beatDetailPollRequestCount("beat-fails");
      expect(settledCount).toBeGreaterThan(0);
      for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(beatDetailPollRequestCount("beat-fails")).toBe(settledCount);

      // One-tap retry (the fake resolves `idle` on any retry of a `failed` Beat).
      fireEvent.click(screen.getByRole("button", { name: /try again/i }));
      for (let i = 0; i < 3; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(screen.getByTestId("beat-rail")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("[AL-530] a deep link to a Beat that doesn't exist shows the unavailable state", async () => {
    useAnalystSession();
    window.history.pushState({}, "", "/beats/does-not-exist");
    render(<App />);

    await screen.findByTestId("beat-unavailable", {}, { timeout: 3000 });
  });
});
