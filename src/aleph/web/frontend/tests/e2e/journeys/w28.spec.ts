// W28 — Browsing kept cards: edit one, delete another, and the deleted card
// is gone for good (AL-410 plan §7: "keep cards → edit one → delete one → the
// deleted card never appears in the queue").
//
// Runs as `UNVERIFIED_USER`, sharing that account with W27 rather than the
// busier `ADMIN_USER` W24-W26 share (`fixtures/auth.ts`) — the same account-
// isolation reasoning W27's own header states, extended to a second tenant.
// Unlike W27 (which only ever reads a citation), this spec asserts its own
// two freshly-kept cards are *present* in the day's selected queue, and
// `select_daily_queue` is blind to `satisfied` (TDD §5.1) — once
// `UNVERIFIED_USER`'s candidate pool exceeds the daily cap, a legitimately
// unselected card fails that assertion and no rewrite rescues it (see W27's
// own header). Two disciplines keep this spec from ever growing that pool:
//
//   1. It grades its one surviving card (the edited one — the other is
//      deleted below) `got_it` before finishing, which pushes `due_on` past
//      today the same way any ordinarily-reviewed card advances — so this
//      spec leaves no permanently-due residue on any run, rather than
//      doubling the one card W27 already leaves behind forever.
//   2. Its queue assertions are scoped to the path this spec itself created
//      (`fetchReviewQueue`'s own `pathId` parameter), not the whole account.
//      `path_id` filtering runs *after* selection (`services/reviews.py`), so
//      scoping does not change whether a card is drawn — it only keeps the
//      assertions reading the two cards this spec actually cares about
//      rather than whatever else the account happens to be carrying.
//
// That leaves exactly W27's one per-run residual card as the account's only
// accumulating candidate (it never grades — see its own header for why), which
// is what keeps `len(candidates) <= cap` true indefinitely and both specs'
// presence assertions honest.

import { type Locator, type Page, expect, test } from "@playwright/test";
import { UNVERIFIED_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  completeLessonAndKeepDrafts,
  createPath,
  fetchReviewQueue,
  gradeCurrentCard,
  shiftFlashcardDue,
  uniqueTopic,
  waitForSurface,
} from "../fixtures/journey";

test.use({ storageState: UNVERIFIED_STORAGE_STATE });

const EDITED_FRONT = "Edited front text";
const EDITED_BACK = "This is the edited back text, with a bit more detail than before.";

/** One `/cards` row by its (still-original) front text. `hasText` matches the
 *  whole row's rendered text, so this only needs to run before any edit
 *  changes what that text is — every caller below reads `data-card-id` off
 *  the match immediately, then addresses the row by id from there on, the
 *  same way `fixtures/journey.ts`'s own `pathRow` addresses a switcher row —
 *  because a locator still filtered on the *old* front text stops resolving
 *  the moment that text is no longer what is rendered. */
async function cardIdByFront(page: Page, front: string): Promise<string> {
  const row = page.getByTestId("card-row").filter({ hasText: front }).first();
  const cardId = await row.getAttribute("data-card-id");
  if (!cardId) {
    throw new Error(`no /cards row found for front ${JSON.stringify(front)}`);
  }
  return cardId;
}

function cardRowById(page: Page, cardId: string): Locator {
  return page.locator(`[data-testid="card-row"][data-card-id="${cardId}"]`);
}

test.describe("W28 browsing kept cards: edit one, delete another", { tag: "@w28" }, () => {
  test("an edit round-trips into the row and a delete removes the card from the queue", async ({
    page,
  }) => {
    const pathId = await createPath(page, uniqueTopic("Tectonic plates"));
    const [frontA, frontB] = await completeLessonAndKeepDrafts(page, 0, 2);

    await shiftFlashcardDue(page, { days: 1 });

    // Sanity check on the premise above: both freshly kept cards are due and
    // unsatisfied, so both are real candidates the queue actually selected —
    // never assumed, the same discipline `fetchReviewQueue`'s other callers
    // (W25-W27) already hold. Scoped to this spec's own path (header, point
    // 2) — this account also carries W27's one residual card, which a scoped
    // read never has to reason about.
    const beforeEdits = await fetchReviewQueue(page, pathId);
    const frontsBefore = beforeEdits.cards.map((card) => card.front);
    expect(frontsBefore).toContain(frontA);
    expect(frontsBefore).toContain(frontB);

    await page.goto("/cards");
    await waitForSurface(page, "cards-page");

    // --- edit the first card -------------------------------------------------
    const cardIdA = await cardIdByFront(page, frontA);
    const rowA = cardRowById(page, cardIdA);
    await rowA.getByTestId("card-row-toggle").click();
    await rowA.getByTestId("card-edit-button").click();
    await rowA.getByTestId("card-edit-front").fill(EDITED_FRONT);
    await rowA.getByTestId("card-edit-back").fill(EDITED_BACK);
    await rowA.getByTestId("card-edit-save").click();
    // Editing closes the form and the row shows the saved text — never the
    // pre-edit front, and never a second, duplicate row.
    await expect(rowA.getByTestId("card-row-front")).toHaveText(EDITED_FRONT, {
      timeout: ACTION_TIMEOUT,
    });
    await expect(page.getByTestId("card-edit-front")).toHaveCount(0);

    // --- delete the second card, behind the two-step confirm -----------------
    const cardIdB = await cardIdByFront(page, frontB);
    const rowB = cardRowById(page, cardIdB);
    await rowB.getByTestId("card-row-toggle").click();
    await rowB.getByTestId("card-delete-button").click();
    await expect(rowB.getByTestId("card-delete-confirm")).toBeVisible();
    await rowB.getByTestId("card-delete-confirm").click();
    await expect(rowB).toHaveCount(0, { timeout: ACTION_TIMEOUT });

    // --- the deleted card never appears in the queue --------------------------
    // Scoped to this spec's own path, same as `beforeEdits` above (header,
    // point 2).
    const afterDelete = await fetchReviewQueue(page, pathId);
    const frontsAfter = afterDelete.cards.map((card) => card.front);
    expect(frontsAfter).not.toContain(frontB);
    expect(afterDelete.cards.some((card) => card.card_id === cardIdB)).toBe(false);
    // The edit round-tripped into the queue's own copy too — one invalidation
    // reaching both surfaces (AL-410 plan §6), not just `/cards` in isolation.
    expect(frontsAfter).toContain(EDITED_FRONT);
    expect(frontsAfter).not.toContain(frontA);

    // The row survives a reload of `/cards` itself — not merely absent from
    // this one render.
    await page.reload();
    await waitForSurface(page, "cards-page");
    await expect(cardRowById(page, cardIdB)).toHaveCount(0);
    await expect(cardRowById(page, cardIdA).getByTestId("card-row-front")).toHaveText(
      EDITED_FRONT,
      {
        timeout: ACTION_TIMEOUT,
      },
    );

    // --- leave no residue: grade the one surviving card (header, point 1) ----
    // `cardIdB` is gone, so the only candidate left in this path is `cardIdA`
    // (the edited one) — scoping the session to `pathId` means it is the only
    // card that can show, with no `bringToFront` search needed the way W27's
    // shared-account version does.
    await page.goto(`/review?path=${pathId}`);
    await expect(page.getByTestId("review-session").getByText(/^Card \d+ of \d+$/)).toBeVisible({
      timeout: ACTION_TIMEOUT,
    });
    await expect(page.getByTestId("review-card-front")).toHaveText(EDITED_FRONT);
    await gradeCurrentCard(page, "got_it");
  });
});
