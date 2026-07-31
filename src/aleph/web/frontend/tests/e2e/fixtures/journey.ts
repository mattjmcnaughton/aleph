// Shared vocabulary for the W1-W8 journeys: the handful of moves a learner
// makes (start a path, open a lesson, answer a Quick check, mark complete) plus
// the locators the specs assert against.
//
// Two rules the helpers encode, both from TDD §12:
//
// 1. **Structure, never text.** The stub model's content is deterministic but
//    its wording is an implementation detail. "Real content renders" therefore
//    asserts invariants — a non-empty Read passage, a 3-4 option Quick check,
//    an Outcome with an explanation — never a literal string.
// 2. **Isolation by topic, not by database.** Every Playwright worker shares one
//    `aleph_e2e` database and one learner account, and the journeys deliberately
//    exercise the real "Your paths" list, so nothing can assume an empty account.
//    Each test coins a unique topic (`uniqueTopic`) and asserts through the
//    resulting path's id, so residue from other specs (and from previous runs of
//    the suite) is invisible to it.

import { type Locator, type Page, expect } from "@playwright/test";

/** The learner's self-assessed starting point (`Level` in lib/api.ts). */
export type Level = "new_to_it" | "some_experience" | "work_in_it";

/** `/paths/{uuid}` — what onboarding navigates to once the outline is ready. */
const PATH_URL_RE = /\/paths\/([0-9a-f-]{36})(?:$|[?#])/;

/**
 * Budget for anything that waits on generation. Generous on purpose: the app
 * polls on the §14 2s->5s backoff, so a state change costs up to a poll even
 * though the stub model itself answers instantly — and CI runners (and a
 * developer machine with something else running on it) stretch every one of
 * those cycles.
 */
export const GENERATION_TIMEOUT = 90_000;

/** Budget for a plain interaction round trip (a POST and its re-render). */
export const ACTION_TIMEOUT = 30_000;

/** How long to trust a surface's own polling before reloading it (see below). */
const POLL_PATIENCE = 15_000;

/**
 * Failures no reload can rescue, so `waitWithReload` gives up on them at once.
 *
 * Its retry loop exists for one failure — "this has not come true *yet*" — and a
 * locator that can never match (a strict-mode violation, an unparseable
 * selector) or a browser that is gone would otherwise be retried for the full
 * 90s budget and reported a minute and a half after the fact.
 *
 * Matched on the message rather than the class deliberately: a web-first
 * assertion throws Playwright's internal `ExpectError` whatever went wrong
 * inside it, so the class only ever says "an assertion failed", never why. In
 * particular `errors.TimeoutError` is NOT what `expect()` raises on a timeout
 * (only `waitFor`-style calls do), so keying on it would rethrow every ordinary
 * unmet assertion and disable the rescue entirely.
 */
const UNRESCUABLE =
  /strict mode violation|has been closed|Execution context was destroyed|Unknown engine|Failed to parse selector/;

/**
 * Wait for `assert` to hold, reloading the page between attempts.
 *
 * Every surface here keeps itself current by polling, and every one of those
 * polls can legitimately *stop* while a test is still waiting:
 *
 * - the lesson view gives up after `GENERATION_STALL_MS` (45s), degrading to the
 *   "isn't ready yet" notice **and switching its `refetchInterval` off** — so a
 *   generation that resolves later is never rendered;
 * - the path view stops as soon as nothing in the payload is resolving, so a
 *   snapshot taken in a gap between claims ends the loop;
 * - both views stop on a terminal error.
 *
 * All three are correct product behaviour — in each case the learner's move is
 * to come back to the page — but each turns "this machine is slow today" into a
 * test failure. So do what the learner does: reload and look again. The reload
 * also re-triggers the backend's idempotent resume (§5.4: a `GET` is a trigger),
 * which is the documented way an interrupted chain gets moving again.
 *
 * **Only for surfaces a reload preserves.** Onboarding (`/new`) holds the path
 * it created in component state, so reloading it drops the learner back to an
 * empty form; its waits stay plain (it polls to a terminal status and has no
 * stall cap, so there is nothing to rescue).
 */
async function waitWithReload(
  page: Page,
  assert: (timeout: number) => Promise<void>,
  timeout = GENERATION_TIMEOUT,
): Promise<void> {
  const deadline = Date.now() + timeout;
  for (let attempt = 0; ; attempt += 1) {
    if (attempt > 0) {
      await page.reload();
    }
    const remaining = deadline - Date.now();
    try {
      await assert(Math.max(1_000, Math.min(POLL_PATIENCE, remaining)));
      return;
    } catch (error) {
      if (Date.now() >= deadline) {
        throw error;
      }
      if (error instanceof Error && UNRESCUABLE.test(error.message)) {
        throw error;
      }
    }
  }
}

/** Wait for a testid to be visible, reloading if the surface stops updating. */
export async function waitForSurface(
  page: Page,
  testId: string,
  timeout = GENERATION_TIMEOUT,
): Promise<void> {
  await waitWithReload(
    page,
    (attemptTimeout) => expect(page.getByTestId(testId)).toBeVisible({ timeout: attemptTimeout }),
    timeout,
  );
}

/**
 * A topic no other test (or previous run) uses.
 *
 * Doubles as the stub model's seed: content is derived from the topic string
 * (SHA-256), so a unique topic also means content that cannot collide with
 * another spec's. Keep it short — the stub interpolates the topic into the Read
 * passage and caps it at 8 words for the §14 word band.
 */
export function uniqueTopic(prefix: string): string {
  const stamp = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  return `${prefix} ${stamp}`;
}

// --- Onboarding -------------------------------------------------------------

/**
 * Fill in and submit the onboarding form, without waiting for the outcome —
 * for the journeys that expect a refusal (W7) or a failure (W8) rather than a
 * path. The level radios are `sr-only` inputs inside their labels, so the label
 * is what a learner (and this helper) actually clicks.
 *
 * `beforeSubmit` runs on the filled-in form, just before the tap that submits
 * it: the seam for anything else a particular learner does on this screen — the
 * admin's model picker (W1, §5.3) is the one caller — so no spec has to
 * hand-roll the form to add one step to it.
 */
export async function startPath(
  page: Page,
  topic: string,
  level: Level = "new_to_it",
  beforeSubmit?: (page: Page) => Promise<void>,
): Promise<void> {
  await page.goto("/new");
  await page.locator("#onboarding-topic").fill(topic);
  await page.locator(`label[for="level-${level}"]`).click();
  await beforeSubmit?.(page);
  await page.getByRole("button", { name: "Build my path" }).click();
}

/**
 * Start a path and wait for onboarding to hand off to the path view.
 * Returns the new path's id.
 */
export async function createPath(
  page: Page,
  topic: string,
  level: Level = "new_to_it",
): Promise<string> {
  await startPath(page, topic, level);
  // Onboarding is the one surface a reload does not preserve (see
  // `waitWithReload`), so this waits plainly — it polls to a terminal status and
  // has no stall cap, so there is nothing a reload would rescue.
  await page.waitForURL(PATH_URL_RE, { timeout: GENERATION_TIMEOUT });
  await waitForSurface(page, "path-rail");
  return pathIdFromUrl(page.url());
}

/** The path id in a `/paths/{id}` URL. */
export function pathIdFromUrl(url: string): string {
  const match = PATH_URL_RE.exec(url);
  if (match === null) {
    throw new Error(`not a path URL: ${url}`);
  }
  return match[1];
}

/** Open a path view directly (the switcher's own link is asserted separately). */
export async function gotoPath(page: Page, pathId: string): Promise<void> {
  await page.goto(`/paths/${pathId}`);
  await waitForSurface(page, "path-rail");
}

// --- The path view ----------------------------------------------------------

/** Every lesson row in the rail, in path order (unit by unit). */
export function railLessons(page: Page): Locator {
  return page.getByTestId("path-rail").locator("button[data-unlock-state]");
}

/** Wait for a rail row to reach a state, reloading if the rail stops updating. */
export async function expectRailState(
  page: Page,
  index: number,
  attribute: "data-unlock-state" | "data-generation-state",
  value: string,
  timeout = GENERATION_TIMEOUT,
): Promise<void> {
  await waitWithReload(
    page,
    (attemptTimeout) =>
      expect(railLessons(page).nth(index)).toHaveAttribute(attribute, value, {
        timeout: attemptTimeout,
      }),
    timeout,
  );
}

/**
 * Assert a rail row's state **on the page as it stands** — no reload.
 *
 * The reload-backed waits above are right for anything downstream of generation:
 * a surface that has stopped polling can only be moved by re-fetching it. They
 * are wrong for the beats that follow a *completion*, because a stale rail
 * corrects itself on any reload — which is exactly the bug the `["paths", …]`
 * invalidation on completion (routes/lessons.$lessonId.tsx) exists to prevent.
 * Asserting in place keeps that regression visible here: the learner tapping
 * "Back to your path" does not reload, so neither does this.
 *
 * Still a retrying assertion — the refetch it is waiting on is a round trip.
 */
export async function expectRailStateInPlace(
  page: Page,
  index: number,
  attribute: "data-unlock-state" | "data-generation-state",
  value: string,
): Promise<void> {
  await expect(railLessons(page).nth(index)).toHaveAttribute(attribute, value, {
    timeout: ACTION_TIMEOUT,
  });
}

/** `expectProgress` without the reload — the post-completion beat (see above). */
export async function expectProgressInPlace(
  locator: Locator,
  done: number,
  total: number,
): Promise<void> {
  await expect(locator).toHaveText(progressPattern(done, total), { timeout: ACTION_TIMEOUT });
}

/** Wait until the lesson at `index` has finished generating (W3: no dead ends). */
export async function waitForLessonGenerated(page: Page, index: number): Promise<void> {
  await expectRailState(page, index, "data-generation-state", "generated");
}

/**
 * Open the lesson at `index` from the rail and wait for its content — the same
 * two taps a learner makes. Assumes the lesson is unlocked; a locked row is
 * inert by design.
 */
export async function openLessonAt(page: Page, index: number): Promise<void> {
  await waitForLessonGenerated(page, index);
  await railLessons(page).nth(index).click();
  await waitForSurface(page, "lesson-read-passage");
}

/**
 * Assert an "N of M lessons complete" readout — on the path view or on a
 * switcher row.
 *
 * A retrying assertion, not a read-then-compare: the readout is server state
 * arriving through a poll or a post-completion refetch, so the first render a
 * test sees can still be the previous value. Reload-backed for the same reason
 * every other wait here is (see `waitWithReload`).
 */
export async function expectProgress(
  page: Page,
  locator: Locator,
  done: number,
  total: number,
): Promise<void> {
  const expected = progressPattern(done, total);
  await waitWithReload(page, (timeout) => expect(locator).toHaveText(expected, { timeout }));
}

/** The exact "N of M lessons complete" readout, for either progress surface. */
function progressPattern(done: number, total: number): RegExp {
  return new RegExp(`^${done} of ${total} lessons? complete$`);
}

// --- The switcher -----------------------------------------------------------

/** One row of "Your paths", addressed by id so other rows never interfere. */
export function pathRow(page: Page, pathId: string): Locator {
  return page.locator(`[data-testid="path-list-item"][data-path-id="${pathId}"]`);
}

/** Open the switcher and wait for the list (never the loading placeholder). */
export async function gotoSwitcher(page: Page): Promise<void> {
  await page.goto("/");
  await waitForSurface(page, "paths-list");
}

// --- The lesson view --------------------------------------------------------

export function quickCheckOptions(page: Page): Locator {
  return page.getByTestId("quick-check-option");
}

/**
 * The open lesson's title, as the learner reads it — the page's one `h1`.
 *
 * Every caller wants it for the same reason (to say "this lesson, not that
 * one"): the rail's scope chip names it, and W11 tells two lessons apart by it.
 */
export async function lessonTitle(page: Page): Promise<string> {
  return (await page.getByRole("heading", { level: 1 }).innerText()).trim();
}

/**
 * The invariants a generated lesson must satisfy (§14, TDD §12): a non-empty
 * Read passage and a single-select Quick check of 3-4 options with a stem.
 * Returns the option count so the caller can pick an answer.
 */
export async function expectLessonContent(page: Page): Promise<number> {
  const passageEl = page.getByTestId("lesson-read-passage");
  const passage = (await passageEl.innerText()).trim();
  expect(passage.length).toBeGreaterThan(0);

  // The Read passage is Markdown and must arrive rendered, not printed. The stub
  // model emits every construct the real agent is prompted for, so a real browser
  // proves the whole chain — agent output → API → components/markdown.tsx.
  await expect(passageEl.locator("h2")).not.toHaveCount(0);
  await expect(passageEl.locator("li")).not.toHaveCount(0);
  await expect(passageEl.locator("pre code")).not.toHaveCount(0);
  await expect(passageEl.locator("table th")).not.toHaveCount(0);
  // The mermaid diagram draws for real here: a browser, the lazily-loaded
  // library, and the stub's always-valid flowchart. `data-mermaid-status`
  // distinguishes a drawn diagram from this component's source fallback, so a
  // silently-degrading renderer fails the journey rather than passing on the
  // fallback's text.
  await expect(passageEl.getByTestId("mermaid-diagram")).toHaveAttribute(
    "data-mermaid-status",
    "rendered",
  );
  await expect(passageEl.locator("[data-testid='mermaid-diagram'] svg")).not.toHaveCount(0);
  // Consumed syntax never reaches the learner as literal characters.
  expect(passage).not.toContain("```");
  expect(passage).not.toContain("**");

  await expect(page.getByTestId("quick-check")).toBeVisible();
  const stem = (await page.getByTestId("quick-check-stem").innerText()).trim();
  expect(stem.length).toBeGreaterThan(0);

  const options = await quickCheckOptions(page).count();
  expect(options).toBeGreaterThanOrEqual(3);
  expect(options).toBeLessThanOrEqual(4);
  return options;
}

/** The result of an Attempt — non-gating either way (`LessonOutcome` in lib/api.ts). */
export type Outcome = "correct" | "incorrect";

/** What the Attempt revealed — the keyed answer + explanation (W6). */
export interface Reveal {
  outcome: Outcome;
  correctIndex: number;
  explanation: string;
}

/** Which option is marked correct, or -1 before an Attempt reveals one. */
export async function revealedCorrectIndex(page: Page): Promise<number> {
  return quickCheckOptions(page).evaluateAll((options) =>
    options.findIndex((option) => option.getAttribute("data-correct") === "true"),
  );
}

/**
 * Answer the Quick check with `index` and read the Outcome back. Like the level
 * radios, the option inputs are `sr-only` inside their labels, so the label is
 * the real tap target.
 */
export async function answerQuickCheck(page: Page, index: number): Promise<Reveal> {
  await page.locator(`label[for="quick-check-option-${index}"]`).click();
  await page.getByTestId("quick-check-submit").click();
  return readReveal(page);
}

/**
 * Read the Outcome the Attempt just submitted revealed.
 *
 * Split out of `answerQuickCheck` because how the two taps are *made* is not
 * always a plain click — the tutor journeys reach past an open bottom sheet with
 * `tapAboveRail` — while what the reveal means is the same either way, and
 * should be read in exactly one place.
 */
export async function readReveal(page: Page): Promise<Reveal> {
  const outcomeReveal = page.getByTestId("outcome-reveal");
  // A synchronous POST, but it is still a round trip on a loaded machine.
  await expect(outcomeReveal).toBeVisible({ timeout: ACTION_TIMEOUT });
  const outcome = await outcomeReveal.getAttribute("data-outcome");
  // Narrows as well as asserts: the Outcome is one of exactly two branches, and
  // every caller that compares or negates one depends on that (W6).
  if (outcome !== "correct" && outcome !== "incorrect") {
    throw new Error(`Outcome must be correct or incorrect, got: ${outcome}`);
  }

  const explanation = (await page.getByTestId("outcome-explanation").innerText()).trim();
  expect(explanation.length).toBeGreaterThan(0);

  return { outcome, correctIndex: await revealedCorrectIndex(page), explanation };
}

/**
 * Mark the open lesson complete and wait for the confirmation surface.
 *
 * Waits plainly rather than through `waitWithReload`: the click and the surface
 * it produces are two halves of one round trip, and reloading mid-flight would
 * drop the test back to a page it would have to re-click.
 */
export async function markComplete(page: Page): Promise<void> {
  await page.getByTestId("lesson-complete-button").click();
  await expect(page.getByTestId("lesson-completed")).toBeVisible({ timeout: ACTION_TIMEOUT });
}

/** Follow "Back to your path" from a completed lesson. */
export async function backToPath(page: Page): Promise<void> {
  await page.getByTestId("lesson-completed-back").click();
  await waitForSurface(page, "path-rail");
}

/**
 * Work a whole lesson: open it, answer the Quick check, mark it complete, and
 * return to the path view. `answer` defaults to the first option — the learner
 * cannot know which is keyed (W6), and the Outcome is non-gating either way.
 */
export async function completeLessonAt(page: Page, index: number, answer = 0): Promise<void> {
  await openLessonAt(page, index);
  await expectLessonContent(page);
  await answerQuickCheck(page, answer);
  await markComplete(page);
  await backToPath(page);
}
