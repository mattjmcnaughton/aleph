// Shared vocabulary for the shaping journeys (AL-331): opening the shaping rail
// on the path view, asking for an edit, and acting on the Proposal that comes
// back — plus the facts about the deterministic stub the specs assert against.
//
// It sits beside `tutor.ts` rather than inside it, for that file's own reason:
// the two rails are separate surfaces with separate testids and separate
// threads (PRD §5.8), and a helper that reached across them would be the first
// thing to break W21's "the in-lesson tutor stays bit-identical".
//
// Three rules it encodes:
//
//  1. **Nothing happens until Apply.** Every helper below that changes the path
//     goes through the card's own button. A Proposal on screen has changed
//     nothing, and `ghostRows` is how a spec says so.
//  2. **Sentinels live in the question**, stateless, stripped from the reply —
//     the same discipline the tutor's sentinels follow (`services/stub_model.py`).
//  3. **Structure, never wording.** The stub's prose is deterministic but its
//     phrasing is an implementation detail; what a spec asserts is the shape of
//     the payload and the state of the card.

import { type Locator, type Page, expect } from "@playwright/test";
import { ACTION_TIMEOUT, GENERATION_TIMEOUT } from "./journey";

/**
 * Sentinels the stub shaper reads out of the **question** text
 * (`services/stub_model.py`). Stripped from the reply, and stateless — each one
 * fires on every send that carries it.
 */
export const FORCE_PROPOSAL_ADD = "[force-proposal-add]";
export const FORCE_PROPOSAL_REVISE = "[force-proposal-revise]";
export const FORCE_SHAPING_DECLINE = "[force-shaping-decline]";
export const FORCE_SHAPING_FAILURE = "[force-shaping-failure]";

/**
 * How the stub titles what it proposes (`build_stub_addition_proposal` /
 * `build_stub_revision_proposal`). Restated here because a Playwright spec
 * cannot import Python; the Python definitions are the documented source. They
 * exist so an added or revised lesson is recognizable in a rail full of
 * generated ones — which is exactly what "the ghosts became real rows" needs.
 */
export const ADDED_LESSON_TITLE_PREFIX = "Added on request:";
export const REVISED_LESSON_TITLE_PREFIX = "Revised on request:";

/** The sentinel Addition's size — `_ADDITION_LESSON_COUNT` in the stub. */
export const ADDITION_LESSON_COUNT = 2;

// --- Opening the shaping rail -------------------------------------------------

/** Open the shaping rail from its floating mark — the phone's way in (PRD §5.1). */
export async function openShapingRail(page: Page): Promise<void> {
  await page.getByTestId("shaping-rail-mark").click();
  await expect(page.getByTestId("shaping-rail")).toBeVisible({ timeout: ACTION_TIMEOUT });
}

/** Collapse the shaping rail; the mark comes back in its place. */
export async function closeShapingRail(page: Page): Promise<void> {
  await page.getByTestId("shaping-rail-collapse").click();
  await expect(page.getByTestId("shaping-rail")).toHaveCount(0);
  await expect(page.getByTestId("shaping-rail-mark")).toBeVisible();
}

// --- The thread ---------------------------------------------------------------

/** The tutor's replies in the shaping thread. */
export function shapingReplies(page: Page): Locator {
  return page.locator('[data-testid="shaping-rail-message"][data-role="tutor"]');
}

/** Every proposal card in the thread, oldest first. */
export function proposalCards(page: Page): Locator {
  return page.getByTestId("shaping-rail-proposal");
}

/**
 * Ask the shaping tutor something and wait for the whole turn to settle: the
 * reply appended, the live bubble gone, the composer open again.
 *
 * `GENERATION_TIMEOUT` for the same reason `askTutor` uses it — a turn is a
 * model call plus a persist, which on a loaded runner is a generation's worth of
 * wall time even though the stub answers instantly.
 */
export async function askShaper(page: Page, question: string): Promise<void> {
  const before = await shapingReplies(page).count();
  await page.getByTestId("shaping-rail-input").fill(question);
  await page.getByTestId("shaping-rail-send").click();
  await expect(shapingReplies(page)).toHaveCount(before + 1, { timeout: GENERATION_TIMEOUT });
  await expect(page.getByTestId("shaping-rail-streaming")).toHaveCount(0);
  await expect(page.getByTestId("shaping-rail-send")).toBeVisible();
}

