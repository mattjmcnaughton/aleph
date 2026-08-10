// Shared vocabulary for the analyst journeys (W29, W31 — Phase 6 TDD §11,
// ticket AL-560): deploy a Beat, wait for its rail to reach a real terminal
// entry, and the phone-viewport (390x844) checks AL-530's own review left for
// this suite to prove — jsdom has no layout, so "no horizontal scroll" and
// ">=44px touch target" could not be verified there (`beats.new.tsx`,
// `beat-card.tsx`, `beat-rail.tsx`, `brief-sources.tsx`'s own comments already
// claim these hold "by construction"; this file is what actually checks it).
//
// Reuses `fixtures/journey.ts`'s `waitForSurface`/`waitWithReload` rather than
// a second copy: a Beat's research run is Phase 1's trigger+poll shape one
// workload over (TDD §2), so the identical reload-backed rescue — real
// significance restated from that file's own docstring — is what keeps this
// spec waiting on the SAME live poll a learner's browser runs, and falls back
// to a hard reload (a fresh `GET`, re-draining server-side) exactly when a
// stuck client-side poll would otherwise hang the test forever. That fallback
// is what makes "the polling path is exercised for real" more than a slogan:
// three prior defects in this phase were "the client cannot see the run its
// own request started," and a bare `expect(...).toBeVisible()` with no reload
// rescue would hang on exactly that bug instead of catching it.

import { type Locator, type Page, expect } from "@playwright/test";
import { GENERATION_TIMEOUT, type Level, waitForSurface } from "./journey";

