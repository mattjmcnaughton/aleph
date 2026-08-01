// W21 — Shaping is never on the critical path (PRD §8; TDD D14, §11).
//
// The phase's freeze, asserted in a browser. Everything Phase 1 and Phase 2A
// ship keeps working while shaping is live around it: a **Proposal** stands
// unapplied, a **Revision** regenerates a lesson underneath, and the learner
// reads, attempts and completes exactly as before, with the in-lesson tutor
// rail behaving exactly as 2A shipped it.
//
// **This spec adds; it never edits.** The 2A journeys (`w9`, `w11`–`w16`) are
// the definition of "unchanged", and they are not touched — a guardrail that
// rewrote the thing it guards would prove nothing. What is new here is only the
// *context*: the same moves, made while shaping is in flight.
//
// Two claims, one per test:
//
//  1. **Nothing shaping does gates the lesson loop.** Read → Attempt → complete
//     works on a lesson a Revision has just rewritten, with a Proposal pending
//     and its ghost rows standing, and the tutor's reply is grounded in the
//     passage exactly as W9 requires.
//  2. **The threads never bleed.** Two conversations, one per surface, each
//     invisible to the other — including under the destructive operation, where
//     clearing the shaping thread leaves the lesson thread whole.
//
// The surfaces are asserted apart as well as the threads: the lesson route
// carries no shaping affordance and the path route carries no in-lesson one.
// One rail grammar, three surfaces, three names (CONTEXT.md).
//
// **What this file deliberately does not assert: the flag *off*.** The harness
// boots one backend with `shaping:on` globally (`scripts/e2e_backend.py`, which
// rehearses AL-370's post-launch configuration on purpose), so there is no
// flag-off browser to drive. The dark-ship gate is covered where it can be:
// 404s on every shaping endpoint in `tests/integration/test_shaping_api.py`, and
// the absent rail entry in `shaping-rail.test.tsx` / `shaping-apply.test.tsx`
// (`shapingOffSession`).

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  answerQuickCheck,
  backToPath,
  createPath,
  expectLessonContent,
  expectProgressInPlace,
  gotoPath,
  markComplete,
  openLessonAt,
  railLessons,
  uniqueTopic,
  waitForLessonGenerated,
} from "../fixtures/journey";
import {
  ADDITION_LESSON_COUNT,
  REVISED_LESSON_TITLE_PREFIX,
  applyProposal,
  askForAddition,
  askForRevision,
  askShaper,
  closeShapingRail,
  fetchChanges,
  fetchShapingConversation,
  ghostRows,
  openShapingRail,
  proposalCards,
  shapingReplies,
} from "../fixtures/shaping";
import {
  askTutor,
  fetchConversation,
  fetchLesson,
  openLessonId,
  openRail,
  passageSlice,
  railMessages,
  submitAndCompleteWithRailOpen,
  tapAboveRail,
} from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

/** The lesson the Revision lands on once the first one is finished. */
const REVISED_INDEX = 1;

/** The tutor's question in the lesson thread — recognizable in either thread. */
const LESSON_QUESTION = "What is this passage really saying?";