/**
 * Every Addition ask gets a unique subject. The stub seeds its lesson titles
 * from the question text, so two identical asks in one journey produce
 * identical titles — and once the first is applied, the second proposal fails
 * the distinct-titles predicate server-side and never reaches the rail.
 * Real learners hit this only by asking for the same thing twice verbatim;
 * the counter keeps the harness off that edge.
 */
let additionAskCounter = 0;

/** Ask for an Addition and wait for the card it produces to be pending. */
export async function askForAddition(page: Page): Promise<Locator> {
  additionAskCounter += 1;
  await askShaper(page, `Add practice round ${additionAskCounter} on this ${FORCE_PROPOSAL_ADD}`);
  const card = proposalCards(page).last();
  await expect(card).toHaveAttribute("data-state", "pending", { timeout: ACTION_TIMEOUT });
  return card;
}

/** Ask for a Revision and wait for the card it produces to be pending. */
export async function askForRevision(page: Page): Promise<Locator> {
  await askShaper(page, `Make my next lesson simpler ${FORCE_PROPOSAL_REVISE}`);
  const card = proposalCards(page).last();
  await expect(card).toHaveAttribute("data-state", "pending", { timeout: ACTION_TIMEOUT });
  return card;
}

// --- Ghost rows ---------------------------------------------------------------

/** The **ghost rows** the path rail is previewing right now (CONTEXT.md). */
export function ghostRows(page: Page): Locator {
  return page.getByTestId("path-rail-ghost");
}

/** The rows a pending **Revision** has marked "will be revised". */
export function revisingRows(page: Page): Locator {
  return page.getByTestId("path-rail-revising");
}

// --- Apply, Undo, the Change history -----------------------------------------

/**
 * Tap **Apply** on a card and wait for the Change to land — the card applied and
 * the rail's ghosts gone, because the response's `path` swapped them for real
 * rows in the same round trip (TDD §5.6).
 */
export async function applyProposal(page: Page, card: Locator): Promise<void> {
  await card.getByTestId("shaping-rail-proposal-apply").click();
  await expect(card).toHaveAttribute("data-state", "applied", { timeout: GENERATION_TIMEOUT });
  await expect(ghostRows(page)).toHaveCount(0);
}

/** Open the read-only **Change history** sheet from the rail's header. */
export async function openChangeHistory(page: Page): Promise<void> {
  await page.getByTestId("shaping-rail-change-history").click();
  await expect(page.getByTestId("shaping-rail-history")).toBeVisible({ timeout: ACTION_TIMEOUT });
}

/** Close the sheet and return to the conversation it covered. */
export async function closeChangeHistory(page: Page): Promise<void> {
  await page.getByTestId("shaping-rail-history-close").click();
  await expect(page.getByTestId("shaping-rail-history")).toHaveCount(0);
}

/** Every row of the Change history, newest first. */
export function changeRows(page: Page): Locator {
  return page.getByTestId("shaping-rail-history-change");
}

/** Undo the newest live Change from the sheet and wait for it to read undone. */
export async function undoNewestChange(page: Page): Promise<void> {
  const newest = changeRows(page).first();
  await newest.getByTestId("shaping-rail-history-undo").click();
  await expect(newest).toHaveAttribute("data-status", "undone", { timeout: GENERATION_TIMEOUT });
}

// --- Wire reads ---------------------------------------------------------------

/** A `GET` on the app's own API, asserted OK and parsed. */
async function fetchJson<T>(page: Page, apiPath: string): Promise<T> {
  const response = await page.request.get(`/api/v1${apiPath}`);
  expect(response.ok()).toBe(true);
  return response.json() as Promise<T>;
}

export interface ChangePayload {
  id: string;
  summary: string;
  kinds: string[];
  status: string;
  applied_at: string;
  undone_at: string | null;
}

/**
 * The Change history straight off `GET /paths/{id}/changes`. The DOM can only
 * speak for what was rendered; a spec asserting that the record *survived*
 * something has to speak for what was stored.
 */
export function fetchChanges(page: Page, pathId: string): Promise<{ changes: ChangePayload[] }> {
  return fetchJson(page, `/paths/${pathId}/changes`);
}

/** The shaping thread off the wire — the other thread, never the in-lesson one. */
export function fetchShapingConversation(
  page: Page,
  pathId: string,
): Promise<{ messages: { role: string; content: string; proposal: unknown }[] }> {
  return fetchJson(page, `/paths/${pathId}/shaping/conversation`);
}
