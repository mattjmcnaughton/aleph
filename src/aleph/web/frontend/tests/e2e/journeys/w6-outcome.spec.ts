// W6 — Quick-check Outcome, both branches + answer-hiding (PRD §8, TDD §6).
//
// Two properties, one journey:
//
// * **Answer-hiding.** Before an Attempt neither the keyed answer nor the
//   explanation is anywhere the learner can reach: absent from the payload the
//   SPA is served — read off the `GET /lessons/{id}` response itself, since the
//   rendered page can only ever show what the DOM contains — and absent from
//   that DOM. After the Attempt both are revealed, and they stay revealed on
//   return (the reveal is server state, not a React flag). The contract behind
//   it is owned by the integration suite (`tests/integration/test_lessons_api.py`
//   — `attempt` is null and the Quick check carries stem + options only until an
//   Attempt exists); what this journey adds is that the running SPA is served it.
// * **Both Outcome branches.** One correct Attempt and one incorrect Attempt,
//   each still able to proceed and mark complete — the Outcome is formative and
//   never gating.
//
// Proving *hiding* needs something to look for, and the correct answer is
// precisely what the learner may not see. So the journey uses two paths on the
// **same topic**: the stub model is deterministic in (topic, position), so both
// paths' lesson 1 carries the same stem, the same options and the same keyed
// answer. The Attempt on the first path discloses the keyed answer and
// explanation legitimately; the second path is then a lesson whose answer we
// know but whose DOM must not contain it — and knowing the key is also what lets
// the journey aim deliberately at each Outcome branch instead of guessing.
//
// The twin is by option **text**, never by index. Each lesson's options are
// re-ordered at generation, seeded on its own id (`domains/answer_order.py`), so
// the correct answer deliberately does *not* sit in the same slot in both — two
// learners on one topic getting the answer in the same position is the bias that
// module exists to remove. The journey therefore finds the keyed option in the
// second lesson by looking its text up in that lesson's own order, which is also
// the stronger form of the hiding claim: knowing the answer by content, and
// still finding no key in the payload or the DOM.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  answerQuickCheck,
  createPath,
  expectLessonContent,
  markComplete,
  openLessonAt,
  quickCheckOptions,
  railLessons,
  revealedCorrectIndex,
  uniqueTopic,
  waitForLessonGenerated,
  waitForSurface,
} from "../fixtures/journey";
import { quickCheckOptionTexts } from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W6 Quick-check Outcome and answer-hiding", { tag: "@w6" }, () => {
  test("the answer is hidden until attempted, then both branches can proceed", async ({ page }) => {
    // One topic, two paths — identical lesson content in both (see header).
    const topic = uniqueTopic("Fermentation");

    // --- path 1: nothing revealed until the Attempt --------------------------
    await createPath(page, topic);
    await openLessonAt(page, 0);
    const optionCount = await expectLessonContent(page);

    // Pre-Attempt: no Outcome, no explanation, no option marked correct, and
    // nothing to submit until the learner actually picks one.
    await expect(page.getByTestId("outcome-reveal")).toHaveCount(0);
    await expect(page.getByTestId("outcome-explanation")).toHaveCount(0);
    await expect(page.locator("[data-correct]")).toHaveCount(0);
    expect(await revealedCorrectIndex(page)).toBe(-1);
    await expect(page.getByTestId("quick-check-submit")).toBeDisabled();

    const firstOptions = await quickCheckOptionTexts(page);
    const first = await answerQuickCheck(page, 0);
    await expect(quickCheckOptions(page).nth(first.correctIndex)).toHaveAttribute(
      "data-correct",
      "true",
    );
    // What the twin is: the correct option's *text*. Its slot is this lesson's.
    const keyedOption = firstOptions[first.correctIndex];
    // Non-gating: whichever branch this was, the lesson can be completed.
    await markComplete(page);

    // --- path 2: the same lesson, with the answer known to the test ----------
    await createPath(page, topic);

    // The strong form of answer-hiding, taken at the wire: capture the very
    // payload this lesson view is built from and check the reveal fields are not
    // in it. `page.content()` below can only speak for the DOM — a key delivered
    // to the browser and merely not rendered would pass it and fail this.
    await waitForLessonGenerated(page, 0);
    const lessonResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" && /\/api\/v1\/lessons\//.test(response.url()),
      { timeout: ACTION_TIMEOUT },
    );
    await railLessons(page).nth(0).click();
    const payload = await (await lessonResponse).json();
    expect(payload.attempt).toBeNull();
    expect(payload.quick_check).not.toHaveProperty("correct_index");
    expect(payload.quick_check).not.toHaveProperty("explanation");

    await waitForSurface(page, "lesson-read-passage");
    expect(await expectLessonContent(page)).toBe(optionCount);

    // ...and nothing leaked into the page built from it either: the explanation
    // the first path revealed is nowhere in this DOM — not rendered, not tucked
    // into a hidden attribute.
    expect(await page.content()).not.toContain(first.explanation);
    await expect(page.locator("[data-correct]")).toHaveCount(0);
    await expect(page.getByTestId("outcome-reveal")).toHaveCount(0);

    // The same options, in this lesson's own order — so the keyed answer is
    // found by its text, not by the slot it held on the first path.
    const secondOptions = await quickCheckOptionTexts(page);
    expect([...secondOptions].sort()).toEqual([...firstOptions].sort());
    const keyedIndex = secondOptions.indexOf(keyedOption);
    expect(keyedIndex).toBeGreaterThanOrEqual(0);

    // Aim at the branch the first Attempt did not take.
    const wantIncorrect = first.outcome === "correct";
    const answer = wantIncorrect ? (keyedIndex + 1) % optionCount : keyedIndex;
    const second = await answerQuickCheck(page, answer);

    // Same topic + position => the same keyed option and the same explanation:
    // the twin the journey relies on. Its index is this lesson's own.
    expect(secondOptions[second.correctIndex]).toBe(keyedOption);
    expect(second.correctIndex).toBe(keyedIndex);
    expect(second.explanation).toBe(first.explanation);
    // ...and the two Attempts between them covered both Outcome branches.
    expect(second.outcome).not.toBe(first.outcome);
    expect([first.outcome, second.outcome].sort()).toEqual(["correct", "incorrect"]);

    // --- revealed on return --------------------------------------------------
    await page.reload();
    await waitForSurface(page, "outcome-reveal");
    await expect(page.getByTestId("outcome-reveal")).toHaveAttribute(
      "data-outcome",
      second.outcome,
    );
    await expect(page.getByTestId("outcome-explanation")).toHaveText(second.explanation);
    await expect(quickCheckOptions(page).nth(second.correctIndex)).toHaveAttribute(
      "data-correct",
      "true",
    );
    // The Attempt is first-wins: there is nothing left to submit.
    await expect(page.getByTestId("quick-check-submit")).toHaveCount(0);

    // The other branch proceeds just as freely.
    await markComplete(page);
  });
});
