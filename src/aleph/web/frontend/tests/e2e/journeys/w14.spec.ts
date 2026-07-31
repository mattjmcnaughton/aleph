// W14 — A failed reply is recoverable (PRD §8, §5.7; TDD §5.6/D2).
//
// A reply that falls over mid-stream must be an *error with a retry*, never a
// dead bubble: clear copy that does not blame the learner's connection for a
// server-side failure, the retry right there, and the learner's question still
// in the composer where they left it.
//
// Failure is forced deterministically with the `[force-tutor-failure]` sentinel
// the streamed stub honours (TDD §11): it raises after two deltas, with deltas
// still owed, which is exactly the discard-partial path.
//
// **Why "retry then succeeds" is not asserted here** — the same reason W8 gives
// for the outline. The sentinel lives in the question text and retry re-sends
// that same question, so the stub fails it again, by design and statelessly
// (TDD §11, D10). What the browser proves is that the failure is legible, that
// the question survives it, that retrying is a real round trip, and that
// nothing was persisted. The other half — fail → retry → success on one turn —
// belongs to the integration tier, where the failure can be made transient
// instead of question-borne (`tests/integration/`, AL-220).

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import { ACTION_TIMEOUT, createPath, openLessonAt, uniqueTopic } from "../fixtures/journey";
import {
  FORCE_TUTOR_FAILURE,
  TUTOR_UPSTREAM_FAILURE_COPY,
  askTutor,
  fetchConversation,
  learnerMessages,
  openRail,
  passageSlice,
  railMessages,
  replyText,
  submitAndCompleteWithRailOpen,
  tapAboveRail,
  tutorReplies,
} from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W14 a failed reply is recoverable", { tag: "@w14" }, () => {
  test("the failure is legible, the question is kept, and the lesson is untouched", async ({
    page,
  }) => {
    const pathId = await createPath(page, uniqueTopic("Tectonics"));
    await openLessonAt(page, 0);
    const slice = await passageSlice(page);
    await openRail(page);

    const question = `${FORCE_TUTOR_FAILURE} Why does this happen at all?`;
    await page.getByTestId("tutor-rail-input").fill(question);
    await page.getByTestId("tutor-rail-send").click();

    // --- an error, with a retry, and the question preserved ------------------
    const failure = page.getByTestId("tutor-rail-error");
    await expect(failure).toBeVisible({ timeout: ACTION_TIMEOUT });
    await expect(failure).toHaveAttribute("role", "alert");
    // The server's own learner-facing copy, verbatim: a model turn that fell
    // over is not "check your connection" (PRD §5.7's wording gap).
    await expect(failure).toContainText(TUTOR_UPSTREAM_FAILURE_COPY);
    await expect(failure).toContainText("Your question is still here.");
    await expect(page.getByTestId("tutor-rail-retry")).toBeEnabled();

    // Preserved twice over, and this is the half the learner can see: the exact
    // text they typed, back in the composer, editable.
    await expect(page.getByTestId("tutor-rail-input")).toHaveValue(question);

    // Not a dead bubble and not half a reply: the partial deltas the stub had
    // already streamed are gone, and nothing was appended to the thread.
    await expect(page.getByTestId("tutor-rail-streaming")).toHaveCount(0);
    await expect(railMessages(page)).toHaveCount(0);

    // --- nothing was persisted (a turn exists whole or not at all, D2) -------
    const conversation = await fetchConversation(page, pathId);
    expect(conversation.messages).toEqual([]);

    // --- retrying is a real round trip ---------------------------------------
    // It lands back on the failure (the sentinel is in the question, so the same
    // send fails the same way) — with the retry still offered and the question
    // still there, which is what "recoverable" has to mean for a learner who
    // taps it twice.
    await page.getByTestId("tutor-rail-retry").click();
    await expect(page.getByTestId("tutor-rail-error")).toBeVisible({ timeout: ACTION_TIMEOUT });
    await expect(page.getByTestId("tutor-rail-retry")).toBeEnabled();
    await expect(page.getByTestId("tutor-rail-input")).toHaveValue(question);
    await expect(railMessages(page)).toHaveCount(0);

    // --- the way out: a question without the sentinel ------------------------
    const reply = await askTutor(page, "Never mind — what is the passage's main point?");
    expect(reply).toContain(slice);
    await expect(page.getByTestId("tutor-rail-error")).toHaveCount(0);
    await expect(learnerMessages(page)).toHaveCount(1);
    await expect(tutorReplies(page)).toHaveCount(1);

    // --- and the lesson never knew ------------------------------------------
    // No tutor state gates completion (PRD release criteria): the Quick check
    // and mark complete work exactly as they would have.
    // With the rail still open, like W9 — nothing is dismissed to reach the end
    // of the lesson.
    await tapAboveRail(page, page.locator('label[for="quick-check-option-0"]'));
    await submitAndCompleteWithRailOpen(page);
    await expect(page.getByTestId("lesson-completed")).toBeVisible();
  });

  test("a failed turn leaves an existing thread exactly as it was", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Cave systems"));
    await openLessonAt(page, 0);
    await openRail(page);

    // A settled turn first, so the failure has something it could damage.
    const reply = await askTutor(page, "What is this lesson for?");
    const before = await fetchConversation(page, pathId);
    expect(before.messages).toHaveLength(2);

    await page.getByTestId("tutor-rail-input").fill(`${FORCE_TUTOR_FAILURE} and then what?`);
    await page.getByTestId("tutor-rail-send").click();
    await expect(page.getByTestId("tutor-rail-error")).toBeVisible({ timeout: ACTION_TIMEOUT });

    // The earlier turn is still rendered, and still the only thing in the
    // thread — a failure invalidates nothing because there is no server state
    // the client could be out of step with (`useTutorRail`, invariant 2).
    await expect(railMessages(page)).toHaveCount(2);
    expect(await replyText(page, 0)).toBe(reply);
    expect(await fetchConversation(page, pathId)).toEqual(before);

    // ...and it is still there after a reload, which is the server's own answer.
    await page.reload();
    await expect(page.getByTestId("lesson-read-passage")).toBeVisible();
    await openRail(page);
    await expect(railMessages(page)).toHaveCount(2);
  });
});
