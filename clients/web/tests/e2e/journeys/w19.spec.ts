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
// > W17–W21 set and extended this file in place: the last two tests below are
// > its half (a **Revision** undone byte-identical, and the engagement that
// > closes an undo window for good).

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  answerQuickCheck,
  createPath,
  expectLessonContent,
  gotoPath,
  openLessonAt,
  railLessons,
  uniqueTopic,
  waitForLessonGenerated,
} from "../fixtures/journey";
import {
  ADDED_LESSON_TITLE_PREFIX,
  ADDITION_LESSON_COUNT,
  REVISED_LESSON_TITLE_PREFIX,
  UNDO_ENGAGED_COPY,
  applyProposal,
  askForAddition,
  askForRevision,
  changeRows,
  closeChangeHistory,
  closeShapingRail,
  fetchChanges,
  openChangeHistory,
  openShapingRail,
  proposalCards,
  undoNewestChange,
} from "../fixtures/shaping";
// The Phase 1 wire reads live in `tutor.ts` (see `w17.spec.ts`).
import { fetchLesson, openLessonId } from "../fixtures/tutor";

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

  test("undoing a Revision puts the lesson back byte-identical", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Kiln firing"));
    const total = await railLessons(page).count();

    // The lesson as it stood before anything shaped it: title, Read passage,
    // Quick check, state. Nothing is engaged, so the first row is the target.
    await waitForLessonGenerated(page, 0);
    await openLessonAt(page, 0);
    const targetId = await openLessonId(page);
    await expectLessonContent(page);
    const before = await fetchLesson(page, targetId);
    await gotoPath(page, pathId);

    await openShapingRail(page);
    await applyProposal(page, await askForRevision(page));
    // The Revision really happened: the row wears its new title, and its content
    // was cleared for Phase 1 to rewrite.
    await expect(railLessons(page).nth(0)).toContainText(REVISED_LESSON_TITLE_PREFIX);
    expect(await fetchLesson(page, targetId)).not.toEqual(before);

    // Let the rewrite finish before undoing it. A Revision target that is
    // mid-write is refused (`target_generating`, TDD §5.7) — restoring a
    // snapshot under a live claim would let the worker's write land on top of
    // the restored row — so this is the learner's own "wait a moment", asserted
    // as a rail state rather than slept through.
    await closeShapingRail(page);
    await waitForLessonGenerated(page, 0);
    await openShapingRail(page);

    await openChangeHistory(page);
    await expect(changeRows(page).first().getByTestId("shaping-rail-history-kind")).toContainText(
      "Revised a lesson",
    );
    await undoNewestChange(page);
    await closeChangeHistory(page);

    // Restored exactly — and "exactly" is the whole payload, not a spot check:
    // the passage and the Quick check come back out of the Change's own record
    // of what it replaced (docs/api.md), and the slot never moved.
    await expect(railLessons(page)).toHaveCount(total);
    expect(await fetchLesson(page, targetId)).toEqual(before);
    const { changes } = await fetchChanges(page, pathId);
    expect(changes).toHaveLength(1);
    expect(changes[0].status).toBe("undone");
  });

  test("engaging a Change's lesson closes its undo window, and the history says why", async ({
    page,
  }) => {
    const pathId = await createPath(page, uniqueTopic("Tide mills"));

    await openShapingRail(page);
    await applyProposal(page, await askForAddition(page));
    // Nothing is engaged, so the Addition lands at the front: its first lesson is
    // the one the learner is looking at next.
    await closeShapingRail(page);
    await expect(railLessons(page).nth(0)).toContainText(ADDED_LESSON_TITLE_PREFIX);

    // An **Attempt** is engagement — the lesson need not be completed for the
    // window to close (CONTEXT.md: *Engaged*).
    await openLessonAt(page, 0);
    await expectLessonContent(page);
    await answerQuickCheck(page, 0);
    await gotoPath(page, pathId);

    await openShapingRail(page);
    await openChangeHistory(page);
    const newest = changeRows(page).first();
    // The button is still offered: engagement is derived server-side and can
    // change between this list rendering and the tap, so the client never
    // pre-disables for it (`change-history-sheet.tsx`). It taps, and is told.
    await newest.getByTestId("shaping-rail-history-undo").click();
    await expect(newest.getByTestId("shaping-rail-history-undo-error")).toContainText(
      UNDO_ENGAGED_COPY,
    );
    // And then the affordance goes, because this one is closed for good: the
    // Change is permanent history now, not a mistake to be corrected.
    await expect(newest.getByTestId("shaping-rail-history-undo")).toHaveCount(0);
    await expect(newest).toHaveAttribute("data-status", "applied");

    // The server is the rule, and it kept the Change and the lessons it made.
    const { changes } = await fetchChanges(page, pathId);
    expect(changes).toHaveLength(1);
    expect(changes[0].status).toBe("applied");
    expect(changes[0].undone_at).toBeNull();
    await closeChangeHistory(page);
    await closeShapingRail(page);
    await expect(
      page
        .getByTestId("path-rail")
        .locator("button[data-unlock-state]", { hasText: ADDED_LESSON_TITLE_PREFIX }),
    ).toHaveCount(ADDITION_LESSON_COUNT);
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
