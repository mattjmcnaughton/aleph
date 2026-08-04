// W26 — A lapse resurfaces without costing a slot (PRD §5: "Grade Again → the
// card returns later in the session → the session is still ten distinct
// cards"; Phase 3 TDD D8, §5.3, §11).
//
// D8: a queue card is satisfied only once its most recent review today is
// `got_it`; `again` leaves it unsatisfied and it is re-served after every
// never-attempted card, with no cap on re-shows and no cost to `total` — the
// cap counts distinct cards, so a lapse cannot cost a slot by construction.
// This journey grades the day's first card `again`, works the untouched cards
// behind it, and confirms the lapsed card returns last rather than
// disappearing or getting a slot of its own.
//
// Runs as `ADMIN_USER`, sharing that account with W24 and W25 in the
// flashcards project (`playwright.config.ts`, `fixtures/auth.ts`) — and W25
// runs immediately before this spec, draining its own selected ten but
// necessarily leaving some of its twelve (and W24's own residue) still
// due-and-ungraded, because only ten of any backlog fit the cap
// (`select_daily_queue`, TDD §5.1). So this reads the actual serve order off
// the wire rather than assuming a clean ten of its own cards: `order` is
// whatever `initial.cards` holds, and every loop bound below is relative to
// its length rather than a literal 9 or 10 — the property under test (a
// lapse costs no slot, and returns last) holds regardless of whose cards fill
// the ten.

import { expect, test } from "@playwright/test";
import { ADMIN_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  completeLessonAndKeepDrafts,
  createPath,
  fetchReviewQueue,
  gradeCurrentCard,
  shiftFlashcardDue,
  uniqueTopic,
} from "../fixtures/journey";

test.use({ storageState: ADMIN_STORAGE_STATE });

/** The header's own `Card {completed+1} of {total}` rule (`routes/review.tsx`). */
function cardHeaderPattern(completed: number, total: number): RegExp {
  const position = Math.min(completed + 1, total);
  return new RegExp(`^Card ${position} of ${total}$`);
}

test.describe("W26 a lapse resurfaces without costing a slot", { tag: "@w26" }, () => {
  test("an Again grade keeps the denominator and returns the card behind the untouched ones", async ({
    page,
  }) => {
    await createPath(page, uniqueTopic("River deltas"));
    await completeLessonAndKeepDrafts(page, 0, 4);
    await completeLessonAndKeepDrafts(page, 1, 4);
    await completeLessonAndKeepDrafts(page, 2, 4);

    await shiftFlashcardDue(page, { days: 1 });

    await page.goto("/review");
    await expect(page.getByTestId("review-session").getByText(/^Card \d+ of \d+$/)).toBeVisible({
      timeout: ACTION_TIMEOUT,
    });

    // The actual serve order this session holds, read once before grading
    // starts — never assumed, for the reason above.
    const initial = await fetchReviewQueue(page);
    expect(initial.total).toBe(10);
    const order = initial.cards.map((card) => card.front);
    const headerBeforeLapse = cardHeaderPattern(initial.completed, initial.total);

    await expect(page.getByTestId("review-card-front")).toHaveText(order[0]);
    await gradeCurrentCard(page, "again");

    // The lapse costs no slot: the denominator is unchanged, and neither is
    // the numerator — `completed` only counts a *satisfied* card, and an
    // `again` is not one (D8).
    await expect(page.getByTestId("review-session").getByText(headerBeforeLapse)).toBeVisible({
      timeout: ACTION_TIMEOUT,
    });

    // The untouched cards serve first, in their original order — the lapsed
    // card has not cut in line.
    for (let index = 1; index < order.length; index += 1) {
      await expect(page.getByTestId("review-card-front")).toHaveText(order[index], {
        timeout: ACTION_TIMEOUT,
      });
      await gradeCurrentCard(page, "got_it");
    }

    // It returns last — after every untouched card, not before — and grading
    // it finishes the session.
    await expect(page.getByTestId("review-card-front")).toHaveText(order[0], {
      timeout: ACTION_TIMEOUT,
    });
    await gradeCurrentCard(page, "got_it");

    await expect(page.getByTestId("session-complete")).toBeVisible({ timeout: ACTION_TIMEOUT });
  });
});
