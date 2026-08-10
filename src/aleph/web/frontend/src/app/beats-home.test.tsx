import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { beatsListRequestCount, seedBeat } from "../mocks/beats";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { App } from "./app";

// The home Beats section (PRD §3/§4.10, TDD §8, AL-530): a section **beside**
// "Your paths", never merged — the same card grammar (title, a line of
// state) with a different verb. Driven end to end through the real router,
// TanStack Query, and MSW — `streaks.test.tsx`'s / `flashcards-home.test.tsx`'s
// own seam for a home decoration behind a flag.

const analystSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { analyst: true } },
};

function useAnalystSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(analystSession)));
}

async function gotoHome(): Promise<void> {
  window.history.pushState({}, "", "/");
  render(<App />);
  await screen.findByTestId("paths-switcher");
}

describe("Home — the Beats section (PRD §3/§4.10, TDD §8)", () => {
  it("[TDD §8] the analyst flag off: no section on home, and no request at all", async () => {
    // The default fake learner (`mocks/handlers.ts`) ships `analyst: false`.
    seedBeat({ id: "beat-hidden", topic: "EU AI regulation", level: "some_experience" });
    await gotoHome();

    expect(screen.queryByTestId("beats-section")).toBeNull();
    expect(screen.queryByTestId("beat-list-item")).toBeNull();
    // `skipToken`, not a fetch that happened to answer with nothing.
    expect(beatsListRequestCount()).toBe(0);
  });

  it("[PRD §3] a Beat card beside 'Your paths' reads 'N new briefs · weekly'", async () => {
    useAnalystSession();
    seedBeat({
      id: "beat-unread",
      topic: "EU AI regulation",
      level: "some_experience",
      entries: [
        {
          kind: "published",
          id: "brief-1",
          number: 1,
          publishedOn: "2026-08-03",
          title: "First Brief",
        },
      ],
    });

    await gotoHome();

    const card = await screen.findByTestId("beat-list-item");
    expect(card.getAttribute("data-beat-id")).toBe("beat-unread");
    const status = screen.getByTestId("beat-item-status");
    expect(status.textContent).toBe("1 new brief · weekly");
    // "Your paths" is still its own, separate section — never merged.
    screen.getByTestId("paths-switcher");
  });

  it("[PRD §3] a researching Beat's card reads 'Researching… · started Xs ago'", async () => {
    useAnalystSession();
    seedBeat({
      id: "beat-researching",
      topic: "EU AI regulation",
      level: "some_experience",
      pollsRemaining: 5,
    });

    await gotoHome();

    const status = await screen.findByTestId("beat-item-status");
    expect(status.textContent).toMatch(/^Researching… · started \d+s ago$/);
  });

  it("[PRD §4.10] the Beats section never merges into the paths list", async () => {
    useAnalystSession();
    seedBeat({ id: "beat-one", topic: "EU AI regulation", level: "some_experience" });

    await gotoHome();

    await screen.findByTestId("beats-section");
    const pathsList = screen.queryByTestId("paths-list");
    // No Beat row ever lands inside the paths list, whichever state it's in.
    if (pathsList) {
      expect(pathsList.querySelector('[data-testid="beat-list-item"]')).toBeNull();
    }
    const beatsSection = screen.getByTestId("beats-section");
    expect(beatsSection.querySelector('[data-testid="beat-list-item"]')).not.toBeNull();
  });

  it("[TDD §7, FIX 7] nothing ever polls the list — the request count stays flat across time", async () => {
    // `mocks/beats.ts` has documented `beatsListRequestCount` as proving
    // this "when a test asserts this stays at 1 across fake time" since
    // AL-530 shipped, but nothing did — the code was already right (no
    // `refetchInterval` on `beatsListQueryOptions`), this is only the
    // missing regression test TDD §7 itself names as the risk: "adding one
    // would break nothing."
    vi.useFakeTimers();
    try {
      useAnalystSession();
      seedBeat({
        id: "beat-researching",
        topic: "EU AI regulation",
        level: "some_experience",
        pollsRemaining: 5,
      });

      window.history.pushState({}, "", "/");
      render(<App />);

      await vi.advanceTimersByTimeAsync(1000);
      const initialCount = beatsListRequestCount();
      expect(initialCount).toBeGreaterThan(0);

      // Advance well past several of the detail poll's own backoff cycles —
      // the list's own count must not move even though the Beat on it is
      // still researching.
      for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(3000);

      expect(beatsListRequestCount()).toBe(initialCount);
    } finally {
      vi.useRealTimers();
    }
  });

  it("[AL-530] the empty state offers Deploy analyst, with no Beats yet", async () => {
    useAnalystSession();
    await gotoHome();

    await screen.findByTestId("beats-empty");
    const cta = screen.getByTestId("deploy-analyst-button");
    expect(cta.getAttribute("href")).toBe("/beats/new");
  });
});
