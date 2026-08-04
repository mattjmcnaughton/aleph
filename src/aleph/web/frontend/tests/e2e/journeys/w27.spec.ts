// W27 — A card survives its source lesson (PRD §5: "Delete the path a kept
// card came from → the card still reviews, with a degraded citation"; Phase 3
// TDD D12, §4 item 4, §11).
//
// Deleting a path cascades to its lessons, which nulls both of the card's
// source FKs in the same delete (`ON DELETE SET NULL`, TDD §4) — but a card
// belongs to the learner, not the lesson (§4 item 3), so it keeps reviewing.
// Both titles are copied onto the card at draft time (D12) precisely so the
// citation's *text* survives even though its link cannot: before deletion
// `review-card-source` renders an `<a>`, after it renders the same line as
// plain text.
//
// Runs as `UNVERIFIED_USER`, alone — the one flashcards journey with no
// account-mate at all (`fixtures/auth.ts`'s own note on why). W24-W26 can
// share `ADMIN_USER` and read whatever queue state that account actually
// holds (their own headers), because `total`/`completed`/serve-order are all
// still correctly derivable under residue. W27 cannot: it keeps exactly one
// card, and `select_daily_queue` is blind to `satisfied` (TDD §5.1), so that
// one card only lands in the selected ten if the account's total candidate
// count is small enough — no rewritten assertion can rescue a card the
// selection legitimately left out. `bringToFront` still grades whatever is
// in front of it first (defensive, not load-bearing here), bounded by the
// queue's own `total`, so a card that never surfaces at all still fails the
// test rather than hanging it.

import { type Page, expect, test } from "@playwright/test";
import { UNVERIFIED_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  completeLessonAndKeepDrafts,
  createPath,
  currentReviewFront,
  fetchReviewQueue,
  gotoSwitcher,
  gradeCurrentCard,
  pathRow,
  shiftFlashcardDue,
  uniqueTopic,
} from "../fixtures/journey";

test.use({ storageState: UNVERIFIED_STORAGE_STATE });

async function bringToFront(page: Page, front: string): Promise<void> {
  const { total } = await fetchReviewQueue(page);
  for (let attempt = 0; attempt <= total; attempt += 1) {
    if ((await currentReviewFront(page)) === front) return;
    await gradeCurrentCard(page, "got_it");
  }
  throw new Error(`card with front ${JSON.stringify(front)} never surfaced in today's queue`);
}

test.describe("W27 a card survives its source lesson", { tag: "@w27" }, () => {
  test("deleting the source path degrades the citation but keeps the card reviewable", async ({
    page,
  }) => {
    const pathId = await createPath(page, uniqueTopic("Glacial till"));
    const [front] = await completeLessonAndKeepDrafts(page, 0, 1);
    await shiftFlashcardDue(page, { days: 1 });

    // --- before deletion: a real citation link --------------------------------
    await page.goto("/review");
    await expect(page.getByTestId("review-session").getByText(/^Card \d+ of \d+$/)).toBeVisible({
      timeout: ACTION_TIMEOUT,
    });
    await bringToFront(page, front);
    await expect(page.getByTestId("review-card-front")).toHaveText(front);
    const sourceBefore = page.getByTestId("review-card-source");
    await expect(sourceBefore).toHaveText(/^From .+ · .+$/);
    await expect(sourceBefore.locator("a")).toHaveCount(1);

    // --- delete the source path, cascading its one lesson ---------------------
    await gotoSwitcher(page);
    const row = pathRow(page, pathId);
    await row.getByTestId("path-delete-button").click();
    await expect(row.getByTestId("path-delete-confirm")).toBeVisible();
    await row.getByTestId("path-delete-confirm").click();
    await expect(row).toHaveCount(0, { timeout: ACTION_TIMEOUT });

    // --- the card still reviews, degraded rather than dangling ----------------
    await page.goto("/review");
    await expect(page.getByTestId("review-session").getByText(/^Card \d+ of \d+$/)).toBeVisible({
      timeout: ACTION_TIMEOUT,
    });
    await bringToFront(page, front);
    await expect(page.getByTestId("review-card-front")).toHaveText(front);
    const sourceAfter = page.getByTestId("review-card-source");
    // D12: the copied titles keep the citation line's own text intact even
    // though the lesson it named is gone — a title with no href, never a
    // blank line and never a dangling link.
    await expect(sourceAfter).toHaveText(/^From .+ · .+$/);
    await expect(sourceAfter.locator("a")).toHaveCount(0);
  });
});
