// Shared vocabulary for the analyst journeys (W29, W31 — Phase 6 TDD §11,
// ticket AL-560): deploy a Beat, wait for its rail to reach a real terminal
// entry, and the phone-viewport (390x844) checks AL-530's own review left for
// this suite to prove — jsdom has no layout, so "no horizontal scroll" and
// ">=44px touch target" could not be verified there (`beats.new.tsx`,
// `beat-card.tsx`, `beat-rail.tsx`, `brief-sources.tsx`'s own comments already
// claim these hold "by construction"; this file is what actually checks it).
//
// Never reload to rescue a wait: research runs independently of the browser,
// so a reload can show the finished result even when polling is broken.
// Instead, hold the real stub retrieval until the browser observes Researching
// and its initial detail GET, then require a later poll to show the result.

import { type Locator, type Page, expect } from "@playwright/test";
import { BACKEND_URL } from "../servers";
import { ACTION_TIMEOUT, GENERATION_TIMEOUT, type Level } from "./journey";

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
 * Hold this topic's stub retrieval until the initial detail GET and pending UI
 * have both been observed. Without the gate, research can finish before even
 * the POST response or navigation, so observing Researching is a race. Waiting
 * for the initial GET also means that GET cannot supply the terminal result:
 * after release, `waitForBeatEntry` needs the application's polling to work.
 * No response is fabricated and no page reload rescues a broken poll.
 */
export async function createBeat(
  page: Page,
  topic: string,
  opts: { level?: Level; anchorWeekday?: number; guidance?: string } = {},
): Promise<string> {
  const held = await page.request.post(`${BACKEND_URL}/__e2e__/hold-research`, {
    data: { topic },
  });
  expect(held.ok()).toBe(true);
  try {
    const [initialDetail] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          /\/api\/v1\/beats\/[0-9a-f-]{36}(?:\?|$)/.test(response.url()),
        { timeout: GENERATION_TIMEOUT },
      ),
      startBeat(page, topic, opts),
    ]);
    expect(initialDetail.ok()).toBe(true);
    expect((await initialDetail.json()).research_state).toBe("researching");
    await page.waitForURL(BEAT_URL_RE, { timeout: GENERATION_TIMEOUT });
    await expect(page.getByTestId("beat-researching")).toBeVisible({ timeout: ACTION_TIMEOUT });
    return beatIdFromUrl(page.url());
  } finally {
    const released = await page.request.post(`${BACKEND_URL}/__e2e__/release-research`, {
      data: { topic },
    });
    expect(released.ok()).toBe(true);
  }
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
 * the "researching -> terminal" transition itself. Assumes the caller is
 * already on `/beats/{id}` (i.e. `createBeat` already ran).
 *
 * `createBeat` asserted Researching while retrieval was held. Now the real
 * terminal entry must arrive through polling alone, without a reload rescue.
 */
export async function waitForBeatEntry(
  page: Page,
  kind: "published" | "skipped" | "failed",
): Promise<void> {
  const testId = kind === "failed" ? "beat-failed" : `beat-rail-${kind}`;

  await expect(page.getByTestId(testId)).toBeVisible({ timeout: GENERATION_TIMEOUT });
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
