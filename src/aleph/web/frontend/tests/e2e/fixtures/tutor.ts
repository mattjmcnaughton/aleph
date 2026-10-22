// Shared vocabulary for the W9/W11-W16 journeys (AL-260): opening the rail,
// asking a question, and reading the thread back — plus the two facts about the
// deterministic stub that the specs assert against.
//
// It sits beside `journey.ts` rather than inside it: Phase 1's helpers are about
// paths, lessons and progress, and nothing here has any business changing them.
// The rules it encodes are the tutor's own:
//
// 1. **A turn is a unit.** Nothing here returns until the reply has *settled* —
//    the live streaming bubble is gone and the tutor message has been appended.
//    A test that reads a half-streamed bubble would be asserting on a state the
//    product deliberately treats as not-yet-a-reply (TDD D2).
// 2. **Sentinels live in the question**, and every one of them is stateless: the
//    stub fires it on every send, so no spec depends on stub state carried
//    across requests (AL-260's acceptance criterion).
// 3. **The rail is a near-full-height sheet at 390x844.** Close it before
//    interacting with the lesson, then reopen it to continue the conversation.

import { type Locator, type Page, expect } from "@playwright/test";
import { ACTION_TIMEOUT, GENERATION_TIMEOUT } from "./journey";

/**
 * Sentinels the stub model reads out of the **question** text
 * (`services/stub_model.py`). Stripped from everything it generates, and
 * stateless — each one fires on every send that carries it.
 */
export const FORCE_TUTOR_FAILURE = "[force-tutor-failure]";
export const FORCE_TUTOR_REFUSAL = "[force-tutor-refusal]";
export const FORCE_TUTOR_CHECK = "[force-tutor-check]";

/** The **topic** sentinel that seeds a lesson with a checkable factual error (W16). */
export const FORCE_LESSON_ERROR = "[force-lesson-error]";

/**
 * The stub's canonical false claim and the correction it streams back — the
 * source of truth is `services/stub_model.py` (`LESSON_ERROR_*`), restated here
 * because a Playwright spec cannot import Python. Keep the two in step: W16
 * asserts the correction by string match, which is the whole point of the claim
 * being wrong in a way no phrasing can rescue.
 */
export const LESSON_ERROR_FALSE_VALUE = "50 degrees Celsius";
export const LESSON_ERROR_FALSE_CLAIM = "water boils at 50 degrees Celsius at sea level";
export const LESSON_ERROR_CORRECTION = "water boils at 100 degrees Celsius at sea level";

/**
 * `TUTOR_REFUSAL_REPLY` (`services/stub_model.py`), verbatim — W15's assertion
 * target. Restated here for the same reason as the constants above; the Python
 * definition is the documented source.
 */
export const TUTOR_REFUSAL_REPLY =
  "I am not going to help with that one — it sits outside what this tutor " +
  "covers, and I would rather say so plainly than answer it badly. Nothing " +
  "has broken here: this is a boundary, not a failure. I am still right here " +
  "for the lesson you are reading, so ask me about the passage and we can " +
  "pick straight back up.";

/**
 * The rail's own failure copy for a model turn that fell over — `_FAILURE_COPY`
 * for `upstream_error` in `services/tutor.py`, which is what a stub raising
 * mid-stream reports (W14). Asserted in full: the wording is a product promise
 * (PRD §5.7 — never "check your connection" for a server-side failure), so a
 * drift in it should be seen, not absorbed.
 */
export const TUTOR_UPSTREAM_FAILURE_COPY =
  "The tutor couldn't finish that answer. Nothing was saved — ask again when you're ready.";

// --- Opening and closing the rail --------------------------------------------

/** Open the rail from the floating mark — the phone's way in (PRD §5.1). */
export async function openRail(page: Page): Promise<void> {
  await page.getByTestId("tutor-rail-mark").click();
  await expect(page.getByTestId("tutor-rail")).toBeVisible({ timeout: ACTION_TIMEOUT });
}

