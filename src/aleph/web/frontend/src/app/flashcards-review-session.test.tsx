import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession, type ReviewQueue } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import {
  configureFlashcards,
  flashcardGradeRequests,
  reviewQueueRequestCount,
} from "../mocks/flashcards";
import { seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// The review session (PRD §3/§4, Phase 3 TDD §8): reveal → grade → next card,
// the interval label read straight off each card's own payload, and the
// widen offer that appears only at the end of a filtered session with cards
// due elsewhere (PRD §4.10). Driven end to end through the real router and
// MSW, the same seam `completion-refresh.test.tsx`/`streaks.test.tsx` use.

const flashcardsSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { flashcards: true } },
};

function useFlashcardsSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(flashcardsSession)));
}

const PATH_ID = "p9800000-0000-4000-8000-000000000001";

function queue(overrides: Partial<ReviewQueue> = {}): ReviewQueue {
  return {
    today: "2026-08-04",
    total: 2,
    completed: 0,
    scope_path_id: null,
    other_due_count: 0,
    cards: [],
    ...overrides,
  };
}

const CARD_ONE = {
  card_id: "c1",
  front: "What does `extends` mean?",
  back: "It constrains T — T must be assignable to X.",
  rung: 0,
  got_it_interval_days: 42, // deliberately not a ladder day (1/3/7/14/30, D2/§13)
  path_id: null,
  source: {
    kind: "degraded" as const,
    lesson_title: "Generic constraints",
    path_title: "Learn TypeScript",
  },
};

const CARD_TWO = {
  ...CARD_ONE,
  card_id: "c2",
  front: "Why constrain a generic at all?",
  rung: 1,
  got_it_interval_days: 5,
};

async function gotoReview(search = ""): Promise<void> {
  window.history.pushState({}, "", `/review${search}`);
  render(<App />);
}

