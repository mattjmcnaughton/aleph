// W18 — Shape by **revising** (PRD §8, §5.4; TDD §5.6/D7).
//
// The other half of the shaping vocabulary, and the narrower one: a **Revision**
// keeps a lesson's slot and re-teaches it, which is the single mutation the
// design allows between *generated* and *engaged*. So the journey is really two
// promises at once — the instruction reaches the regenerated content, and the
// work the learner has already done is not in reach at all.
//
// Three claims this spec exists to hold:
//
//  1. **The instruction travels.** Apply writes the Proposal's instruction to the
//     lesson's `revision_instruction`, Phase 1's untouched prompt carries it in
//     its revision block, and the regenerated Read passage comes back carrying
//     `REVISED_PASSAGE_MARKER`. Asserting the marker on screen is a *structural*
//     check of that whole chain (TDD §11) — no wording of the reply could stand
//     in for it.
//  2. **The engagement boundary is visible.** With the first lesson finished, the
//     Proposal targets the next one; the finished lesson is never marked, never
//     revised, and its payload is bit-identical afterwards.
//  3. **Nothing happens until Apply**, for revisions too. A pending Revision
//     marks a row and changes nothing behind it: the lesson still opens on the
//     content it always had.
//
// > Extends the shaping suite AL-331 started (`w17.spec.ts`, `w19.spec.ts`) —
// > same fixtures, same rules, one file per workflow.

import { type Page, expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  answerQuickCheck,
  backToPath,
  createPath,
  expectLessonContent,
  gotoPath,
  markComplete,
  openLessonAt,
  railLessons,
  uniqueTopic,
  waitForLessonGenerated,
} from "../fixtures/journey";
import {
  REVISED_LESSON_TITLE_PREFIX,
  REVISED_PASSAGE_MARKER,
  applyProposal,
  askForRevision,
  changeRows,
  closeChangeHistory,
  closeShapingRail,
  fetchChanges,
  ghostRows,
  openChangeHistory,
  openShapingRail,
  proposalCards,
  revisingRows,
} from "../fixtures/shaping";
// The Phase 1 wire reads live in `tutor.ts` because W12's bit-identical
// comparison is what first needed them. They read a *lesson* and the id of the
// open one — path state, not rail state — so using them here crosses no surface.
import { fetchLesson, openLessonId } from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

/** Where the Revision lands once the learner has finished the first lesson. */
const TARGET_INDEX = 1;