/** Collapse the rail; the mark comes back in its place. */
export async function closeRail(page: Page): Promise<void> {
  await page.getByTestId("tutor-rail-collapse").click();
  await expect(page.getByTestId("tutor-rail")).toHaveCount(0);
  await expect(page.getByTestId("tutor-rail-mark")).toBeVisible();
}

// --- The thread ---------------------------------------------------------------

/** Every message bubble in the thread, oldest first. */
export function railMessages(page: Page): Locator {
  return page.getByTestId("tutor-rail-message");
}

/** The tutor's replies alone (the learner's own questions are the other role). */
export function tutorReplies(page: Page): Locator {
  return page.locator('[data-testid="tutor-rail-message"][data-role="tutor"]');
}

/** The learner's messages alone. */
export function learnerMessages(page: Page): Locator {
  return page.locator('[data-testid="tutor-rail-message"][data-role="learner"]');
}

/**
 * The prose column of reply `index` — the bubble **minus its aleph mark**.
 *
 * A tutor bubble is the decorative glyph (an `aria-hidden` span) beside a single
 * content `<div>`, so the reply the learner reads is that div and nothing else.
 * Reading the bubble whole would prefix every assertion with "א", which is how
 * an exact-wording check (W15's refusal copy) fails for a reason that has
 * nothing to do with the wording.
 */
export function replyBody(page: Page, index: number): Locator {
  return tutorReplies(page).nth(index).locator("> div");
}

/** `replyBody`'s text, trimmed. */
export async function replyText(page: Page, index: number): Promise<string> {
  return (await replyBody(page, index).innerText()).trim();
}

/** The text of every message in the thread, in order — a thread's identity. */
export async function threadText(page: Page): Promise<string[]> {
  return railMessages(page).allInnerTexts();
}

/**
 * Ask a question and wait for the whole turn to settle: the reply appended, the
 * live bubble gone, and the composer open again. Returns the reply's text.
 *
 * The wait is on the *count* of replies rather than on any one bubble, because
 * a settled reply is a new element — the streaming bubble is a different node
 * that is torn down when the turn lands (`useTutorRail`, invariant 3).
 */
export async function askTutor(page: Page, question: string): Promise<string> {
  const before = await tutorReplies(page).count();
  await page.getByTestId("tutor-rail-input").fill(question);
  await page.getByTestId("tutor-rail-send").click();
  return settledReply(page, before);
}

/** Tap one of the composer's one-tap asks (PRD §5.3) and wait for the reply. */
export async function tapSuggestion(page: Page, label: string): Promise<string> {
  const before = await tutorReplies(page).count();
  const disclosure = page.getByRole("button", { name: "Suggested questions", exact: true });
  if ((await disclosure.getAttribute("aria-expanded")) !== "true") {
    await disclosure.click();
  }
  await page.getByTestId("tutor-rail-suggestion").filter({ hasText: label }).click();
  return settledReply(page, before);
}

/**
 * Tap a revealed check's follow-up ask (PRD §5.5) and wait for the reply.
 * `within` scopes it to one card — a thread can hold several, and every card
 * offers the same two labels.
 */
export async function tapCheckFollowUp(
  page: Page,
  label: string,
  within?: Locator,
): Promise<string> {
  const before = await tutorReplies(page).count();
  const scope = within ?? page.getByTestId("tutor-rail-messages");
  await scope.getByTestId("tutor-rail-check-follow-up").filter({ hasText: label }).first().click();
  return settledReply(page, before);
}

/**
 * Wait for reply number `before + 1` to settle, and return its text.
 *
 * `GENERATION_TIMEOUT` rather than `ACTION_TIMEOUT`: a turn is a model call plus
 * a persist, and on a loaded CI runner that is the same order of wait as a
 * lesson generation — even though the stub itself answers instantly.
 */
