// W24 — Finishing a lesson produces a due card (PRD §5: "Complete a lesson →
// drafts appear → keep two → they are due for review"; Phase 3 TDD D5/D6,
// §5.2, §11).
//
// A freshly kept card is due tomorrow, never today (TDD §14 — it falls out of
// entering the ladder at rung 0) — so this drives the one clock seam that
// exists for exactly this (D15): `shiftFlashcardDue` backdates the two cards
// this test earns through the real drafting + keep flow by one day, in the
// harness's stub backend only (`scripts/e2e_backend.py`), never fabricating a
// card the app itself did not create.
//
// Runs as `ADMIN_USER`, not the suite's `DEV_USER`: W24-W27 share the
// `select_daily_queue` candidate pool with *every* card graded that same
// calendar day (`due_on <= today` **or** `a review exists today`, and the
// selection is blind to `satisfied` — TDD §5.1/§5.3), so sharing `DEV_USER`
// with W1-W23 would leave this suite's assertions competing against ~30
// other completions' worth of residue. `ADMIN_USER` is otherwise idle (W1's
// own admin sub-test opens a lesson but never completes one), and it runs
// first among W24-W26 (`playwright.config.ts`'s dedicated project, alphabetical
// within it), so it is genuinely fresh here — the one place in this trio a
// literal "2"/"of 2" assertion is still honest.

import { expect, test } from "@playwright/test";
import { ADMIN_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  completeLessonAndKeepDrafts,
  createPath,
  gradeCurrentCard,
  shiftFlashcardDue,
  uniqueTopic,
} from "../fixtures/journey";

test.use({ storageState: ADMIN_STORAGE_STATE });

test.describe("W24 finishing a lesson produces a due card", { tag: "@w24" }, () => {
  test("keeping two of four drafts and shifting them due surfaces both in review", async ({
    page,
  }) => {
    await createPath(page, uniqueTopic("Tide pools"));
    await completeLessonAndKeepDrafts(page, 0, 2);

    await shiftFlashcardDue(page, { days: 1 });

    // The pill is app-header chrome, present on every route (TDD §8) — a
    // reload of wherever the last helper left the browser is enough to pick
    // up the two now-due cards.
    await page.reload();
    await expect(page.getByTestId("review-pill")).toHaveText("2 due", { timeout: ACTION_TIMEOUT });

    await page.getByTestId("review-pill").click();
    await expect(page.getByTestId("review-session").getByText("Card 1 of 2")).toBeVisible({
      timeout: ACTION_TIMEOUT,
    });

    // Both cards review — reveal + grade each in turn, all the way to the
    // end of a two-card session.
    await gradeCurrentCard(page, "got_it");
    await expect(page.getByTestId("review-session").getByText("Card 2 of 2")).toBeVisible({
      timeout: ACTION_TIMEOUT,
    });
    await gradeCurrentCard(page, "got_it");

    await expect(page.getByTestId("session-complete")).toBeVisible({ timeout: ACTION_TIMEOUT });
  });
});