test.describe("W21 shaping is never on the critical path", { tag: "@w21" }, () => {
  test("the lesson loop and the 2A rail are untouched under active shaping", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Salt marshes"));
    const total = await railLessons(page).count();
    expect(total).toBeGreaterThan(REVISED_INDEX);

    // --- Phase 1 state worth protecting --------------------------------------
    await openLessonAt(page, 0);
    const engagedId = await openLessonId(page);
    await expectLessonContent(page);
    await answerQuickCheck(page, 0);
    await markComplete(page);
    await backToPath(page);
    const engagedBefore = await fetchLesson(page, engagedId);
    // Idle before it can be re-pitched (see `w18.spec.ts`).
    await waitForLessonGenerated(page, REVISED_INDEX);

    // --- shaping, in flight ---------------------------------------------------
    await openShapingRail(page);
    // A Revision, applied: the next lesson is being rewritten from here on.
    const revision = await askForRevision(page);
    await applyProposal(page, revision);
    await expect(railLessons(page).nth(REVISED_INDEX)).toContainText(REVISED_LESSON_TITLE_PREFIX);
    // ...and an Addition left standing: a Proposal on screen, ghosts in the rail,
    // and no consent given to either.
    const pending = await askForAddition(page);
    await expect(ghostRows(page)).toHaveCount(ADDITION_LESSON_COUNT);
    await closeShapingRail(page);

    // --- 2A, exactly as it shipped -------------------------------------------
    // The revised lesson, once Phase 1's untouched pipeline has written it. That
    // this resolves at all is half the claim: a Revision is an ordinary
    // `ungenerated` lesson from the generator's point of view.
    await openLessonAt(page, REVISED_INDEX);
    await expectLessonContent(page);
    const slice = await passageSlice(page);

    await openRail(page);
    const reply = await askTutor(page, LESSON_QUESTION);
    // Grounded in the passage in front of the learner — W9's assertion, made
    // while a Proposal is pending on the very same path.
    expect(reply).toContain(slice);

    // The in-lesson rail is the 2A surface and nothing else: no card, no ghost,
    // no way into shaping from a lesson.
    await expect(page.getByTestId("shaping-rail")).toHaveCount(0);
    await expect(page.getByTestId("shaping-rail-mark")).toHaveCount(0);
    await expect(page.getByTestId("shaping-rail-proposal")).toHaveCount(0);
    await expect(page.getByTestId("path-rail-ghost")).toHaveCount(0);

    // Read → Attempt → complete, with the rail open the whole way (W9's posture:
    // nothing is dismissed to reach the end of the lesson).
    await tapAboveRail(page, page.locator('label[for="quick-check-option-0"]'));
    await submitAndCompleteWithRailOpen(page);
    await backToPath(page);

    // --- and shaping wrote nothing while the learner worked ------------------
    await expectProgressInPlace(page.getByTestId("path-progress"), 2, total);
    // One Change on this path: the Revision the learner applied. The pending
    // Proposal is still an offer — it never became a write, whatever the card
    // now says about whether it still fits.
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(1);
    await expect(railLessons(page)).toHaveCount(total);
    await openShapingRail(page);
    await expect(proposalCards(page)).toHaveCount(2);
    await expect(pending).not.toHaveAttribute("data-state", "applied");
    // The finished lesson is bit-identical through all of it.
    expect(await fetchLesson(page, engagedId)).toEqual(engagedBefore);
  });

  test("the two threads never bleed, and clearing one leaves the other whole", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Kelp forests"));

    // --- the lesson thread ----------------------------------------------------
    await openLessonAt(page, 0);
    await expectLessonContent(page);
    await openRail(page);
    await askTutor(page, LESSON_QUESTION);
    await expect(railMessages(page)).toHaveCount(2);
    // The lesson route offers no way into shaping — different surface, different
    // thread, and the entry point belongs to the path view.
    await expect(page.getByTestId("shaping-rail-mark")).toHaveCount(0);

    // --- the shaping thread ---------------------------------------------------
    await gotoPath(page, pathId);
    // ...and the path route offers no in-lesson rail, for the same reason.
    await expect(page.getByTestId("tutor-rail-mark")).toHaveCount(0);
    await expect(page.getByTestId("tutor-rail")).toHaveCount(0);
    await openShapingRail(page);
    await askShaper(page, "What could you add to this path for me?");
    // The shaping rail shows its own thread and only its own: the lesson
    // question is not in it, on screen or on the wire.
    await expect(shapingReplies(page)).toHaveCount(1);
    await expect(page.getByTestId("shaping-rail-messages")).not.toContainText(LESSON_QUESTION);

    const lessonThread = await fetchConversation(page, pathId);
    const shapingThread = await fetchShapingConversation(page, pathId);
    expect(lessonThread.messages).toHaveLength(2);
    expect(shapingThread.messages).toHaveLength(2);
    expect(lessonThread.messages.some((message) => message.content.includes(LESSON_QUESTION))).toBe(
      true,
    );
    expect(
      shapingThread.messages.some((message) => message.content.includes(LESSON_QUESTION)),
    ).toBe(false);
    expect(
      lessonThread.messages.some((message) => message.content.includes("add to this path")),
    ).toBe(false);

    // --- the destructive one --------------------------------------------------
    // "New conversation" on the shaping rail clears the shaping thread. It is
    // per-kind: the in-lesson thread is another conversation entirely and must
    // not notice (TDD D3).
    await page.getByTestId("shaping-rail-new-conversation").click();
    await page.getByTestId("shaping-rail-new-conversation-confirm").click();
    await expect(page.getByTestId("shaping-rail-empty")).toBeVisible();
    expect((await fetchShapingConversation(page, pathId)).messages).toEqual([]);

    // The lesson thread is exactly where the learner left it — on the wire, and
    // on the surface that owns it.
    expect(await fetchConversation(page, pathId)).toEqual(lessonThread);
    // The shaping rail is a sheet over the path on a phone, so it is collapsed
    // before a rail row is tapped — the same move a learner makes.
    await closeShapingRail(page);
    await openLessonAt(page, 0);
    await openRail(page);
    await expect(railMessages(page)).toHaveCount(2);
  });
});