describe("Flashcards — the review session (PRD §3/§4, Phase 3 TDD §8)", () => {
  it("[TDD §8] the flag off renders nothing to review and never fetches the queue", async () => {
    // Default fake session ships `flashcards: false`.
    await gotoReview();
    await screen.findByTestId("review-unavailable");
    // `skipToken`, not a fetch that happened to answer with nothing — the
    // assertion the test's own name promises (previously unproven: nothing
    // here counted a request).
    expect(reviewQueueRequestCount()).toBe(0);
  });

  it("[PRD §4.10] the scope chip reads 'All paths' with no path filter", async () => {
    useFlashcardsSession();
    configureFlashcards({ queue: queue({ total: 1, cards: [CARD_ONE] }) });
    await gotoReview();

    expect((await screen.findByTestId("review-scope-chip")).textContent).toBe("All paths");
  });

  it("[PRD §4.10] the scope chip names the path when filtered, with no switcher offered", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    configureFlashcards({
      queue: queue({ total: 1, cards: [CARD_ONE], scope_path_id: PATH_ID }),
    });
    await gotoReview(`?path=${PATH_ID}`);

    await waitFor(() => {
      expect(screen.getByTestId("review-scope-chip").textContent).toBe("Learn TypeScript");
    });
    // §4.10: no switcher — a plain chip, never a control.
    expect(screen.queryByRole("button", { name: /switch/i })).toBeNull();
  });

  it("the reveal→grade flow: front only pre-reveal, then the back and both grades", async () => {
    useFlashcardsSession();
    configureFlashcards({ queue: queue({ total: 2, cards: [CARD_ONE, CARD_TWO] }) });
    await gotoReview();

    await screen.findByTestId("review-card-front");
    expect(screen.getByTestId("review-card-front").textContent).toBe(CARD_ONE.front);
    // Grading a card you haven't seen the back of is not a review.
    expect(screen.queryByTestId("review-card-back")).toBeNull();
    expect(screen.queryByTestId("review-grade-again")).toBeNull();
    expect(screen.queryByTestId("review-grade-got-it")).toBeNull();
    screen.getByText("Card 1 of 2");

    fireEvent.click(screen.getByTestId("review-card-flip"));
    expect(screen.getByTestId("review-card-back").textContent).toBe(CARD_ONE.back);
    // [§8] the interval label is read straight off this card's own payload.
    expect(screen.getByTestId("review-grade-interval").textContent).toBe("in 42 days");
  });

  it("[§8] the interval label changes per card — never a client-side ladder constant", async () => {
    useFlashcardsSession();
    configureFlashcards({ queue: queue({ total: 2, cards: [CARD_ONE, CARD_TWO] }) });
    await gotoReview();

    await screen.findByTestId("review-card-front");
    fireEvent.click(screen.getByTestId("review-card-flip"));
    fireEvent.click(screen.getByTestId("review-grade-got-it"));

    // Card one satisfied and gone; card two is now current, with its own
    // (different) preview — nothing here could be a hardcoded constant.
    await waitFor(() => {
      expect(screen.getByTestId("review-card-front").textContent).toBe(CARD_TWO.front);
    });
    screen.getByText("Card 2 of 2");
    fireEvent.click(screen.getByTestId("review-card-flip"));
    expect(screen.getByTestId("review-grade-interval").textContent).toBe("in 5 days");

    fireEvent.click(screen.getByTestId("review-grade-again"));
    await waitFor(() => {
      expect(flashcardGradeRequests()).toEqual([
        { card_id: "c1", grade: "got_it", rung_before: 0 },
        { card_id: "c2", grade: "again", rung_before: 1 },
      ]);
    });
  });

  it("[§8, finding 5] an `again` demotion never shows the pre-grade interval — it drops the card and lets the refetch re-insert it", async () => {
    useFlashcardsSession();
    // A rung-2 card previewing 14 days pre-grade — its own fixture, distinct
    // from `CARD_ONE`/`CARD_TWO` above, so the 14 -> 7 change below is
    // unambiguously the *server's* recompute, not a coincidence of shared data.
    const DEMOTED_CARD = { ...CARD_ONE, card_id: "c-demote", rung: 2, got_it_interval_days: 14 };
    let queueCalls = 0;
    // The authoritative refetch (triggered by the grade mutation's own
    // `invalidateQueries`) is deliberately held open until the test releases
    // it — a real refetch can land fast enough in jsdom to make the optimistic
    // patch's transient state a race; blocking it makes that state observable
    // deterministically instead of hoping the assertion wins the race.
    let releaseRefetch: (() => void) | undefined;
    server.use(
      http.post(`${API_V1_BASE}/reviews`, async ({ request }) => {
        const body = (await request.json()) as { card_id: string };
        return HttpResponse.json({ card_id: body.card_id, rung: 1, due_on: "2026-08-04" });
      }),
      http.get(`${API_V1_BASE}/reviews/queue`, async () => {
        queueCalls += 1;
        // First fetch (the session's initial load): the pre-grade card at
        // rung 2, previewing 14 days.
        if (queueCalls === 1) {
          return HttpResponse.json(queue({ total: 1, cards: [DEMOTED_CARD] }));
        }
        // Every fetch after: held until released, then answers with the same
        // card demoted to rung 1 and a genuinely different 7-day preview —
        // never client-recomputed, only ever server-served.
        await new Promise<void>((resolve) => {
          releaseRefetch = resolve;
        });
        return HttpResponse.json(
          queue({ total: 1, cards: [{ ...DEMOTED_CARD, rung: 1, got_it_interval_days: 7 }] }),
        );
      }),
    );
    await gotoReview();

    await screen.findByTestId("review-card-front");
    fireEvent.click(screen.getByTestId("review-card-flip"));
    expect(screen.getByTestId("review-grade-interval").textContent).toBe("in 14 days");
    fireEvent.click(screen.getByTestId("review-grade-again"));

    // With the refetch still blocked, only the optimistic patch has landed.
    // The buggy version re-queued the card immediately with its stale 14-day
    // preview (still revealed, since a real fix must not need a second reveal
    // to prove this); the fix drops it outright, so the session reads as
    // finished until the authoritative data arrives — and the stale interval
    // is nowhere on screen.
    await screen.findByTestId("session-complete");
    expect(screen.queryByTestId("review-grade-interval")).toBeNull();
    expect(screen.queryByText("in 14 days")).toBeNull();

    // Release the refetch: it re-inserts the card with the server's own new
    // preview — 7 days, never the stale 14 the client never computed.
    releaseRefetch?.();
    await screen.findByTestId("review-card-front");
    fireEvent.click(screen.getByTestId("review-card-flip"));
    expect(screen.getByTestId("review-grade-interval").textContent).toBe("in 7 days");
  });

  it("[PRD §4.10] the widen offer appears only in a filtered session with others due", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    configureFlashcards({
      queue: queue({
        total: 1,
        cards: [CARD_ONE],
        scope_path_id: PATH_ID,
        other_due_count: 3,
      }),
    });
    await gotoReview(`?path=${PATH_ID}`);

    await screen.findByTestId("review-card-front");
    fireEvent.click(screen.getByTestId("review-card-flip"));
    fireEvent.click(screen.getByTestId("review-grade-got-it"));

    await screen.findByTestId("session-complete");
    // No "study more" button — the cap is the point (PRD §4.4). The copy may
    // still *name* the absent control in prose (below); what must not exist
    // is an actionable one.
    expect(screen.queryByRole("button", { name: /study more/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /study more/i })).toBeNull();
    const widen = await screen.findByTestId("session-widen-offer");
    expect(widen.textContent).toMatch(/3 cards are due/i);
  });

  it("[PRD §4.10] no widen offer in an unfiltered session, even with cards left elsewhere", async () => {
    useFlashcardsSession();
    configureFlashcards({
      queue: queue({ total: 1, cards: [CARD_ONE], scope_path_id: null, other_due_count: 0 }),
    });
    await gotoReview();

    await screen.findByTestId("review-card-front");
    fireEvent.click(screen.getByTestId("review-card-flip"));
    fireEvent.click(screen.getByTestId("review-grade-got-it"));

    await screen.findByTestId("session-complete");
    expect(screen.queryByTestId("session-widen-offer")).toBeNull();
  });

  it("[PRD §4.10] no widen offer in a filtered session with nothing due elsewhere", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    configureFlashcards({
      queue: queue({
        total: 1,
        cards: [CARD_ONE],
        scope_path_id: PATH_ID,
        other_due_count: 0,
      }),
    });
    await gotoReview(`?path=${PATH_ID}`);

    await screen.findByTestId("review-card-front");
    fireEvent.click(screen.getByTestId("review-card-flip"));
    fireEvent.click(screen.getByTestId("review-grade-got-it"));

    await screen.findByTestId("session-complete");
    expect(screen.queryByTestId("session-widen-offer")).toBeNull();
  });

  it("[PRD §4.8] no cards due at all reads as an invitation, never a debt", async () => {
    useFlashcardsSession();
    configureFlashcards({ queue: queue({ total: 0, cards: [] }) });
    await gotoReview();

    const nothing = await screen.findByTestId("review-nothing-due");
    expect(nothing.textContent).not.toMatch(/\d/);
  });

  // AL-410 review finding 1/3: `SessionComplete`'s own "Your cards" door — the
  // other of the two the plan specifies (`routes/index.tsx` is the first) —
  // was untested before this.
  it("[AL-410 plan §6] the end-of-queue screen's 'Your cards' link points at /cards", async () => {
    useFlashcardsSession();
    configureFlashcards({ queue: queue({ total: 1, cards: [CARD_ONE] }) });
    await gotoReview();

    await screen.findByTestId("review-card-front");
    fireEvent.click(screen.getByTestId("review-card-flip"));
    fireEvent.click(screen.getByTestId("review-grade-got-it"));

    const link = await screen.findByTestId("session-complete-your-cards");
    expect(link.textContent).toBe("Your cards");
    expect(link.getAttribute("href")).toBe("/cards");
  });
});
