import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession, type FlashcardCitation } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import {
  cardDeleteRequestIds,
  cardUpdateRequestBodies,
  cardsListRequestCount,
  configureCardUpdateFailure,
  reviewSummaryRequestCount,
  seedCard,
} from "../mocks/flashcards";
import { seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// `/cards` (AL-410): browse, edit and delete every kept card. Driven end to
// end through the real router, TanStack Query and MSW — `Link`-bearing review
// components (`card-source.tsx`'s citation) are covered here rather than as a
// standalone component render, the same rule `draft-list.test.tsx` states for
// `review-card.tsx`'s own sibling components.

const flashcardsSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { flashcards: true } },
};

function useFlashcardsSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(flashcardsSession)));
}

const PATH_ID = "p9800000-0000-4000-8000-000000000010";

const LINKED_SOURCE: FlashcardCitation = {
  kind: "linked",
  lesson_id: "l0000000-0000-4000-8000-000000000001",
  lesson_title: "Generic constraints",
  path_title: "Learn TypeScript",
};

const DEGRADED_SOURCE: FlashcardCitation = {
  kind: "degraded",
  lesson_title: "SELECT basics",
  path_title: "SQL performance",
};

/** `due_on` a fixed number of days from "today" — never the real clock, so
 *  the exact due-label text stays deterministic across whichever day the
 *  suite runs on. */
function dueInDays(days: number): string {
  const now = new Date();
  const shifted = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days);
  return `${shifted.getFullYear()}-${String(shifted.getMonth() + 1).padStart(2, "0")}-${String(
    shifted.getDate(),
  ).padStart(2, "0")}`;
}

async function gotoCards(): Promise<void> {
  window.history.pushState({}, "", "/cards");
  render(<App />);
}

