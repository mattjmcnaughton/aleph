// W11 — The conversation persists (PRD §8, §5.8).
//
// Two halves, and they fail for different reasons, so they are two tests:
//
// 1. **Across lessons.** There is one conversation per *path*, not per lesson
//    (TDD §3), so completing a lesson and moving to the next one must find the
//    same thread — with the rail's scope chip now naming the new lesson, because
//    what the tutor can *see* moves even though the thread does not.
// 2. **Across sessions.** The thread lives on the server, not in this browser.
//    The session is torn down the way W2 tears it down — app cookie *and*
//    Keycloak's SSO cookie — so signing back in is a real credential round trip
//    rather than a silent re-auth, and the thread that comes back came from the
//    database.
//
// Both compare the thread as *text, in order*: a thread's identity is its
// messages, and the stub's wording is deterministic but not this spec's business
// to know (TDD §12's structure-not-text rule).

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE, signIn } from "../fixtures/auth";
import {
  answerQuickCheck,
  backToPath,
  createPath,
  lessonTitle,
  markComplete,
  openLessonAt,
  uniqueTopic,
} from "../fixtures/journey";
import {
  askTutor,
  closeRail,
  learnerMessages,
  openRail,
  passageSlice,
  railMessages,
  threadText,
  tutorReplies,
} from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W11 the conversation persists", { tag: "@w11" }, () => {
  test("the thread survives completing a lesson and moving to the next", async ({ page }) => {
    await createPath(page, uniqueTopic("Bridge cables"));
    await openLessonAt(page, 0);
    const firstTitle = await lessonTitle(page);
    await openRail(page);

    await askTutor(page, "Why does this lesson start where it does?");
    const thread = await threadText(page);
    expect(thread).toHaveLength(2);

    // Collapsing and reopening is not a reload, but it does unmount the rail —
    // the thread it comes back with is cached server state, not component state.
    await closeRail(page);
    await openRail(page);
    expect(await threadText(page)).toEqual(thread);

    // --- complete this lesson and move to the next ---------------------------
    await closeRail(page);
    await answerQuickCheck(page, 0);
    await markComplete(page);
    await backToPath(page);
    await openLessonAt(page, 1);

    const secondTitle = await lessonTitle(page);
    expect(secondTitle).not.toBe(firstTitle);

    // One conversation per path: the same thread, on a different lesson.
    await openRail(page);
    expect(await threadText(page)).toEqual(thread);
    // ...and the scope moved with the learner, even though the thread did not.
    await expect(page.getByTestId("tutor-rail-context-chip")).toContainText(secondTitle);

    // A new turn continues that same thread and is grounded in the *new*
    // lesson's passage — the conversation carries over, the context does not.
    const slice = await passageSlice(page);
    const reply = await askTutor(page, "And what is this one about?");
    expect(reply).toContain(slice);
    await expect(learnerMessages(page)).toHaveCount(2);
    await expect(tutorReplies(page)).toHaveCount(2);
  });

  test("the thread survives signing out, reloading and signing back in", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Salt marshes"));
    await openLessonAt(page, 0);
    const lessonUrl = page.url();
    await openRail(page);
    await askTutor(page, "What should I take away from this passage?");
    const thread = await threadText(page);
    expect(thread).toHaveLength(2);

    // --- leave ---------------------------------------------------------------
    await closeRail(page);
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    // Every cookie, Keycloak's SSO session included (W2's teardown).
    await page.context().clearCookies();
    await page.reload();

    // --- come back -----------------------------------------------------------
    await signIn(page);
    await page.goto(lessonUrl);
    await expect(page.getByTestId("lesson-read-passage")).toBeVisible();
    await openRail(page);

    // Exactly as left: same messages, same order, nothing duplicated by the
    // round trip and nothing lost to it.
    //
    // The count first, and retrying: this rail is mounting against a cold cache
    // after a full reload, so the thread arrives over a round trip. `threadText`
    // is a one-shot `allInnerTexts()`, which on a slow runner would happily
    // snapshot the empty state and compare *that*.
    await expect(railMessages(page)).toHaveCount(2);
    expect(await threadText(page)).toEqual(thread);
    await expect(page.getByTestId("tutor-rail-empty")).toHaveCount(0);

    // And it is still a live conversation, not a transcript: the next turn
    // appends to it.
    await askTutor(page, "One more thing — what comes next?");
    await expect(learnerMessages(page)).toHaveCount(2);
    await expect(tutorReplies(page)).toHaveCount(2);

    // The path is where it was left, too: the conversation round trip moved no
    // Phase 1 state (TDD §3).
    await page.goto(`/paths/${pathId}`);
    await expect(page.getByTestId("path-rail")).toBeVisible();
  });
});
