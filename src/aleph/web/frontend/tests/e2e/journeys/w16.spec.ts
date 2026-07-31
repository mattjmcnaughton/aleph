// W16 — A wrong lesson is corrected, not papered over (PRD §8, §5.7b;
// CONTEXT.md *contradiction handling*).
//
// The hard case, and the reason it needs a browser: a lesson whose Read passage
// carries a false claim **and whose Quick check is keyed to that claim**. The
// tutor cannot simply be right — being right and silent would send the learner
// into a check that marks their corrected answer wrong. So the reply has to do
// three things at once: correct the claim, attribute the difference plainly,
// and say what the check expects.
//
// And it has to do all of that while changing nothing. The tutor never writes
// Phase 1 state (TDD §3): the passage is byte-identical after the correction,
// the Quick check still keys to the passage's figure, and the lesson completes.
// A tutor that "fixed" the lesson would be a worse bug than the wrong lesson.
//
// `[force-lesson-error]` is a **topic** sentinel on the lesson branch of the
// stub, not a question sentinel: it seeds the passage, and the streamed branch
// then reacts to what it finds there (TDD §11). So every ask on this lesson gets
// the correction, which is exactly the behaviour under test — the learner does
// not have to know to ask about it.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  createPath,
  expectLessonContent,
  openLessonAt,
  quickCheckOptions,
  uniqueTopic,
} from "../fixtures/journey";
import {
  FORCE_LESSON_ERROR,
  LESSON_ERROR_CORRECTION,
  LESSON_ERROR_FALSE_CLAIM,
  LESSON_ERROR_FALSE_VALUE,
  askTutor,
  fetchLesson,
  openLessonId,
  openRail,
  quickCheckOptionTexts,
  submitAndCompleteWithRailOpen,
  tapAboveRail,
} from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W16 a wrong lesson is corrected", { tag: "@w16" }, () => {
  test("the correction names what the check expects, and changes nothing", async ({ page }) => {
    await createPath(page, `${FORCE_LESSON_ERROR} ${uniqueTopic("Kettles")}`);
    await openLessonAt(page, 0);

    // --- the lesson really is wrong ------------------------------------------
    // Wait for the whole passage to be *finished* rendering before snapshotting
    // it: the mermaid diagram draws asynchronously, and a snapshot taken while
    // it is still the source fallback would differ from the one taken after —
    // for a reason that has nothing to do with the tutor.
    await expectLessonContent(page);
    const passageBefore = (await page.getByTestId("lesson-read-passage").innerText()).trim();
    expect(passageBefore).toContain(LESSON_ERROR_FALSE_CLAIM);
    const optionsBefore = await quickCheckOptionTexts(page);
    expect(optionsBefore).toContain(LESSON_ERROR_FALSE_VALUE);
    // The whole payload too, not just what is on screen: "the tutor changed
    // nothing" has to be about what the server holds, and the rendered passage
    // can only speak for the Markdown that reached the renderer (W12's rule).
    const lessonId = await openLessonId(page);
    const lessonBefore = await fetchLesson(page, lessonId);

    // --- the tutor corrects it, and says what the check expects --------------
    await openRail(page);
    const reply = await askTutor(page, "Is the temperature in this lesson right?");

    // Corrected...
    expect(reply).toContain(LESSON_ERROR_CORRECTION);
    // ...attributed rather than quietly worked around (the difference is named,
    // and the lesson is named as where the wrong figure came from)...
    expect(reply).toContain(LESSON_ERROR_FALSE_CLAIM);
    expect(reply).toContain("this lesson's passage says");
    // ...and the learner is told what the graded check will accept, which is the
    // half a merely-correct tutor would leave out.
    expect(reply).toContain("Quick check");
    expect(reply).toContain(`it expects ${LESSON_ERROR_FALSE_VALUE}`);

    // Not an error and not a refusal — a correction is an ordinary reply.
    await expect(page.getByTestId("tutor-rail-error")).toHaveCount(0);

    // --- the lesson is unchanged ---------------------------------------------
    // Byte-identical passage and the same options, in the same order: the tutor
    // writes no Phase 1 state, so the correction lives in the conversation and
    // nowhere else (TDD §3).
    const passageAfter = (await page.getByTestId("lesson-read-passage").innerText()).trim();
    expect(passageAfter).toBe(passageBefore);
    expect(await quickCheckOptionTexts(page)).toEqual(optionsBefore);
    // ...and at the wire, where a tutor that "helpfully" rewrote the passage or
    // re-keyed the Quick check would show up even if the rendered text happened
    // to read the same.
    expect(await fetchLesson(page, lessonId)).toEqual(lessonBefore);

    // --- and it is still completable -----------------------------------------
    // Answering the way the tutor said the check expects — the passage's figure,
    // not the true one — is what the check marks correct. That tension is the
    // point of W16, and it is left standing rather than resolved by the tutor.
    // Still with the rail open, like W9: the tutor gates nothing, so nothing is
    // dismissed to get to the end of the lesson.
    const keyedIndex = optionsBefore.indexOf(LESSON_ERROR_FALSE_VALUE);
    await tapAboveRail(page, page.locator(`label[for="quick-check-option-${keyedIndex}"]`));
    const outcome = await submitAndCompleteWithRailOpen(page);
    expect(outcome.outcome).toBe("correct");
    expect(outcome.correctIndex).toBe(keyedIndex);
    await expect(quickCheckOptions(page).nth(keyedIndex)).toHaveAttribute("data-correct", "true");
    await expect(page.getByTestId("lesson-completed")).toBeVisible();
  });

  test("a healthy lesson gets no correction", async ({ page }) => {
    // The control. `[force-lesson-error]` seeds one passage; without it the
    // reply has nothing to correct, so an over-eager tutor that "corrects"
    // everything — the failure direction §5.7b names — fails here.
    await createPath(page, uniqueTopic("Steam engines"));
    await openLessonAt(page, 0);
    await openRail(page);

    const reply = await askTutor(page, "Is the temperature in this lesson right?");
    expect(reply).not.toContain(LESSON_ERROR_CORRECTION);
    expect(reply).not.toContain("that is not right");
  });
});
