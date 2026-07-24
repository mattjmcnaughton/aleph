// W7 — Unsafe topic is refused gracefully (PRD §8, §10).
//
// A topic over the safety boundary comes back as a *refusal*, not an error: a
// clear, non-alarming message (iris, the refusal treatment — never the danger
// treatment), no partial path content anywhere, and an app that stays usable —
// the learner types a different topic and the journey continues.
//
// The boundary is forced deterministically with the `[force-refusal]` sentinel
// the stub model honours (TDD §12), so this journey never depends on a live
// model's safety judgement.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  GENERATION_TIMEOUT,
  createPath,
  gotoSwitcher,
  startPath,
  uniqueTopic,
  waitForSurface,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W7 refusal", { tag: "@w7" }, () => {
  test("an out-of-scope topic refuses gracefully and the app stays usable", async ({ page }) => {
    const topic = `[force-refusal] ${uniqueTopic("over the boundary")}`;

    await startPath(page, topic);

    // --- a refusal, not an error ---------------------------------------------
    const refused = page.getByTestId("onboarding-refused");
    await expect(refused).toBeVisible({ timeout: GENERATION_TIMEOUT });
    await expect(refused).toHaveAttribute("data-variant", "refusal");
    expect((await refused.innerText()).trim().length).toBeGreaterThan(0);

    // Nothing on this screen is an error surface, and no path content was built.
    await expect(page.getByTestId("onboarding-failed")).toHaveCount(0);
    await expect(page.getByTestId("onboarding-error")).toHaveCount(0);
    await expect(page.locator('[data-variant="error"]')).toHaveCount(0);
    await expect(page.getByTestId("path-rail")).toHaveCount(0);

    // --- the refused path reads as out-of-scope on the switcher too ----------
    await gotoSwitcher(page);
    const row = page.getByTestId("path-list-item").filter({ hasText: topic });
    await expect(row).toHaveAttribute("data-status", "refused");
    await expect(row).toHaveAttribute("data-variant", "refusal");
    await expect(row.getByTestId("path-item-status")).toHaveText("This topic is out of scope.");
    // A refused path has no lessons, so it carries no progress readout.
    await expect(row.getByTestId("path-item-progress")).toHaveCount(0);

    // Opening it shows the same graceful surface — never a rail of content.
    await row.getByTestId("path-item-open").click();
    await waitForSurface(page, "path-refused");
    await expect(page.getByTestId("path-refused")).toHaveAttribute("data-variant", "refusal");
    await expect(page.getByTestId("path-rail")).toHaveCount(0);

    // --- the app is still usable: a different topic builds a real path -------
    await page.goto("/new");
    await expect(page.locator("#onboarding-topic")).toBeVisible();
    await createPath(page, uniqueTopic("Mycology basics"));
    await expect(page.getByTestId("path-rail")).toBeVisible();
  });

  test("the refusal surface offers a way straight back to a different topic", async ({ page }) => {
    await startPath(page, `[force-refusal] ${uniqueTopic("also out of scope")}`);
    await expect(page.getByTestId("onboarding-refused")).toBeVisible({
      timeout: GENERATION_TIMEOUT,
    });

    await page.getByRole("button", { name: "Try a different topic" }).click();

    // Back on the form, with everything still working.
    await expect(page.locator("#onboarding-topic")).toBeVisible();
    await expect(page.getByTestId("onboarding-refused")).toHaveCount(0);
  });
});
