// W9 — Ask about the lesson you're reading, the magic moment (PRD §8, TDD §11).
//
// Open a lesson, open the tutor, send "Explain this simpler", and get a reply
// that is about *this* passage — then answer the Quick check and mark the lesson
// complete, with the thread still there at the end of it.
//
// **Grounding is asserted structurally, never by wording.** The stub echoes a
// recognizable slice of the lesson's own Read passage (`stub_passage_slice` —
// the passage's lead heading), and this spec reads that slice off the rendered
// lesson rather than rebuilding it in TypeScript. So the assertion is "the reply
// names the words this lesson is showing the learner", which is a property of
// the whole chain — context assembly → prompt → stream → the rail's renderer —
// and not a string two files happen to agree on. Reply *quality* is §10's job
// (the evals), never a browser's.
//
// The second half is the one the PRD calls the pass condition: the tutor is
// **additive**. It gates nothing and writes no Phase 1 state, so the Quick check
// still takes an answer and the lesson still completes — and the thread is still
// there afterwards. All of it happens with the rail **open**: on a 390x844 phone
// that is a sheet over the bottom 75% of the viewport, and `main` carries a
// matching 75vh of bottom clearance for as long as it is
// (`components/workspace.tsx`), so every control can be scrolled above the
// sheet's top edge. `tapAboveRail` is what aims each tap inside the resulting
// band and hit-tests it there. Nothing is collapsed to reach the lesson.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  createPath,
  expectLessonContent,
  lessonTitle,
  openLessonAt,
  quickCheckOptions,
  uniqueTopic,
} from "../fixtures/journey";
import {
  askTutor,
  lastReply,
  learnerMessages,
  openRail,
  passageSlice,
  replyBody,
  submitAndCompleteWithRailOpen,
  tapAboveRail,
  tapSuggestion,
  tutorReplies,
} from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W9 ask about this lesson", { tag: "@w9" }, () => {
  test("a grounded reply arrives and the lesson stays completable", async ({ page }) => {
    await createPath(page, uniqueTopic("Tide pools"));
    await openLessonAt(page, 0);
    await expectLessonContent(page);
    const slice = await passageSlice(page);

    // --- the way in ----------------------------------------------------------
    // The mark, not a tab bar and not an inline card (PRD §5.1). Before it is
    // tapped there is no rail at all — the surface is entered, not merely shown.
    await expect(page.getByTestId("tutor-rail")).toHaveCount(0);
    await openRail(page);

    // The empty state names what the tutor can see, and the composer's chip
    // names the lesson: the scope statement is the point of both (PRD §5.2).
    await expect(page.getByTestId("tutor-rail-empty")).toBeVisible();
    await expect(page.getByTestId("tutor-rail-context-chip")).toContainText(
      await lessonTitle(page),
    );

    // --- the magic moment ----------------------------------------------------
    const reply = await tapSuggestion(page, "Explain this simpler");

    // The turn is a pair: the learner's message and the tutor's, both in the
    // thread, and the empty state gone.
    await expect(learnerMessages(page)).toHaveCount(1);
    await expect(learnerMessages(page)).toHaveText(["Explain this simpler"]);
    await expect(tutorReplies(page)).toHaveCount(1);
    await expect(page.getByTestId("tutor-rail-empty")).toHaveCount(0);

    // Grounded: the reply names this passage's own words (see the header).
    expect(reply).toContain(slice);
    // A reply, not an error and not a refusal treatment.
    await expect(page.getByTestId("tutor-rail-error")).toHaveCount(0);
    await expect(page.getByTestId("tutor-rail-retry")).toHaveCount(0);

    // Rendered as Markdown through the one renderer (`markdown.tsx`), like every
    // other piece of generated prose in the app — the reply is a list and prose,
    // never literal syntax on screen.
    await expect(replyBody(page, 0).locator("li")).not.toHaveCount(0);
    expect(reply).not.toContain("**");

    // --- the tutor is additive ----------------------------------------------
    // The lesson is live *behind* the open sheet, and stays that way to the end:
    // answering the Quick check, submitting it and marking the lesson complete
    // all happen with the rail on screen. No tutor state gates any of them, and
    // the Attempt the tutor sat beside is the learner's own.
    await expect(page.getByTestId("tutor-rail")).toBeVisible();
    const optionCount = await quickCheckOptions(page).count();
    expect(optionCount).toBeGreaterThanOrEqual(3);
    await tapAboveRail(page, page.locator('label[for="quick-check-option-0"]'));
    await expect(quickCheckOptions(page).nth(0)).toBeChecked();
    await expect(page.getByTestId("tutor-rail")).toBeVisible();

    const outcome = await submitAndCompleteWithRailOpen(page);
    expect(outcome.correctIndex).toBeGreaterThanOrEqual(0);
    await expect(page.getByTestId("lesson-completed")).toBeVisible();

    // ...and the thread survived all of it, without ever having been dismissed:
    // the conversation and the lesson are separate stores (TDD §3), and
    // completing a lesson invalidates the `["paths", …]` prefix, which a
    // conversation is deliberately not under.
    await expect(page.getByTestId("tutor-rail")).toBeVisible();
    await expect(tutorReplies(page)).toHaveCount(1);
    expect(await lastReply(page)).toBe(reply);
    // The tutor is still there on a completed lesson, and still answering.
    const afterCompletion = await askTutor(page, "Now that I am done — what should stick?");
    expect(afterCompletion).toContain(slice);
  });

  test("a typed follow-up continues the same thread on the same lesson", async ({ page }) => {
    await createPath(page, uniqueTopic("Kite aerodynamics"));
    await openLessonAt(page, 0);
    const slice = await passageSlice(page);
    await openRail(page);

    const first = await askTutor(page, "What is this lesson actually claiming?");
    const second = await askTutor(page, "Can you give me the shortest version of that?");

    // Two turns, four messages, in order — and both replies grounded in the same
    // passage, which is what "the tutor can see this lesson" means over a thread
    // rather than for one lucky question.
    await expect(learnerMessages(page)).toHaveCount(2);
    await expect(tutorReplies(page)).toHaveCount(2);
    expect(first).toContain(slice);
    expect(second).toContain(slice);
    // Deterministic per question, so two different asks are two different
    // replies — the second is an answer, not the first one repeated.
    expect(second).not.toBe(first);
  });
});
