// W11 — The conversation persists (PRD §8, §5.8).
//
// Two halves, and they fail for different reasons, so they are two tests:
//
// 1. **Across lessons.** Each lesson owns its thread. Moving to another lesson
//    shows a separate conversation; returning restores the first one. Clearing
//    the second lesson must not erase the first lesson's history.
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
  test("each lesson restores its own thread and clearing B leaves A intact", async ({ page }) => {
    await createPath(page, uniqueTopic("Bridge cables"));
    await openLessonAt(page, 0);
    const firstUrl = page.url();
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

    // A new lesson has no copy of the first lesson's chat.
    await openRail(page);
    await expect(page.getByTestId("tutor-rail-empty")).toBeVisible();
    await expect(railMessages(page)).toHaveCount(0);
    await expect(page.getByTestId("tutor-rail-context-chip")).toContainText(secondTitle);

    // The second lesson gets its own grounded turn.
    const secondUrl = page.url();
    const slice = await passageSlice(page);
    const reply = await askTutor(page, "And what is this one about?");
    expect(reply).toContain(slice);
    await expect(learnerMessages(page)).toHaveCount(1);
    await expect(tutorReplies(page)).toHaveCount(1);
    const secondThread = await threadText(page);

    await page.goto(firstUrl);
    await expect(page.getByTestId("lesson-read-passage")).toBeVisible();
    await openRail(page);
    await expect(railMessages(page)).toHaveCount(2);
    expect(await threadText(page)).toEqual(thread);

    await page.goto(secondUrl);
    await expect(page.getByTestId("lesson-read-passage")).toBeVisible();
    await openRail(page);
    await expect(railMessages(page)).toHaveCount(2);
    expect(await threadText(page)).toEqual(secondThread);
    await page.getByTestId("tutor-rail-new-conversation").click();
    await page.getByTestId("tutor-rail-new-conversation-confirm").click();
    await expect(page.getByTestId("tutor-rail-empty")).toBeVisible();

    await page.goto(firstUrl);
    await expect(page.getByTestId("lesson-read-passage")).toBeVisible();
    await openRail(page);
    await expect(railMessages(page)).toHaveCount(2);
    expect(await threadText(page)).toEqual(thread);

    await page.goto(secondUrl);
    await expect(page.getByTestId("lesson-read-passage")).toBeVisible();
    await openRail(page);
    await expect(page.getByTestId("tutor-rail-empty")).toBeVisible();
    await expect(railMessages(page)).toHaveCount(0);
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
