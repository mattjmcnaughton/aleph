// W19 — **Undo** a Change, and the **Change history** that records it
// (PRD §8, §5.5).
//
// A Change is undoable until the learner engages with anything it created or
// revised; undo restores the path exactly, and the record of what happened
// survives either way. This spec walks that whole promise on a phone: apply,
// read the history, undo, and find the path back where it started with the row
// still there, marked undone.
//
// Two claims worth naming:
//
//  1. **Undo is a status, never a delete.** The undone Change stays in the
//     history — the record is of what happened, not of what is currently true.
//  2. **The history belongs to the path, not the thread.** "New conversation"
//     empties the rail and leaves every row standing (TDD D3): an applied Change
//     is real path structure, and clearing a conversation could not take it back.
//
// > **Scope note (AL-331 → AL-360).** See `w17.spec.ts` — AL-360 owns the full
// > W17–W21 set and should extend these rather than start a second suite.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import { createPath, railLessons, uniqueTopic } from "../fixtures/journey";
import {
  ADDED_LESSON_TITLE_PREFIX,
  ADDITION_LESSON_COUNT,
  applyProposal,
  askForAddition,
  changeRows,
  closeChangeHistory,
  fetchChanges,
  openChangeHistory,
  openShapingRail,
  proposalCards,
  undoNewestChange,
} from "../fixtures/shaping";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W19 undo a change", { tag: "@w19" }, () => {
  test("undo restores the path exactly and leaves the record standing", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Sourdough starters"));
    const before = await railLessons(page).count();

    await openShapingRail(page);
    const card = await askForAddition(page);
    await applyProposal(page, card);
    await expect(railLessons(page)).toHaveCount(before + ADDITION_LESSON_COUNT);

    // --- the record ------------------------------------------------------------
    await openChangeHistory(page);
    await expect(changeRows(page)).toHaveCount(1);
    const newest = changeRows(page).first();
    await expect(newest).toHaveAttribute("data-status", "applied");
    // Plain language, in the server's own summary — a record, not a payload.
    await expect(newest.getByTestId("shaping-rail-history-kind")).toContainText("Added lessons");

    // --- undo ------------------------------------------------------------------
    await undoNewestChange(page);

    // Restored exactly: the rows the Change added are gone and nothing else is.
    await closeChangeHistory(page);
    await expect(railLessons(page)).toHaveCount(before);
    await expect(
      page
        .getByTestId("path-rail")
        .locator("button[data-unlock-state]", { hasText: ADDED_LESSON_TITLE_PREFIX }),
    ).toHaveCount(0);

    // Undo is a status, never a delete — the row is still in the history.
    const { changes } = await fetchChanges(page, pathId);
    expect(changes).toHaveLength(1);
    expect(changes[0].status).toBe("undone");
    expect(changes[0].undone_at).not.toBeNull();

    // And the Proposal that made it reads `undone` on the next thread read.
    await expect(proposalCards(page).last()).toHaveAttribute("data-state", "undone");
  });

  test("only the newest live Change offers undo", async ({ page }) => {
    await createPath(page, uniqueTopic("Harbour dredging"));
    await openShapingRail(page);

    await applyProposal(page, await askForAddition(page));
    await applyProposal(page, await askForAddition(page));

    await openChangeHistory(page);
    await expect(changeRows(page)).toHaveCount(2);

    // Newest first. The one on top of the stack is the one that can come off it;
    // the older row says why plainly rather than offering a doomed tap.
    await expect(changeRows(page).nth(0).getByTestId("shaping-rail-history-undo")).toBeVisible();
    await expect(changeRows(page).nth(1).getByTestId("shaping-rail-history-undo")).toHaveCount(0);
    await expect(
      changeRows(page).nth(1).getByTestId("shaping-rail-history-not-latest"),
    ).toContainText("Undo the newest change first");

    // Undoing the newest one hands the affordance to the one below it.
    await undoNewestChange(page);
    await expect(changeRows(page).nth(1).getByTestId("shaping-rail-history-undo")).toBeVisible();
  });

  test("the history survives new conversation — it belongs to the path", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Peat bogs"));
    await openShapingRail(page);
    await applyProposal(page, await askForAddition(page));

    // New conversation confirms first, then clears the thread.
    await page.getByTestId("shaping-rail-new-conversation").click();
    await page.getByTestId("shaping-rail-new-conversation-confirm").click();
    await expect(page.getByTestId("shaping-rail-empty")).toBeVisible();
    await expect(proposalCards(page)).toHaveCount(0);

    // The Change outlived the thread that proposed it — on the wire and on screen.
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(1);
    await openChangeHistory(page);
    await expect(changeRows(page)).toHaveCount(1);
    await expect(changeRows(page).first()).toHaveAttribute("data-status", "applied");
  });
});
