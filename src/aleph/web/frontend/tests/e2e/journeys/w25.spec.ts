// W25 — The daily queue caps and holds (PRD §5: "With more than ten cards due,
// the queue is ten; reloading returns the same ten in the same order"; Phase 3
// TDD D2/D3, §5.1/§5.3, §15).
//
// §15 calls the pin behind this journey "the highest-consequence,
// lowest-visibility surface" in the phase: the day's selected set is a pure
// derivation over `(candidates, today, user_id)`, never stored, so a reload
// must re-derive the identical ten rather than reroll them mid-session. Three
// lessons keeping four cards each earns twelve real due cards (D15's whole
// discipline — no seeder), comfortably over the cap, so `select_daily_queue`'s
// 7-overdue/3-random split is actually exercised rather than the `<= cap`
// no-op path.
//
// Runs as `ADMIN_USER`, sharing that account with W24 and W26 in the
// flashcards project (`playwright.config.ts`, `fixtures/auth.ts` — never
// `DEV_USER`, which W1-W23 leave far more polluted). W24 runs first and
// grades two cards `got_it` before this spec starts, and `select_daily_queue`
// is blind to `satisfied` (TDD §5.1) — a graded card still competes for the
// day's ten slots — so this reads the queue off the wire rather than
// asserting a clean-room `completed === 0`: `total` is the one number the cap
// pins regardless of whose cards fill it, `completed` is whatever it actually
// is, and the header text is derived from both rather than hard-coded.

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

test.describe("W25 the daily queue caps and holds", { tag: "@w25" }, () => {
  test("twelve due cards cap at ten and the day's set survives a reload", async ({ page }) => {
    await createPath(page, uniqueTopic("Sediment layers"));
    await completeLessonAndKeepDrafts(page, 0, 4);
    await completeLessonAndKeepDrafts(page, 1, 4);
    await completeLessonAndKeepDrafts(page, 2, 4);

    await shiftFlashcardDue(page, { days: 1 });

    await page.goto("/review");
    await expect(page.getByTestId("review-session").getByText(/^Card \d+ of \d+$/)).toBeVisible({
      timeout: ACTION_TIMEOUT,
    });

    // The queue this account actually holds — never assumed fresh (see the
    // header note on the shared `ADMIN_USER` account).
    const before = await fetchReviewQueue(page);
    expect(before.total).toBe(10);
    expect(before.cards).toHaveLength(before.total - before.completed);
    await expect(
      page
        .getByTestId("review-session")
        .getByText(cardHeaderPattern(before.completed, before.total)),
    ).toBeVisible({ timeout: ACTION_TIMEOUT });
    const firstFront = await page.getByTestId("review-card-front").innerText();
    expect(firstFront.trim()).toBe(before.cards[0].front);

    // --- the pin: a reload re-derives the same ten, it does not reroll them ---
    await page.reload();
    await expect(
      page
        .getByTestId("review-session")
        .getByText(cardHeaderPattern(before.completed, before.total)),
    ).toBeVisible({ timeout: ACTION_TIMEOUT });
    await expect(page.getByTestId("review-card-front")).toHaveText(firstFront.trim(), {
      timeout: ACTION_TIMEOUT,
    });

    const after = await fetchReviewQueue(page);
    expect(after.total).toBe(before.total);
    expect(after.completed).toBe(before.completed);
    // The same ten, in the same order — not merely the same set (D3's own
    // "returned order is deterministic" clause, TDD §5.1).
    expect(after.cards.map((card) => card.card_id)).toEqual(
      before.cards.map((card) => card.card_id),
    );

    // --- work through this session's own unsatisfied cards -------------------
    // This grades exactly `before.cards.length` cards — the day's selected
    // ten minus whatever arrived already satisfied — which finishes the
    // session. It does **not** leave the shared account free of due residue:
    // twelve cards were earned above but only ten fit the cap, so up to two
    // of *this spec's own* cards (plus whatever W24 left behind and this
    // session's draw did not select) remain due-and-ungraded for whatever
    // runs after it in this project. That is exactly the residue W26 is
    // written to read off the wire rather than assume away.
    for (let graded = 0; graded < before.cards.length; graded += 1) {
      await gradeCurrentCard(page, "got_it");
    }
    await expect(page.getByTestId("session-complete")).toBeVisible({ timeout: ACTION_TIMEOUT });
  });
});