test.describe("W18 shape your path — revise a lesson", { tag: "@w18" }, () => {
  test("Apply regenerates the target in place, and the finished lesson is untouched", async ({
    page,
  }) => {
    const pathId = await createPath(page, uniqueTopic("Cheese caves"));
    const total = await railLessons(page).count();
    expect(total).toBeGreaterThan(TARGET_INDEX);

    // --- work the first lesson, so there is engaged content to protect --------
    await openLessonAt(page, 0);
    const engagedId = await openLessonId(page);
    await expectLessonContent(page);
    await answerQuickCheck(page, 0);
    await markComplete(page);
    await backToPath(page);
    const engagedBefore = await fetchLesson(page, engagedId);

    // The Revision target has to be idle before it can be re-pitched: a lesson
    // mid-generation is refused (`target_generating`, TDD §5.6), and completing
    // the first lesson is exactly what advances the prefetch window onto it.
    // Waiting here is the learner's own pause, not a sleep — it is the rail
    // reaching a state, asserted.
    await waitForLessonGenerated(page, TARGET_INDEX);
    const targetId = await lessonIdAt(page, TARGET_INDEX);

    // --- the offer ------------------------------------------------------------
    await openShapingRail(page);
    const card = await askForRevision(page);

    // One operation, and it is the narrow shape: a Revision names a lesson, not
    // a slot, so it adds nothing — there is no ghost row anywhere.
    const operation = card.getByTestId("shaping-rail-proposal-operation");
    await expect(operation).toHaveCount(1);
    await expect(operation).toHaveAttribute("data-kind", "revise_lesson");
    await expect(ghostRows(page)).toHaveCount(0);

    // The path rail says which row the offer stands on — and it is the next
    // lesson, never the one already finished (the engagement boundary, D2).
    await expect(revisingRows(page)).toHaveCount(1);
    await expect(railLessons(page).nth(TARGET_INDEX)).toHaveAttribute("data-revising", "true");
    await expect(railLessons(page).nth(0)).not.toHaveAttribute("data-revising", "true");
    // Still an offer: no Change exists, and the row is still openable.
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(0);

    // --- the tap --------------------------------------------------------------
    await applyProposal(page, card);

    // The slot is kept. A Revision re-teaches a lesson; it never grows the path.
    await expect(railLessons(page)).toHaveCount(total);
    await expect(revisingRows(page)).toHaveCount(0);
    // Same row, same id — with the title the Proposal named.
    expect(await lessonIdAt(page, TARGET_INDEX)).toBe(targetId);
    await expect(railLessons(page).nth(TARGET_INDEX)).toContainText(REVISED_LESSON_TITLE_PREFIX);

    const { changes } = await fetchChanges(page, pathId);
    expect(changes).toHaveLength(1);
    expect(changes[0].kinds).toEqual(["revise_lesson"]);
    expect(changes[0].status).toBe("applied");
    await openChangeHistory(page);
    await expect(changeRows(page).first().getByTestId("shaping-rail-history-kind")).toContainText(
      "Revised a lesson",
    );
    // The sheet takes the whole rail while it is open, so it is closed before
    // the rail is — the collapse control lives on the thread's header.
    await closeChangeHistory(page);
    await closeShapingRail(page);

    // --- the instruction reached the content ---------------------------------
    // Cleared and rewritten by Phase 1's untouched pipeline, so this waits for a
    // real generation — and what comes back carries the marker the stub plants
    // when it recognizes its own revision instruction in the lesson prompt.
    await openLessonAt(page, TARGET_INDEX);
    await expectLessonContent(page);
    await expect(page.getByTestId("lesson-read-passage")).toContainText(REVISED_PASSAGE_MARKER);
    expect(await openLessonId(page)).toBe(targetId);

    // --- and the finished lesson never moved ---------------------------------
    // Whole-payload equality: title, passage, Quick check, the recorded Attempt
    // with its keyed reveal, completion. Engaged content is out of reach (D2).
    expect(await fetchLesson(page, engagedId)).toEqual(engagedBefore);
  });

  test("a pending Revision marks a row and changes nothing behind it", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Signal flags"));
    const total = await railLessons(page).count();
    // Nothing is engaged here, so the first lesson is the one on offer.
    await waitForLessonGenerated(page, 0);
    const targetId = await lessonIdAt(page, 0);
    const before = await fetchLesson(page, targetId);

    await openShapingRail(page);
    const card = await askForRevision(page);
    await expect(railLessons(page).nth(0)).toHaveAttribute("data-revising", "true");
    await expect(revisingRows(page)).toHaveText(["Will be revised"]);

    // "Not now" costs exactly what it promises: the marker goes, the path does
    // not move, and no Change was ever created.
    await card.getByTestId("shaping-rail-proposal-dismiss").click();
    await expect(card).toHaveAttribute("data-state", "dismissed");
    await expect(revisingRows(page)).toHaveCount(0);
    await expect(railLessons(page)).toHaveCount(total);
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(0);
    expect(await fetchLesson(page, targetId)).toEqual(before);

    // The lesson the offer stood on still opens on the content it always had —
    // the marker was a drawing, and the passage was never regenerated.
    await closeShapingRail(page);
    await openLessonAt(page, 0);
    await expectLessonContent(page);
    await expect(page.getByTestId("lesson-read-passage")).not.toContainText(REVISED_PASSAGE_MARKER);

    // Back on the path, the thread still holds the dismissed offer as history —
    // dismissal is the learner's, not the server's, so the card comes back
    // pending on a fresh read and the path is still untouched.
    await gotoPath(page, pathId);
    await openShapingRail(page);
    await expect(proposalCards(page).last()).toHaveAttribute("data-state", "pending");
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(0);
  });
});

/**
 * The id of the lesson at `index` in the rail, read off its own testid.
 *
 * The rail addresses every row as `lesson-{id}` (`routes/paths.$pathId.tsx`),
 * which is how a spec can name the same lesson before and after a Revision has
 * rewritten its title — the id is the only thing about it that must not change.
 */
async function lessonIdAt(page: Page, index: number): Promise<string> {
  const testid = await railLessons(page).nth(index).getAttribute("data-testid");
  const id = testid?.replace(/^lesson-/, "");
  if (!id) {
    throw new Error(`no lesson id on rail row ${index} (data-testid=${testid})`);
  }
  return id;
}
