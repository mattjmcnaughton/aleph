// W31 — A quiet period is Skipped, not padded (PRD §3, §4.6, §7.1; TDD §11).
//
// The second of the two journeys PRD §7.1 keeps as browser journeys: with no
// novel findings, a research run publishes a **Skipped** entry — dated, one
// line, no body, no retry affordance, no error styling — instead of an
// uncited or padded Brief. CONTEXT.md: Skipped is "a first-class outcome the
// way Refused is for a path... a Skipped period is the feature working
// correctly", and this is the assertion that a quiet Beat reads that way to a
// learner, not as a failure.
//
// **The `[force-no-findings]` sentinel forces the researcher to report ZERO
// FINDINGS from documents that were genuinely, non-emptily retrieved — never
// zero documents.** That distinction is the whole point (TDD §5.7, §11):
// zero documents after `services/retrieval.py`'s filters is a **failed** run
// (`services/briefing.py`'s own load-bearing row — "we found nothing to
// read" is not "nothing happened"), and this test would prove nothing about
// the Skipped path if its stub took that shortcut. Here,
// `services/retrieval.py::StubRetriever` returns its ordinary real, dated
// documents (retrieval genuinely succeeds); `services/stub_model.py`'s
// researcher dispatch is what reports zero Findings from them, so the run
// reaches `domains/novelty.py::filter_new` with nothing to admit and the
// analyst publishes Skipped — never the failed branch.
//
// **A deliberate, recorded amendment to TDD §11's original text** (code
// review, ticket AL-560 follow-up — the TDD itself is corrected separately,
// ticket AL-561; this comment is what a reader of this file sees first).
// §11 originally described `[force-no-findings]` as forcing documents the
// novelty gate itself REJECTS — exercising the gate's rejection branch, not
// just its "nothing survived" one. That was judged arguably forced: a
// Beat's first-ever research run — the only run this spec (or W29) ever
// drives — has no prior Brief, so there are no earlier-cited URLs or claims
// for the gate to reject anything *against*. Genuinely exercising rejection
// needs a second run, which only W30 produces, and W30 is an integration
// case per PRD §7.1's own table, never a Playwright journey. So the shipped
// mechanism above — the researcher itself returning zero Findings — is what
// this suite actually tests, and it still reaches the same TDD §5.7 row
// (Skipped) through the same gate (`filter_new` on an empty findings list
// admits nothing, exactly as it would on a batch the gate rejected).
//
// **No live network** (`EXA_API_KEY` unset — see `w29.spec.ts`'s own header)
// and **the polling path is exercised for real**: this spec deploys through
// the real form and waits — through the Beat view's own live poll ALONE, no
// reload rescue (`fixtures/beats.ts::waitForBeatEntry`; see its own module
// header for why) — for the Beat's own rail to show a real,
// server-persisted Skipped row, exactly as `w29.spec.ts` waits for a
// published one.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  FORCE_NO_FINDINGS,
  createBeat,
  expectNoHorizontalOverflow,
  waitForBeatEntry,
} from "../fixtures/beats";
import { uniqueTopic } from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W31 a quiet period is Skipped, not padded", { tag: "@w31" }, () => {
  test("publishes a dated, one-line Skipped row with no body, retry, or error styling", async ({
    page,
  }) => {
    const topic = `${FORCE_NO_FINDINGS} ${uniqueTopic("a quiet subject")}`;
    await createBeat(page, topic);
    await waitForBeatEntry(page, "skipped");
    await expectNoHorizontalOverflow(page);

    // Exactly one Skipped row, and nothing published alongside it — a run
    // whose only findings were rejected produces a Skip, not a partial Brief.
    const skippedRow = page.getByTestId("beat-rail-skipped");
    await expect(skippedRow).toHaveCount(1);
    await expect(page.getByTestId("beat-rail-published")).toHaveCount(0);
    await expect(page.getByTestId("beat-rail-empty")).toHaveCount(0);

    // --- dated, one line ---------------------------------------------------------
    // `components/skipped-row.tsx` renders exactly two `<p>`s: the date, then
    // the skip line — checked by element count rather than splitting rendered
    // text on `\n`, since a real browser's `innerText` also breaks on a
    // soft-wrapped visual line, which a longer skip line legitimately is at
    // 390px and would otherwise make this assertion fragile for reasons that
    // have nothing to do with the product rule being tested.
    const rowParagraphs = skippedRow.locator("p");
    await expect(rowParagraphs).toHaveCount(2);
    const dateText = (await rowParagraphs.nth(0).innerText()).trim();
    const skipText = (await rowParagraphs.nth(1).innerText()).trim();
    expect(dateText.length).toBeGreaterThan(0);
    expect(skipText.length).toBeGreaterThan(0);

    // --- no body ----------------------------------------------------------------
    // A Skipped row is a plain `<li>`, never a link: there is nothing to open,
    // unlike a published row's whole-row `<Link>` (`beat-rail.tsx`).
    await expect(skippedRow.locator("a")).toHaveCount(0);

    // --- no retry affordance -----------------------------------------------------
    // Skipped is never conflated with failure (CONTEXT.md) — no button of any
    // kind inside the row, and no "Try again"/retry surface anywhere on the
    // page (the Beat view renders `FailedState` only for `research_state ===
    // "failed"`, which this run never reaches).
    await expect(skippedRow.locator("button")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
    await expect(page.getByTestId("beat-failed")).toHaveCount(0);

    // --- no error styling ---------------------------------------------------------
    // `skipped-row.tsx`'s own neutral tone (`border-divider bg-surface`) —
    // never the danger/iris treatment `beat-card.tsx`/`beats.$beatId.tsx` use
    // for a failed or refused Beat. A subtree scan (code-review FIX 8), not
    // the row's own `class` attribute alone and not a `data-variant` check:
    // `skipped-row.tsx` sets no `data-variant` at all, so
    // `not.toHaveAttribute("data-variant", "error")` passed trivially whether
    // or not the row was actually neutral — it could not tell "confirmed
    // neutral" from "no attribute here to check" — and a plain `/danger|iris/`
    // match against the row's own `class` misses the tone landing on a nested
    // element (e.g. a `<span className="text-danger">`) instead of the row.
    await expect(skippedRow.locator("[class*=danger], [class*=iris]")).toHaveCount(0);
  });
});
