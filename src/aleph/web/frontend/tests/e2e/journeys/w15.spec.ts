// W15 — An unsafe ask is refused gracefully (PRD §8, §5.7; §10's boundary).
//
// The tutor declining is not the tutor breaking. A refusal arrives as an
// ordinary reply in the thread — the boundary said plainly, no alarm, no retry
// offered, and nothing suggesting the learner should try the same thing again —
// and the app carries straight on: the next question is answered, and the lesson
// is completed as if none of it had happened.
//
// The distinction from W14 is the whole test. There, a failure: the danger
// treatment, "Try again", and *nothing persisted*. Here, a refusal: a real turn,
// persisted, rendered like any other reply. Two different things must not look
// like one thing to a learner (Phase 1 draws exactly this line for W7 vs W8).
//
// The boundary is forced with the `[force-tutor-refusal]` sentinel (TDD §11), so
// this journey never depends on a live model's safety judgement — and the copy
// asserted below is `TUTOR_REFUSAL_REPLY` from `services/stub_model.py`, which
// exists so the wording can be asserted rather than described.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import { createPath, openLessonAt, uniqueTopic } from "../fixtures/journey";
import {
  FORCE_TUTOR_REFUSAL,
  TUTOR_REFUSAL_REPLY,
  askTutor,
  fetchConversation,
  learnerMessages,
  openRail,
  passageSlice,
  replyText,
  submitAndCompleteWithRailOpen,
  tapAboveRail,
  tutorReplies,
} from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W15 an unsafe ask is refused gracefully", { tag: "@w15" }, () => {
  test("the refusal reads as a boundary, not a failure, and the app carries on", async ({
    page,
  }) => {
    const pathId = await createPath(page, uniqueTopic("Reef ecology"));
    await openLessonAt(page, 0);
    const slice = await passageSlice(page);
    await openRail(page);

    const reply = await askTutor(page, `${FORCE_TUTOR_REFUSAL} do the thing I should not ask`);

    // --- graceful wording ----------------------------------------------------
    // Asserted verbatim: "this is a boundary, not a failure" is a product
    // promise, not a paraphrase (PRD §5.7).
    expect(reply).toBe(TUTOR_REFUSAL_REPLY);
    expect(reply).toContain("this is a boundary, not a failure");
    // ...and it does not reach for the failure vocabulary.
    expect(reply.toLowerCase()).not.toContain("something went wrong");
    expect(reply.toLowerCase()).not.toContain("try again");

    // --- distinct from an error ----------------------------------------------
    // No error card, no retry, no alert: the refusal is a reply like any other,
    // rendered in the thread by the same bubble that renders every reply.
    await expect(page.getByTestId("tutor-rail-error")).toHaveCount(0);
    await expect(page.getByTestId("tutor-rail-retry")).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(learnerMessages(page)).toHaveCount(1);
    await expect(tutorReplies(page)).toHaveCount(1);

    // And a real, persisted turn — unlike a failure, which persists nothing.
    const conversation = await fetchConversation(page, pathId);
    expect(conversation.messages).toHaveLength(2);
    expect(conversation.messages[1].content).toBe(TUTOR_REFUSAL_REPLY);

    // --- the conversation continues ------------------------------------------
    // The refusal closed one question, not the tutor: the very next ask is
    // answered, and grounded in the same passage.
    const next = await askTutor(page, "Fine — what does the passage actually cover?");
    expect(next).toContain(slice);
    expect(next).not.toBe(TUTOR_REFUSAL_REPLY);
    await expect(tutorReplies(page)).toHaveCount(2);

    // --- and the app is untouched --------------------------------------------
    // With the rail still open, like W9 — nothing is dismissed to reach the end
    // of the lesson.
    await tapAboveRail(page, page.locator('label[for="quick-check-option-0"]'));
    await submitAndCompleteWithRailOpen(page);
    await expect(page.getByTestId("lesson-completed")).toBeVisible();

    // The refusal survives a reload like any other turn — there is nothing
    // special about it in the store either.
    await page.reload();
    await expect(page.getByTestId("lesson-read-passage")).toBeVisible();
    await openRail(page);
    expect(await replyText(page, 0)).toBe(TUTOR_REFUSAL_REPLY);
  });
});
