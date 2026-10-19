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

/**
 * `REVISED_PASSAGE_MARKER` (`services/stub_model.py`), verbatim — W18's
 * assertion target.
 *
 * It is what closes the revision loop end to end in a browser: the sentinel puts
 * `SHAPING_REVISION_INSTRUCTION` on the Proposal, apply writes it to the
 * lesson's `revision_instruction`, the Phase 1 lesson prompt carries it in its
 * revision block (D7), and the stub — recognizing *its own* instruction — plants
 * this sentence in the regenerated Read passage. Seeing it on screen therefore
 * proves the instruction travelled the whole chain, which no wording check on
 * the reply could.
 */
export const REVISED_PASSAGE_MARKER = "This passage was regenerated from a learner's revision.";

/** The sentinel Addition's size — `_ADDITION_LESSON_COUNT` in the stub. */
export const ADDITION_LESSON_COUNT = 2;

/**
 * `SHAPING_DECLINED_EDIT_REPLY` (`services/stub_model.py`), verbatim — W20's
 * assertion target, and **Markdown source**, not rendered text.
 *
 * Asserted in full rather than by keyword because the **declined edit** is a
 * product promise (PRD §5.7): it names what shaping can do, and it must read as
 * neither a failure nor a safety refusal. A drift in it should be seen, not
 * absorbed — the same reason W15 pins the refusal copy.
 *
 * Compare it against the *wire* (`fetchShapingConversation`): the rail renders
 * this through `markdown.tsx`, so the `**add**` emphasis is a `<strong>` on
 * screen and the asterisks are gone from `innerText`.
 */
export const SHAPING_DECLINED_EDIT_REPLY =
  "That is not one of the changes I can make to a path. What shaping can do is " +
  "**add** lessons — on their own or grouped as a new unit — anywhere you have " +
  "not started yet, and **revise** a lesson you have not started yet so it " +
  "lands differently. What it cannot do is remove or reorder lessons, change " +
  "work you have already engaged with, or touch your progress. Your path is " +
  "exactly as it was. Tell me what you were hoping that change would get you " +
  "and there is a good chance an addition or a revision gets you there.";

/**
 * The rail's failure copy for a shaping turn that fell over — `_FAILURE_COPY`
 * for `upstream_error` in `services/tutor.py`.
 *
 * The *tutor's* module, and deliberately so: TDD §5.8 is a delta over Phase 2
 * §5.6, so both rails report a failed turn with one set of words. It is restated
 * here rather than imported from `tutor.ts` because what this constant means to
 * a shaping spec is "not a **declined edit**" — the two are different answers to
 * different situations, and W20 is where that distinction is asserted.
 */
export const SHAPING_UPSTREAM_FAILURE_COPY =
  "The tutor couldn't finish that answer. Nothing was saved — ask again when you're ready.";

/**
 * The server's `409 engaged` sentence, verbatim (`services/shaping.py`) — what
 * the Change history says when the undo window has closed for good.
 *
 * The client never pre-disables undo for engagement (it cannot derive it, and it
 * can change between the list rendering and the tap), so this copy is reached by
 * *tapping* — which is exactly the promise W19 asserts: say plainly that the
 * window is closed rather than hide the button.
 */
export const UNDO_ENGAGED_COPY =
  "you have already started one of the lessons this change made, so it is now " +
  "part of your path's history";

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
 * Every Proposal ask gets a unique subject. The stub seeds the titles it
 * proposes from the question text, so two identical asks in one journey produce
 * identical titles — and once the first is applied, the second proposal fails
 * the distinct-titles predicate server-side and never reaches the rail.
 * Real learners hit this only by asking for the same thing twice verbatim;
 * the counter keeps the harness off that edge.
 *
 * One counter for both shapes, because the collision is about *titles* and both
 * builders draw theirs from the same question text.
 */
let proposalAskCounter = 0;

/** Ask for an Addition and wait for the card it produces to be pending. */
export async function askForAddition(page: Page): Promise<Locator> {
  proposalAskCounter += 1;
  await askShaper(page, `Add practice round ${proposalAskCounter} on this ${FORCE_PROPOSAL_ADD}`);
  return pendingCard(page);
}

/** Ask for a Revision and wait for the card it produces to be pending. */
export async function askForRevision(page: Page): Promise<Locator> {
  proposalAskCounter += 1;
  await askShaper(
    page,
    `Make my next lesson simpler, take ${proposalAskCounter} ${FORCE_PROPOSAL_REVISE}`,
  );
  return pendingCard(page);
}

/** The newest card in the thread, once it is offering itself. */
async function pendingCard(page: Page): Promise<Locator> {
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

/** `POST /messages/{id}/apply-proposal` — the one write into path structure. */
const APPLY_ROUTE = /\/api\/v1\/messages\/[^/]+\/apply-proposal$/;

/** One lesson as the apply response draws it (`path` is `GET /paths/{id}`). */
export interface AppliedLesson {
  id: string;
  title: string;
  generation_state: string;
}

/** The `200 {change, path}` body apply answers with (docs/api.md). */
export interface AppliedPayload {
  change: ChangePayload;
  path: { units: { lessons: AppliedLesson[] }[] };
}

/**
 * Apply, and hand back the body the server answered with.
 *
 * For the one claim the rendered rail cannot hold still for: **apply lands
 * structure, not content** (PRD §5.7). Added lessons arrive `ungenerated` and
 * ride Phase 1 from there — and because the same response kicks the prefetch
 * driver, they can be generated by the time any assertion reaches the DOM.
 * Reading the response is not a workaround for that race; it is the exact
 * statement of the claim, at the moment the claim is about.
 */
export async function applyProposalReadingResponse(
  page: Page,
  card: Locator,
): Promise<AppliedPayload> {
  const response = page.waitForResponse(
    (candidate) => APPLY_ROUTE.test(candidate.url()) && candidate.request().method() === "POST",
  );
  await applyProposal(page, card);
  return (await (await response).json()) as AppliedPayload;
}

/** Every lesson in an applied path payload, in path order. */
export function appliedLessons(payload: AppliedPayload): AppliedLesson[] {
  return payload.path.units.flatMap((unit) => unit.lessons);
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
