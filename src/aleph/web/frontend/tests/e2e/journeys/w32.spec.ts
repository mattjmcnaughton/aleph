// W32 — A flow opens the next lesson on its own (flow TDD §5.3/§5.6/§5.7,
// §5.5). Behind the `flow` flag; `scripts/e2e_backend.py` turns it on for
// the plain e2e learner alongside every other launched flag.
//
// Two fresh paths, both with several lessons (`_build_outline`'s own floor:
// 2-4 units of 3-4 lessons each, well past the two a length-3 interleaved
// flow needs from the first path). `/flow/new`: pick 3, Select all, leave
// Interleave (the default), Start. Then three ordinary lesson completions —
// Quick check answered, Mark complete — except the door at the end of each
// is the flow's own advance card, not the usual three-way choice, and the
// tap through it is `flow-advance-go` rather than a link (§5.3's own rule:
// never wait out the 5-second count in a browser test that does not need to).
//
// **Structure, never model text** (§12's rule, restated in the plan's §7):
// every assertion below is a testid, a count, or a topic string this spec
// itself chose — never a sentence the stub model wrote.
//
// **Isolation.** The shared `aleph_e2e` account is not trusted to be clean:
// `Select all` is exercised (one click, proving the control itself works),
// but its result is then trimmed back to exactly this spec's own two paths —
// every other row it selected is clicked off again. So the flow's scope is
// always `{pathA, pathB}` regardless of whatever else is sitting `ready` in
// the account from an earlier spec in the same run; the receipt assertions
// below (`3` lessons, `2` paths, both topics in the ledger) hold no matter
// how much residue is present.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  answerQuickCheck,
  createPath,
  expectLessonContent,
  uniqueTopic,
  waitForSurface,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W32 a flow opens the next lesson on its own", { tag: "@w32" }, () => {
  test("three lessons across two paths, interleaved, land on the receipt", async ({ page }) => {
    const topicA = uniqueTopic("Flow alpha subject");
    const topicB = uniqueTopic("Flow beta subject");
    const pathA = await createPath(page, topicA);
    const pathB = await createPath(page, topicB);

    // The setup sheet (mock screen 02): 3 lessons, both paths, Interleave
    // (already the default — nothing to click for it).
    await page.goto("/flow/new");
    await waitForSurface(page, "flow-setup");
    await page.getByTestId("flow-length-3").click();
    await page.getByTestId("flow-select-all").click();
    // Trim the selection back to this spec's own two paths (see the
    // "Isolation" note above) — anything `Select all` also picked up from
    // the shared account's residue gets clicked off again.
    // Read the pressed ids once, then click each by its own stable testid.
    // Not `locator.all()`: that hands back index-based locators over the
    // live `[aria-pressed="true"]` selection, so the first deselect shrinks
    // the set and every later index waits out the test timeout.
    const pressedIds = await page
      .locator('[data-testid^="flow-path-"][aria-pressed="true"]')
      .evaluateAll((rows) => rows.map((row) => row.getAttribute("data-testid") ?? ""));
    for (const testid of pressedIds) {
      const id = testid.replace("flow-path-", "");
      if (id !== pathA && id !== pathB) await page.getByTestId(testid).click();
    }
    await expect(page.getByTestId(`flow-path-${pathA}`)).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId(`flow-path-${pathB}`)).toHaveAttribute("aria-pressed", "true");
    await page.getByTestId("flow-start").click();

    await waitForSurface(page, "lesson-read-passage");
    // Position-based (flow-fix plan item 3): the first lesson reads "1 of 3",
    // never "0 of 3".
    await expect(page.getByTestId("flow-bar")).toContainText("Flow · 1 of 3");

    // Two ordinary completions, each followed by the advance card's own
    // "go now" rather than the count (§5.3) or the usual completed-state door.
    for (let k = 1; k <= 2; k++) {
      await expectLessonContent(page);
      await answerQuickCheck(page, 0);
      await page.getByTestId("lesson-complete-button").click();
      await expect(page.getByTestId("flow-advance")).toBeVisible({ timeout: ACTION_TIMEOUT });
      // The bar already reflects this completion before the count would even
      // have started — "k of n" moves with Mark complete, not with the count.
      await expect(page.getByTestId("flow-bar")).toContainText(`Flow · ${k} of 3`);
      await page.getByTestId("flow-advance-go").click();
      await waitForSurface(page, "lesson-read-passage");
    }

    // The third (and last) completion reaches the flow's length: the advance
    // never renders for it at all — the route moves straight to the receipt
    // (§5.3 steps 3-4), so there is no "go now" to click here.
    await expectLessonContent(page);
    await answerQuickCheck(page, 0);
    await page.getByTestId("lesson-complete-button").click();

    await waitForSurface(page, "flow-receipt");
    await expect(page.getByTestId("flow-receipt-lessons")).toHaveText("3");
    await expect(page.getByTestId("flow-receipt-paths")).toHaveText("2");
    const ledger = page.getByTestId("flow-receipt-ledger");
    await expect(ledger).toContainText(topicA);
    await expect(ledger).toContainText(topicB);
  });
});
