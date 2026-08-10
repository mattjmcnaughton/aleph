// W29 — Deploy an analyst, get a cited Brief (PRD §7.1, §5, TDD §11).
//
// The first of the two journeys PRD §7.1 keeps as browser journeys: create a
// Beat, its first Brief is researched **immediately** (PRD §3 — "researched
// immediately, not at the first Anchor day", D4) rather than waiting for the
// Anchor day, and the published Brief's Sources are real, resolving citations
// (PRD §4.4) — never an uncited essay from model priors.
//
// **No live network.** The harness's stub backend (`scripts/e2e_backend.py`)
// wires `services/retrieval.py::StubRetriever` into `services/briefing.py`'s
// `briefing_service` singleton in place of the (unbuilt-in-production)
// `ExaRetriever`, so this suite runs with `EXA_API_KEY` unset — the same
// posture Phase 1's stub model already gives every other generation surface.
//
// **The polling path is exercised for real, not a pre-seeded row.** This spec
// never injects a Brief into the database or mocks the API — it drives the
// real deploy form, reads the real `202` response (already `research_state:
// "researching"`, the claim already committed server-side before the response
// is built, `routers/v1/beats.py`'s own module doc), waits through the real
// `Researching…` state, and then waits — through the Beat view's own live
// poll ALONE, with no reload rescue (`fixtures/beats.ts::waitForBeatEntry`;
// see its own module header for why a reload-backed rescue here would defeat
// the point) — for the SAME Beat's rail to show a real, server-persisted,
// published entry. Three separate defects in this phase were "the client
// cannot see the run its own request started"; a bare, non-reloading wait is
// what catches a fourth — it times out and fails on exactly that class of
// bug, rather than a reload quietly finding the server's already-completed
// state and passing anyway.
//
// **Also covers the AL-530 review carry-over**: "390x844 phone viewport: no
// horizontal scroll, tap targets >= 44px" could not be verified in jsdom (no
// layout engine) and is checked here, in a real browser, for every Beats
// surface a deploy-and-read journey naturally visits — home, the deploy form,
// the Beat rail, and the Brief reading surface. The one target this journey's
// own success path never renders — the Beat view's retry button — gets its
// own small test below, using `[force-retrieval-failure]` (W33's own
// sentinel; W33 itself is an integration case, PRD §7.1's table, not a
// Playwright journey) purely as a way to reach that surface.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  FORCE_RETRIEVAL_FAILURE,
  createBeat,
  expectMinTouchTarget,
  expectNoHorizontalOverflow,
  sourceHrefs,
  sourceLinks,
  waitForBeatEntry,
} from "../fixtures/beats";
import { GENERATION_TIMEOUT, uniqueTopic } from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

// Every Source `StubRetriever` invents resolves to this shape
// (`services/retrieval.py::_build_stub_document`) — asserting the pattern,
// never a literal URL, is this suite's own "structure, never text" rule
// (`fixtures/journey.ts`'s header) applied to a citation instead of a passage.
const STUB_SOURCE_URL_RE = /^https:\/\/example\.com\/stub-source\/\d+$/;

test.describe("W29 deploy an analyst, get a cited Brief", { tag: "@w29" }, () => {
  test("researches immediately and the Brief cites real, resolving sources", async ({ page }) => {
    // --- home: the entry point, before anything is deployed ------------------
    await page.goto("/");
    await expect(page.getByTestId("beats-section")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expectMinTouchTarget(page.getByTestId("deploy-analyst-button"));

    // --- the deploy form -------------------------------------------------------
    await page.goto("/beats/new");
    await expectNoHorizontalOverflow(page);
    await expectMinTouchTarget(page.locator("#beat-topic"));
    for (const level of ["new_to_it", "some_experience", "work_in_it"] as const) {
      await expectMinTouchTarget(page.locator(`label[for="beat-level-${level}"]`));
    }
    await expectMinTouchTarget(page.locator("#beat-anchor-weekday"));
    await expectMinTouchTarget(page.locator("#beat-guidance"));
    await expectMinTouchTarget(page.getByRole("button", { name: "Deploy analyst" }));

    // --- deploy, and wait through a REAL researching -> terminal transition --
    const topic = uniqueTopic("EU AI regulation");
    const beatId = await createBeat(page, topic);
    await waitForBeatEntry(page, "published");
    await expectNoHorizontalOverflow(page);

    const publishedRow = page.getByTestId("beat-rail-published");
    await expect(publishedRow).toHaveCount(1);
    // Nothing else on the rail: one deploy, one immediate run, one Brief.
    await expect(page.getByTestId("beat-rail-skipped")).toHaveCount(0);
    await expectMinTouchTarget(publishedRow.locator("a"));

    // --- open the Brief and read the Sources block ----------------------------
    await publishedRow.locator("a").click();
    await page.waitForURL(/\/briefs\//, { timeout: GENERATION_TIMEOUT });
    await expect(page.getByTestId("brief-body")).toBeVisible();
    await expectNoHorizontalOverflow(page);

    const sources = page.getByTestId("brief-sources");
    await expect(sources).toBeVisible();
    const links = sourceLinks(page);
    const sourceCount = await links.count();
    expect(sourceCount).toBeGreaterThan(0);

    const hrefs = await sourceHrefs(page);
    for (const href of hrefs) {
      expect(href).toMatch(STUB_SOURCE_URL_RE);
    }
    await expectMinTouchTarget(links.first());

    // Brief #1 on a fresh Beat: no earlier Brief to build on (PRD §3's
    // continuity line renders only from Brief #2 on — `builds-on-line.tsx`).
    // (Also out of e2e's reach on purpose: W30, which alone would produce a
    // second Brief to link to, is an integration case — PRD §7.1's table.)
    await expect(page.getByTestId("builds-on-line")).toHaveCount(0);

    // --- revisit home: a real Beat card now exists (code-review FIX 9) -------
    // The home visit at the top of this test happens BEFORE anything is
    // deployed, so on a fresh account the Beats section is empty there and no
    // card is ever measured — despite this file's own header naming
    // `beat-card.tsx` among the components it "actually checks". Revisiting
    // home now, with this Beat published, is what actually measures one.
    await page.goto("/");
    await expectMinTouchTarget(
      page.locator(`[data-testid="beat-list-item"][data-beat-id="${beatId}"]`),
    );
  });

  test("a failed research run's retry button meets the phone touch target", async ({ page }) => {
    const topic = `${FORCE_RETRIEVAL_FAILURE} ${uniqueTopic("failed analyst run")}`;
    await createBeat(page, topic);
    await waitForBeatEntry(page, "failed");
    await expectNoHorizontalOverflow(page);

    const failed = page.getByTestId("beat-failed");
    await expect(failed).toHaveAttribute("data-variant", "error");
    await expectMinTouchTarget(page.getByRole("button", { name: "Try again" }));
  });
});
