import { render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { configureFlashcards, reviewSummaryRequestCount } from "../mocks/flashcards";
import { seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// The retention loop's home surfaces (PRD §3, Phase 3 TDD §8): the due pill
// (app-header, every route), the *Due today* card, and each path row's
// `Review N` chip. Driven end to end through the real router, TanStack Query
// and MSW — `streaks.test.tsx`'s own seam, for the same reason: this is the
// exact route the streak line already lives on, and both surfaces answer
// "how does a failed decoration behave" the identical way (TDD §5.6's last
// row, extended to a component now mounted on every route).

/** A learner with `flashcards` on — the default fake session ships it dark. */
const flashcardsSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { flashcards: true } },
};

function useFlashcardsSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(flashcardsSession)));
}

async function gotoHome(): Promise<void> {
  window.history.pushState({}, "", "/");
  render(<App />);
  await screen.findByTestId("paths-switcher");
}

function itemFor(pathId: string): HTMLElement {
  const item = screen
    .getAllByTestId("path-list-item")
    .find((el) => el.getAttribute("data-path-id") === pathId);
  if (!item) throw new Error(`no list item for path ${pathId}`);
  return item;
}

describe("Flashcards — the home surfaces (PRD §3, Phase 3 TDD §8)", () => {
  it("[TDD §8] the flag off: no pill, no Due today card, no chip, and no request at all", async () => {
    // The default fake learner (`mocks/handlers.ts`) ships `flashcards: false`.
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    configureFlashcards({
      summary: { today: "2026-08-04", due_count: 10, estimated_minutes: 4, paths: [] },
    });
    await gotoHome();

    await screen.findByTestId("path-list-item");
    expect(screen.queryByTestId("review-pill")).toBeNull();
    expect(screen.queryByTestId("due-today-card")).toBeNull();
    expect(screen.queryByTestId("review-chip")).toBeNull();
    // `skipToken`, not a fetch that happened to answer with nothing.
    expect(reviewSummaryRequestCount()).toBe(0);
  });

  it("[PRD §3] hidden at zero even with the flag on — an invitation, not a debt", async () => {
    useFlashcardsSession();
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    configureFlashcards({
      summary: { today: "2026-08-04", due_count: 0, estimated_minutes: 0, paths: [] },
    });
    await gotoHome();

    await screen.findByTestId("path-list-item");
    await waitFor(() => expect(reviewSummaryRequestCount()).toBeGreaterThan(0));
    expect(screen.queryByTestId("review-pill")).toBeNull();
    expect(screen.queryByTestId("due-today-card")).toBeNull();
  });

  it("the pill, the Due today card, and each path's chip render together", async () => {
    useFlashcardsSession();
    seedPath({ id: "p-ts", topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    seedPath({
      id: "p-sql",
      topic: "SQL performance",
      level: "some_experience",
      title: "SQL performance",
    });
    configureFlashcards({
      summary: {
        today: "2026-08-04",
        due_count: 10,
        estimated_minutes: 4,
        paths: [
          { path_id: "p-ts", due_count: 3 },
          { path_id: "p-sql", due_count: 7 },
        ],
      },
    });
    await gotoHome();

    expect((await screen.findByTestId("review-pill")).textContent).toBe("10 due");

    const dueCard = screen.getByTestId("due-today-card");
    expect(dueCard.textContent).toContain("10 cards");
    expect(dueCard.textContent).toContain("~4 min");
    expect(dueCard.textContent).toContain("Across every path.");
    expect(dueCard.textContent).toContain("3 from Learn TypeScript");
    expect(dueCard.textContent).toContain("7 from SQL performance");

    // §5.3: the chips are each path's *share* of the global ten.
    expect(within(itemFor("p-ts")).getByTestId("review-chip").textContent).toBe("Review 3");
    expect(within(itemFor("p-sql")).getByTestId("review-chip").textContent).toBe("Review 7");
  });

  it("[D5] a path absent from `paths` gets no chip — absent means zero", async () => {
    useFlashcardsSession();
    seedPath({ id: "p-ts", topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    seedPath({
      id: "p-rust",
      topic: "Rust ownership",
      level: "new_to_it",
      title: "Rust ownership",
    });
    configureFlashcards({
      summary: {
        today: "2026-08-04",
        due_count: 3,
        estimated_minutes: 2,
        paths: [{ path_id: "p-ts", due_count: 3 }],
      },
    });
    await gotoHome();

    await screen.findByTestId("review-pill");
    within(itemFor("p-ts")).getByTestId("review-chip");
    expect(within(itemFor("p-rust")).queryByTestId("review-chip")).toBeNull();
  });

  it("[TDD §5.6] a failed summary query fails as decoration — the paths list is unaffected", async () => {
    useFlashcardsSession();
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    server.use(
      http.get(`${API_V1_BASE}/reviews/summary`, () =>
        HttpResponse.json(
          { error: { code: "internal_error", message: "Something went wrong." } },
          { status: 500 },
        ),
      ),
    );
    await gotoHome();

    // The product — the list — is unaffected by the decoration's failure.
    await screen.findByTestId("path-list-item");
    expect(screen.queryByTestId("review-pill")).toBeNull();
    expect(screen.queryByTestId("due-today-card")).toBeNull();
    expect(screen.queryByTestId("review-chip")).toBeNull();
  });

  // AL-410 review finding 1: a learner with kept cards and none due today has
  // no door into `/cards` unless home's own link is unconditional on the flag
  // alone — `DueTodayCard` hides outright at zero (the test above), and that
  // used to be the link's only home too.
  it("[AL-410 finding 1] 'Your cards' renders on a quiet day too — no due cards, still a door", async () => {
    useFlashcardsSession();
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    configureFlashcards({
      summary: { today: "2026-08-04", due_count: 0, estimated_minutes: 0, paths: [] },
    });
    await gotoHome();

    await screen.findByTestId("path-list-item");
    // The card this link used to live inside is genuinely gone…
    expect(screen.queryByTestId("due-today-card")).toBeNull();
    // …but the door itself is not: it renders straight off the flag, never
    // off `due_count`.
    const link = await screen.findByTestId("your-cards-link");
    expect(link.getAttribute("href")).toBe("/cards");
    // The section says so out loud on the quiet day, rather than vanishing.
    expect(screen.getByTestId("cards-section-summary").textContent).toBe("Nothing due today");
  });

  it("[AL-410 finding 1] 'Your cards' still renders alongside a non-empty Due today card — one door, not two", async () => {
    useFlashcardsSession();
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    configureFlashcards({
      summary: { today: "2026-08-04", due_count: 3, estimated_minutes: 2, paths: [] },
    });
    await gotoHome();

    await screen.findByTestId("due-today-card");
    // Exactly one "Your cards" door on the page — not a second copy inside
    // `DueTodayCard` itself.
    expect(screen.getAllByTestId("your-cards-link")).toHaveLength(1);
    expect(screen.getByTestId("cards-section-summary").textContent).toBe("3 due today");
  });

  it("[AL-410 finding 1] 'Your cards' is absent with the flag off — same gate as everything else", async () => {
    // Default fake learner ships `flashcards: false`.
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    await gotoHome();

    await screen.findByTestId("path-list-item");
    expect(screen.queryByTestId("your-cards-link")).toBeNull();
  });
});
