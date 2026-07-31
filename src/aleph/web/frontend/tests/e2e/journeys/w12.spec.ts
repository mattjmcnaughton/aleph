// W12 — A Tutor check does not touch progress (PRD §8, §5.5; TDD §3/§6).
//
// A **Tutor check** is not a **Quick check** (CONTEXT.md keeps the two words
// apart, and so does this file). It is the tutor's own question, posed inside
// the conversation: one tap answers it, the feedback is immediate and local, and
// it scores nothing. So the test is a *negative* one, and it is asserted the
// strongest way a browser can — by capturing the Phase 1 payloads before and
// after and comparing them:
//
// * the lesson payload **whole** (`GET /lessons/{id}`) — completion, the Quick
//   check, and the recorded Attempt with its keyed reveal, byte for byte;
// * the path payload **whole but for two named fields** — `progress
//   .generated_lessons`, and each lesson's `generation_state`. Those two move on
//   their own: completing a lesson advances the prefetch window (§5.4), so
//   background generation, not the tutor, is what changes them. Everything else
//   is compared, *including fields added after this file was written* — which is
//   the difference between deleting the two things known to be volatile and
//   projecting out the handful somebody remembered to list.
//
// The check is forced with `[force-tutor-check]` (TDD §11) — free-text "quiz me"
// phrasing should not have to trip a real model's judgement in CI — and the
// sentinel is stateless, so both sends in the second test pose their own check.

import { type Locator, type Page, expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  answerQuickCheck,
  createPath,
  expectLessonContent,
  markComplete,
  openLessonAt,
  quickCheckOptions,
  uniqueTopic,
} from "../fixtures/journey";
import {
  FORCE_TUTOR_CHECK,
  type PathPayload,
  askTutor,
  fetchLesson,
  fetchPath,
  openLessonId,
  openRail,
  tapCheckFollowUp,
  tutorReplies,
} from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

/** Every check card in the thread, in order. */
function checkCards(page: Page): Locator {
  return page.getByTestId("tutor-rail-check");
}

/**
 * The path payload minus the two fields background generation moves on its own
 * (see the header). A **denylist**, not a projection: what is not named here is
 * still compared, so a field this file has never heard of is covered too.
 */
function withoutGenerationAxis(payload: PathPayload): PathPayload {
  const { generated_lessons, ...progress } = payload.progress;
  return {
    ...payload,
    progress,
    units: payload.units.map((unit) => ({
      ...unit,
      lessons: unit.lessons.map(({ generation_state, ...lesson }) => lesson),
    })),
  };
}