async function settledReply(page: Page, before: number): Promise<string> {
  const replies = tutorReplies(page);
  await expect(replies).toHaveCount(before + 1, { timeout: GENERATION_TIMEOUT });
  // The settle is only complete when the live bubble is gone and the composer
  // has reopened; asserting the reply's arrival alone can catch the frame in
  // between, where a follow-up tap would be a no-op.
  await expect(page.getByTestId("tutor-rail-streaming")).toHaveCount(0);
  await expect(page.getByTestId("tutor-rail-send")).toBeVisible();
  return replyText(page, before);
}

/** The most recent tutor reply's rendered text. */
export async function lastReply(page: Page): Promise<string> {
  return replyText(page, (await tutorReplies(page).count()) - 1);
}

// --- Grounding ----------------------------------------------------------------

/**
 * The stub Read passage's recognizable slice, read off the rendered lesson:
 * its lead heading (`## Lesson N: the … of …`), which is exactly what
 * `stub_passage_slice` hands the streamed stub to quote back (TDD §11).
 *
 * Taken from the DOM rather than rebuilt in TypeScript on purpose — that is what
 * makes W9's grounding assertion *structural*: the reply names the words this
 * lesson is actually showing the learner, whatever they turned out to be.
 */
export async function passageSlice(page: Page): Promise<string> {
  const heading = page.getByTestId("lesson-read-passage").locator("h2").first();
  const text = (await heading.innerText()).trim();
  expect(text.length).toBeGreaterThan(0);
  return text;
}

/**
 * The Quick check's options **as the learner reads them**, in keyed order.
 *
 * `journey.ts`'s `quickCheckOptions` addresses the radio `input`s, which is
 * right for state (`data-correct`, `toBeChecked`) and useless for text: the
 * inputs are `sr-only` and the visible option lives on the enclosing `<label>`.
 * W13 needs the strings — it has to know the answer it is checking is not being
 * given away — and W16 needs to find one by its wording.
 */
export function quickCheckOptionTexts(page: Page): Promise<string[]> {
  return page.locator('label[for^="quick-check-option-"]').allInnerTexts();
}

// --- Wire reads ---------------------------------------------------------------

/** A `GET` on the app's own API, asserted OK and parsed — every read below. */
async function fetchJson<T>(page: Page, apiPath: string): Promise<T> {
  const response = await page.request.get(`/api/v1${apiPath}`);
  expect(response.ok()).toBe(true);
  return response.json() as Promise<T>;
}

/**
 * The thread straight off `GET /lessons/{id}/conversation`, bypassing the rendered
 * rail. The DOM can only speak for what was rendered; W13's leak assertion has
 * to speak for what was *delivered* (the same reason W6 reads the lesson payload
 * at the wire).
 */
export function fetchConversation(
  page: Page,
  lessonId: string,
): Promise<{ messages: { role: string; content: string }[] }> {
  return fetchJson(page, `/lessons/${lessonId}/conversation`);
}

/** A lesson's full payload off the wire (W12/W16's bit-identical comparison). */
export function fetchLesson(page: Page, lessonId: string): Promise<unknown> {
  return fetchJson(page, `/lessons/${lessonId}`);
}

/**
 * `GET /paths/{id}` as far as a spec has to *reach into* it.
 *
 * Named fields only where a comparison has to delete one (W12 drops the two
 * fields background generation moves on its own); everything else stays under
 * the index signature, deliberately unnamed — a denylist that had to enumerate
 * the payload would stop comparing any field added after it was written.
 */
export interface PathPayload {
  progress: Record<string, unknown> & { generated_lessons?: number };
  units: (Record<string, unknown> & {
    lessons: (Record<string, unknown> & { generation_state?: string })[];
  })[];
  [key: string]: unknown;
}

/** A path's full payload off the wire. */
export function fetchPath(page: Page, pathId: string): Promise<PathPayload> {
  return fetchJson(page, `/paths/${pathId}`);
}

/** The open lesson's id, from the seam the lesson view keeps for exactly this. */
export async function openLessonId(page: Page): Promise<string> {
  return (await page.getByTestId("lesson-view-id").innerText()).trim();
}