/** `/beats/{uuid}` — where `routes/beats.new.tsx` navigates on a successful deploy. */
const BEAT_URL_RE = /\/beats\/([0-9a-f-]{36})(?:$|[?#])/;

/** `/briefs/{uuid}` — where a published rail row links (`beat-rail.tsx`). */
const BRIEF_URL_RE = /\/briefs\/([0-9a-f-]{36})(?:$|[?#])/;

/**
 * Stub sentinels in the Beat's topic string (the Phase 1 precedent, TDD §11):
 *
 * - `FORCE_RETRIEVAL_FAILURE` — `services/retrieval.py::StubRetriever` raises
 *   `RetrievalUnavailableError` before returning anything: W33's branch
 *   (`failed`, retryable, never Skipped). Used here only for the retry
 *   button's touch-target check — W33 itself is an integration case
 *   (PRD §7.1's own table), not a Playwright journey.
 * - `FORCE_NO_FINDINGS` — `services/stub_model.py`'s researcher dispatch
 *   reports `Findings(findings=[])` from documents this run genuinely,
 *   non-emptily retrieved, so the run reaches the novelty gate with nothing
 *   to admit and publishes **Skipped** — never the "zero documents" failed
 *   branch a stub that returned no documents at all would have proven
 *   instead (TDD §5.7, §11).
 */
export const FORCE_RETRIEVAL_FAILURE = "[force-retrieval-failure]";
export const FORCE_NO_FINDINGS = "[force-no-findings]";

/**
 * Fill in and submit the deploy-analyst form (`routes/beats.new.tsx`), without
 * waiting for the outcome — the `startPath` precedent (`fixtures/journey.ts`)
 * for a caller that wants to assert the form itself before it navigates away.
 */
export async function startBeat(
  page: Page,
  topic: string,
  opts: { level?: Level; anchorWeekday?: number; guidance?: string } = {},
): Promise<void> {
  await page.goto("/beats/new");
  await page.locator("#beat-topic").fill(topic);
  const level = opts.level ?? "new_to_it";
  await page.locator(`label[for="beat-level-${level}"]`).click();
  if (opts.anchorWeekday !== undefined) {
    await page.locator("#beat-anchor-weekday").selectOption(String(opts.anchorWeekday));
  }
  if (opts.guidance !== undefined) {
    await page.locator("#beat-guidance").fill(opts.guidance);
  }
  await page.getByRole("button", { name: "Deploy analyst" }).click();
}

/**
 * Deploy an analyst and wait for the hand-off to the Beat view. Returns the
 * new Beat's id.
 *
 * Waits plainly for the navigation (`POST /beats` is a single round trip that
 * either lands on `/beats/{id}` or leaves the form showing an error/rate-limit
 * notice — there is nothing here for a reload to rescue, `createPath`'s own
 * reasoning in `fixtures/journey.ts`), then for `standing-orders` — the one
 * thing the Beat view renders unconditionally the instant `detail` resolves,
 * whatever `research_state` the `202` response came back with (`researching`
 * in the ordinary case; TanStack Query's cache is seeded with that exact body
 * before the navigate, `routes/beats.new.tsx`'s own `onSuccess`, so this is
 * never a synthesized wait — it is the real first paint of the real response).
 */
export async function createBeat(
  page: Page,
  topic: string,
  opts: { level?: Level; anchorWeekday?: number; guidance?: string } = {},
): Promise<string> {
  await startBeat(page, topic, opts);
  await page.waitForURL(BEAT_URL_RE, { timeout: GENERATION_TIMEOUT });
  await waitForSurface(page, "standing-orders");
  return beatIdFromUrl(page.url());
}

/** The Beat id in a `/beats/{id}` URL. */
export function beatIdFromUrl(url: string): string {
  const match = BEAT_URL_RE.exec(url);
  if (match === null) {
    throw new Error(`not a Beat URL: ${url}`);
  }
  return match[1];
}

/** The Brief id in a `/briefs/{id}` URL. */
export function briefIdFromUrl(url: string): string {
  const match = BRIEF_URL_RE.exec(url);
  if (match === null) {
    throw new Error(`not a Brief URL: ${url}`);
  }
  return match[1];
}

/**
 * Wait for a Beat's rail to show a real, server-persisted entry of `kind` —
 * the "researching -> terminal" transition itself (see this module's own
 * header for why the reload-backed `waitForSurface` underneath is what makes
 * that transition genuine rather than assumed). Assumes the caller is already
 * on `/beats/{id}` (i.e. `createBeat` already ran).
 */
export async function waitForBeatEntry(
  page: Page,
  kind: "published" | "skipped" | "failed",
): Promise<void> {
  const testId = kind === "failed" ? "beat-failed" : `beat-rail-${kind}`;
  await waitForSurface(page, testId, GENERATION_TIMEOUT);
}

/** Every `brief-source` row's title link, in the order the Brief renders them. */
export function sourceLinks(page: Page): Locator {
  return page.getByTestId("brief-source").locator("a");
}

/** The `href` of every Source link on the open Brief, in rendered order. */
export async function sourceHrefs(page: Page): Promise<string[]> {
  const links = sourceLinks(page);
  const count = await links.count();
  const hrefs: string[] = [];
  for (let index = 0; index < count; index += 1) {
    hrefs.push((await links.nth(index).getAttribute("href")) ?? "");
  }
  return hrefs;
}

// --- The 390x844 viewport checks (AL-530 review carry-over, TDD §11) --------

const MIN_TOUCH_TARGET_PX = 44;

/**
 * No horizontal scroll at the phone viewport — a page whose content overflows
 * `documentElement`'s own width, forcing a learner to scroll sideways to read
 * it, fails PRD's mobile-first promise (CONTEXT.md: Mobile-first) as surely as
 * a broken layout would. `+1` absorbs sub-pixel rounding a real browser's
 * layout engine can introduce even on content that fits.
 */
export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflowPx = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth - doc.clientWidth;
  });
  expect(
    overflowPx,
    `page has ${overflowPx}px of horizontal overflow at the phone viewport`,
  ).toBeLessThanOrEqual(1);
}

/**
 * `locator`'s rendered box meets the >=44x44px touch-target minimum
 * (WCAG 2.5.5's own figure, and the one every Beats-surface comment this
 * ticket verifies already cites). Asserts visibility first so a `null`
 * bounding box reads as a clear failure message, never a silent skip.
 */
export async function expectMinTouchTarget(
  locator: Locator,
  min: number = MIN_TOUCH_TARGET_PX,
): Promise<void> {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  if (box === null) {
    throw new Error("expectMinTouchTarget: locator resolved but has no bounding box");
  }
  expect(box.width, "touch target width").toBeGreaterThanOrEqual(min);
  expect(box.height, "touch target height").toBeGreaterThanOrEqual(min);
}