describe("/cards — browse, edit, delete (AL-410)", () => {
  it("[flag off] renders the dead end and never fetches the list", async () => {
    // Default fake session ships `flashcards: false`.
    await gotoCards();
    await screen.findByTestId("cards-unavailable");
    expect(cardsListRequestCount()).toBe(0);
  });

  it("no cards kept yet reads as an invitation, not an error", async () => {
    useFlashcardsSession();
    await gotoCards();

    const empty = await screen.findByTestId("cards-empty");
    expect(empty.textContent).toMatch(/haven't kept any cards/i);
  });

  it("renders every kept card, and expanding one reveals its back + citation", async () => {
    useFlashcardsSession();
    seedCard({
      id: "c1",
      front: "What does `extends` mean?",
      back: "It constrains T.",
      due_on: dueInDays(3),
      source: LINKED_SOURCE,
    });
    seedCard({
      id: "c2",
      front: "What is SELECT for?",
      back: "Choosing which columns come back.",
      due_on: dueInDays(0),
      source: DEGRADED_SOURCE,
    });
    await gotoCards();

    const rows = await screen.findAllByTestId("card-row");
    expect(rows).toHaveLength(2);

    // Newest-kept-first (AL-410 plan §2) — `c2` was seeded after `c1`, so it
    // leads; its due date, being today, reads as such rather than a count.
    expect(rows[0].textContent).toContain("What is SELECT for?");
    expect(rows[0].textContent).toContain("Due today");
    expect(rows[1].textContent).toContain("Due in 3 days");

    // Back + citation are collapsed until the row is tapped.
    expect(screen.queryByTestId("card-row-back")).toBeNull();
    fireEvent.click(screen.getAllByTestId("card-row-toggle")[0]);
    expect(screen.getByTestId("card-row-back").textContent).toBe(
      "Choosing which columns come back.",
    );
    // D12: `degraded` never renders a link — plain text only.
    const source = screen.getByTestId("card-row-source");
    expect(source.textContent).toBe("From SELECT basics · SQL performance");
    expect(source.querySelector("a")).toBeNull();

    // The other row's `linked` citation does render a link.
    fireEvent.click(screen.getAllByTestId("card-row-toggle")[1]);
    const linkedSource = screen.getAllByTestId("card-row-source")[1];
    expect(linkedSource.querySelector("a")).not.toBeNull();
  });

  it("filters by path via the filter chips", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", title: "Learn TypeScript" });
    seedCard({
      id: "c1",
      front: "TypeScript card",
      back: "back",
      due_on: dueInDays(1),
      source: LINKED_SOURCE,
      pathId: PATH_ID,
    });
    seedCard({
      id: "c2",
      front: "SQL card",
      back: "back",
      due_on: dueInDays(1),
      source: DEGRADED_SOURCE,
      pathId: null,
    });
    await gotoCards();

    await screen.findAllByTestId("card-row");
    expect(screen.getAllByTestId("card-row")).toHaveLength(2);

    const chip = await screen.findByRole("link", { name: "Learn TypeScript" });
    fireEvent.click(chip);

    await waitFor(() => {
      const rows = screen.getAllByTestId("card-row");
      expect(rows).toHaveLength(1);
      expect(rows[0].textContent).toContain("TypeScript card");
    });
  });

  it("[edit] Save is disabled on an invalid pair, and a valid save round-trips the row's text", async () => {
    useFlashcardsSession();
    seedCard({
      id: "c1",
      front: "Old front",
      back: "Old back",
      due_on: dueInDays(2),
      source: DEGRADED_SOURCE,
    });
    await gotoCards();

    fireEvent.click(await screen.findByTestId("card-row-toggle"));
    fireEvent.click(screen.getByTestId("card-edit-button"));

    const frontInput = screen.getByTestId("card-edit-front") as HTMLTextAreaElement;
    const backInput = screen.getByTestId("card-edit-back") as HTMLTextAreaElement;

    // Identical sides (case/whitespace-insensitive) — the backend's own
    // `sides_differ` rule (`lib/flashcard-caps.ts`) — disables Save.
    fireEvent.change(frontInput, { target: { value: "Same text" } });
    fireEvent.change(backInput, { target: { value: "same text" } });
    expect((screen.getByTestId("card-edit-save") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(frontInput, { target: { value: "New front" } });
    fireEvent.change(backInput, { target: { value: "New back" } });
    expect((screen.getByTestId("card-edit-save") as HTMLButtonElement).disabled).toBe(false);

    const summaryRequestsBefore = reviewSummaryRequestCount();
    fireEvent.click(screen.getByTestId("card-edit-save"));

    await waitFor(() => {
      expect(screen.getByTestId("card-row-front").textContent).toBe("New front");
    });
    expect(screen.queryByTestId("card-edit-front")).toBeNull();
    expect(cardUpdateRequestBodies()).toEqual([
      { card_id: "c1", front: "New front", back: "New back" },
    ]);
    // The mutation's own `invalidateQueries({queryKey: FLASHCARDS_QUERY_PREFIX})`
    // (AL-410 plan §6) reaches the header pill's summary query too — the same
    // shared prefix that also backs the Daily queue's cached copy of this card.
    await waitFor(() => {
      expect(reviewSummaryRequestCount()).toBeGreaterThan(summaryRequestsBefore);
    });
  });

  it("[edit] a failed save surfaces a retry notice and leaves the row unchanged", async () => {
    useFlashcardsSession();
    configureCardUpdateFailure(true);
    seedCard({
      id: "c1",
      front: "Original front",
      back: "Original back",
      due_on: dueInDays(2),
      source: DEGRADED_SOURCE,
    });
    await gotoCards();

    fireEvent.click(await screen.findByTestId("card-row-toggle"));
    fireEvent.click(screen.getByTestId("card-edit-button"));
    fireEvent.change(screen.getByTestId("card-edit-front"), { target: { value: "New front" } });
    fireEvent.click(screen.getByTestId("card-edit-save"));

    await screen.findByTestId("card-edit-error");
    // Still in edit mode, with the row's real text untouched underneath.
    expect(screen.queryByTestId("card-edit-front")).not.toBeNull();
  });

  it("[search] typing into the search box reaches the fake's `q` matcher, front or back", async () => {
    useFlashcardsSession();
    seedCard({
      id: "c1",
      front: "What does `extends` mean?",
      back: "It constrains T.",
      due_on: dueInDays(3),
      source: LINKED_SOURCE,
    });
    seedCard({
      id: "c2",
      front: "What is SELECT for?",
      // The needle below only matches this card's *back* — proving the
      // search reaches both sides, not just `front` (AL-410 plan §2, and the
      // fake's own `matchesCardFilters`).
      back: "Choosing which pillars come back.",
      due_on: dueInDays(0),
      source: DEGRADED_SOURCE,
    });
    await gotoCards();

    await screen.findAllByTestId("card-row");
    expect(screen.getAllByTestId("card-row")).toHaveLength(2);

    fireEvent.change(screen.getByTestId("cards-search-input"), {
      target: { value: "pillars" },
    });

    // Debounced (`SEARCH_DEBOUNCE_MS`) — the list settles to the one matching
    // row rather than firing a request per keystroke.
    await waitFor(() => {
      const rows = screen.getAllByTestId("card-row");
      expect(rows).toHaveLength(1);
      expect(rows[0].textContent).toContain("What is SELECT for?");
    });

    // Clearing the box returns to the unfiltered list — the fake's `q === ""`
    // branch, not "no cards match".
    fireEvent.change(screen.getByTestId("cards-search-input"), { target: { value: "" } });
    await waitFor(() => {
      expect(screen.getAllByTestId("card-row")).toHaveLength(2);
    });
  });

  it("[search] a query matching nothing reads as the filtered empty state", async () => {
    useFlashcardsSession();
    seedCard({
      id: "c1",
      front: "What does `extends` mean?",
      back: "It constrains T.",
      due_on: dueInDays(3),
      source: LINKED_SOURCE,
    });
    await gotoCards();

    await screen.findByTestId("card-row");
    fireEvent.change(screen.getByTestId("cards-search-input"), {
      target: { value: "nothing matches this" },
    });

    const empty = await screen.findByTestId("cards-empty");
    expect(empty.textContent).toMatch(/no cards match/i);
  });

  it("[load more] a page over the limit shows the button; clicking it reaches the fake's cursor", async () => {
    useFlashcardsSession();
    // 22 cards — one more than `getCards`' default `limit` of 20 — so the
    // fake's `limit + 1` over-fetch (`mocks/flashcards.ts`) actually reports a
    // `next_cursor`, and "Load more" has something to page into.
    for (let index = 0; index < 22; index += 1) {
      seedCard({
        id: `c${index}`,
        front: `Card number ${String(index).padStart(2, "0")}`,
        back: "back",
        due_on: dueInDays(1),
        source: DEGRADED_SOURCE,
        // Spaced apart and strictly increasing with `index` so
        // newest-kept-first ordering (`kept_at DESC, id DESC`) is exactly
        // reverse-index order — deterministic regardless of how fast the
        // seeding loop above actually runs.
        keptAt: new Date(2026, 6, 1, 0, 0, index).toISOString(),
      });
    }
    await gotoCards();

    await screen.findAllByTestId("card-row");
    expect(screen.getAllByTestId("card-row")).toHaveLength(20);
    // Newest-kept-first: card 21 (the last seeded) leads page one.
    expect(screen.getAllByTestId("card-row")[0].textContent).toContain("Card number 21");

    const loadMore = screen.getByTestId("cards-load-more");
    fireEvent.click(loadMore);

    await waitFor(() => {
      expect(screen.getAllByTestId("card-row")).toHaveLength(22);
    });
    // The second, smaller page's own cards are now on screen too…
    expect(screen.getAllByTestId("card-row")[21].textContent).toContain("Card number 00");
    // …and with nothing left past the 22nd, the button is gone rather than
    // offering a page the fake would answer empty.
    expect(screen.queryByTestId("cards-load-more")).toBeNull();
  });

  it("[delete] the two-step confirm removes the row only after Delete is confirmed", async () => {
    useFlashcardsSession();
    seedCard({
      id: "c1",
      front: "Keep me",
      back: "back",
      due_on: dueInDays(2),
      source: DEGRADED_SOURCE,
    });
    seedCard({
      id: "c2",
      front: "Delete me",
      back: "back",
      due_on: dueInDays(2),
      source: DEGRADED_SOURCE,
    });
    await gotoCards();

    const rows = await screen.findAllByTestId("card-row");
    const targetRow = rows.find((row) => row.textContent?.includes("Delete me"));
    if (!targetRow) throw new Error("row not found");

    fireEvent.click(within(targetRow).getByTestId("card-row-toggle"));
    fireEvent.click(within(targetRow).getByTestId("card-delete-button"));
    expect(within(targetRow).queryByTestId("card-delete-confirm")).not.toBeNull();

    // Cancelling leaves both rows exactly as they were, and restores this
    // row's own Delete button (still expanded — cancel never re-collapses
    // the row) rather than dropping back to the confirm's own vanished button.
    fireEvent.click(within(targetRow).getByTestId("card-delete-cancel"));
    expect(screen.queryByTestId("card-delete-confirm")).toBeNull();
    expect(screen.getAllByTestId("card-row")).toHaveLength(2);

    fireEvent.click(within(targetRow).getByTestId("card-delete-button"));
    fireEvent.click(within(targetRow).getByTestId("card-delete-confirm"));

    await waitFor(() => {
      expect(screen.getAllByTestId("card-row")).toHaveLength(1);
    });
    expect(screen.getByTestId("card-row").textContent).toContain("Keep me");
    expect(cardDeleteRequestIds()).toEqual(["c2"]);
  });
});