test.describe("W12 a Tutor check does not touch progress", { tag: "@w12" }, () => {
  test("Phase 1 state is bit-identical after a Tutor check is posed and answered", async ({
    page,
  }) => {
    const pathId = await createPath(page, uniqueTopic("Beekeeping"));
    await openLessonAt(page, 0);
    await expectLessonContent(page);
    const lessonId = await openLessonId(page);

    // A lesson with real Phase 1 state on it: an Attempt and a completion. A
    // comparison against an empty lesson would prove very little.
    const reveal = await answerQuickCheck(page, 0);
    await markComplete(page);

    const lessonBefore = await fetchLesson(page, lessonId);
    const pathBefore = withoutGenerationAxis(await fetchPath(page, pathId));

    // --- the tutor poses a check, and the learner answers it -----------------
    await openRail(page);
    await askTutor(page, `${FORCE_TUTOR_CHECK} quiz me on this`);

    const card = checkCards(page).first();
    await expect(card).toBeVisible();
    // Named as itself, and stated to be outside the lesson before it is
    // answered — a learner who thinks it is graded reads the question wrongly.
    await expect(card.getByTestId("tutor-rail-check-note")).toHaveText(
      "This doesn't count toward the lesson.",
    );
    await expect(card.getByTestId("tutor-rail-check-stem")).not.toBeEmpty();

    // Unanswered: nothing revealed, and no key in the DOM to be found.
    await expect(card).not.toHaveAttribute("data-answered", "true");
    await expect(card.locator("[data-correct]")).toHaveCount(0);
    await expect(card.getByTestId("tutor-rail-check-outcome")).toHaveCount(0);

    // One tap answers — there is no "Check answer" button, because the feedback
    // is local and a confirm step would only add a wait to something instant.
    const cardOptions = card.getByTestId("tutor-rail-check-option");
    await expect(cardOptions).toHaveCount(4);
    await cardOptions.nth(0).click();

    await expect(card).toHaveAttribute("data-answered", "true");
    await expect(cardOptions.nth(0)).toHaveAttribute("data-selected", "true");
    await expect(card.getByTestId("tutor-rail-check-outcome")).toBeVisible();
    await expect(card.getByTestId("tutor-rail-check-explanation")).not.toBeEmpty();
    // Exactly one option is keyed correct, and it is now legible.
    await expect(card.locator('[data-correct="true"]')).toHaveCount(1);
    // The reveal is terminal in the UI: the card offers no re-answer.
    await expect(cardOptions.nth(1)).toHaveAttribute("aria-disabled", "true");

    // --- nothing in Phase 1 moved -------------------------------------------
    expect(await fetchLesson(page, lessonId)).toEqual(lessonBefore);
    expect(withoutGenerationAxis(await fetchPath(page, pathId))).toEqual(pathBefore);

    // ...and the same is true on screen: the lesson's own reveal is the Attempt
    // the learner recorded, unchanged, and the lesson is still complete.
    await expect(page.getByTestId("outcome-reveal")).toHaveAttribute(
      "data-outcome",
      reveal.outcome,
    );
    await expect(page.getByTestId("outcome-explanation")).toHaveText(reveal.explanation);
    await expect(quickCheckOptions(page).nth(reveal.correctIndex)).toHaveAttribute(
      "data-correct",
      "true",
    );
    await expect(page.getByTestId("lesson-completed")).toBeVisible();

    // The answer survives a reload, which is the only reason the check-answer
    // record exists at all — and it still moves nothing.
    await page.reload();
    await expect(page.getByTestId("lesson-read-passage")).toBeVisible();
    await openRail(page);
    await expect(checkCards(page).first()).toHaveAttribute("data-answered", "true");
    expect(await fetchLesson(page, lessonId)).toEqual(lessonBefore);
    expect(withoutGenerationAxis(await fetchPath(page, pathId))).toEqual(pathBefore);
  });

  test("two checks in one thread reveal independently, and close while a reply streams", async ({
    page,
  }) => {
    await createPath(page, uniqueTopic("Loom weaving"));
    await openLessonAt(page, 0);
    await openRail(page);

    // Two checks, from two different questions — the stub seeds a check from
    // the question text, so these are genuinely different cards.
    await askTutor(page, `${FORCE_TUTOR_CHECK} quiz me on the opening`);
    await askTutor(page, `${FORCE_TUTOR_CHECK} now quiz me on the ending`);
    await expect(checkCards(page)).toHaveCount(2);

    const [first, second] = [checkCards(page).nth(0), checkCards(page).nth(1)];
    // Two cards, each a whole check of its own — not one card re-posed.
    await expect(first.getByTestId("tutor-rail-check-option")).toHaveCount(4);
    await expect(second.getByTestId("tutor-rail-check-option")).toHaveCount(4);

    // --- each card is its own reveal -----------------------------------------
    await first.getByTestId("tutor-rail-check-option").nth(0).click();
    await expect(first).toHaveAttribute("data-answered", "true");
    // The second is untouched: revealing one card must not reveal the other.
    await expect(second).not.toHaveAttribute("data-answered", "true");
    await expect(second.locator("[data-correct]")).toHaveCount(0);
    await expect(second.getByTestId("tutor-rail-check-outcome")).toHaveCount(0);

    await second.getByTestId("tutor-rail-check-option").nth(2).click();
    await expect(second).toHaveAttribute("data-answered", "true");
    // Both revealed, each remembering its own learner's pick.
    await expect(first.locator('[data-selected="true"]')).toHaveCount(1);
    await expect(first.getByTestId("tutor-rail-check-option").nth(0)).toHaveAttribute(
      "data-selected",
      "true",
    );
    await expect(second.getByTestId("tutor-rail-check-option").nth(2)).toHaveAttribute(
      "data-selected",
      "true",
    );

    // --- the rail closes itself while a reply is in flight -------------------
    // The send is held open on purpose: the in-flight window is otherwise over
    // in milliseconds against the stub, and "did the composer lock?" is not a
    // question a test should race. Only the *request* is held — the stream it
    // then returns is the real one.
    //
    // Held on a promise this test resolves, never on a timer: a fixed delay is a
    // bet that the assertions below finish inside it, which is exactly the race
    // it was meant to remove. The request cannot proceed until `release()` is
    // called, so the window is open for precisely as long as it is needed.
    const sendRoute = /\/api\/v1\/paths\/[^/]+\/conversation\/messages$/;
    let release = (): void => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(sendRoute, async (route) => {
      await held;
      await route.continue();
    });

    const before = await tutorReplies(page).count();
    await first
      .getByTestId("tutor-rail-check-follow-up")
      .filter({ hasText: "Another one" })
      .click();

    // In flight: stop is the only control, the composer is closed, the
    // suggestions are gone, and **every** check's follow-ups are disabled —
    // tapping one could only queue a send the server would answer with a 409.
    await expect(page.getByTestId("tutor-rail-stop")).toBeVisible({ timeout: ACTION_TIMEOUT });
    await expect(page.getByTestId("tutor-rail-send")).toHaveCount(0);
    await expect(page.getByTestId("tutor-rail-input")).toBeDisabled();
    await expect(page.getByTestId("tutor-rail-suggestion")).toHaveCount(0);
    const followUps = page.getByTestId("tutor-rail-check-follow-up");
    await expect(followUps).not.toHaveCount(0);
    for (let index = 0; index < (await followUps.count()); index += 1) {
      await expect(followUps.nth(index)).toBeDisabled();
    }

    // ...and everything reopens once the turn settles — which only begins now
    // that every assertion above has been made.
    release();
    await expect(tutorReplies(page)).toHaveCount(before + 1, { timeout: ACTION_TIMEOUT * 2 });
    await expect(page.getByTestId("tutor-rail-send")).toBeVisible();
    await expect(page.getByTestId("tutor-rail-input")).toBeEnabled();
    await expect(page.getByTestId("tutor-rail-suggestion")).not.toHaveCount(0);
    await page.unroute(sendRoute);

    // The follow-up is an ordinary send down the ordinary path, so it is usable
    // again the moment the rail is at rest.
    await tapCheckFollowUp(page, "Why is that right?", second);
    // Both cards are still exactly as the learner left them.
    await expect(first).toHaveAttribute("data-answered", "true");
    await expect(second).toHaveAttribute("data-answered", "true");
  });
});
