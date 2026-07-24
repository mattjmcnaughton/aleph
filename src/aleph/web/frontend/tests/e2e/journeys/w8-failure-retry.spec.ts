// W8 — Generation failure is recoverable (PRD §8, §5.6).
//
// Both failure surfaces the learner can hit — an outline that never drafts, and
// a lesson that never writes — must be an error *with a retry*, never a dead
// spinner, and neither may disturb the paths and progress around it.
//
// Failures are forced deterministically with the stub model's sentinels (TDD
// §12): `[force-outline-failure]` and `[force-lesson-failure:N]`.
//
// **Why "retry then succeeds" is not asserted here.** The sentinel lives in the
// path's *topic*, and retry re-runs generation for that same stored topic — so
// the stub fails it again, by design and deterministically. What the browser can
// prove, and does below, is that the retry is offered, that taking it performs a
// real round trip and lands the learner back on a live surface rather than a
// spinner, and that the documented recovery — a different topic for the outline,
// the failed chain head for a lesson — keeps working.
//
// The other half of W8 is proven one layer down, where the failure can be made
// transient instead of topic-borne (`tests/integration/test_paths_api.py`):
// `test_retry_reclaims_failed_outline` pins the re-claim through a fresh claim
// stamp, and `test_retry_after_a_transient_failure_recovers_the_same_path`
// drives failed → retry → ready on one path, ending where a first-time success
// would have: lesson 1 open, with content. Neither is reachable from a browser
// against a stateless stub, and both are the same workflow this file opens.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  GENERATION_TIMEOUT,
  completeLessonAt,
  createPath,
  expectProgress,
  expectRailState,
  gotoPath,
  gotoSwitcher,
  pathIdFromUrl,
  pathRow,
  railLessons,
  startPath,
  uniqueTopic,
  waitForSurface,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W8 failure and retry", { tag: "@w8" }, () => {
  test("a failed outline offers a retry and never strands the learner", async ({ page }) => {
    // A healthy path with progress, to prove the failure leaves it alone (§5.6).
    const healthyTopic = uniqueTopic("Graph algorithms");
    const healthyPath = await createPath(page, healthyTopic);
    const healthyTotal = await railLessons(page).count();
    await completeLessonAt(page, 0);

    // --- the outline fails ---------------------------------------------------
    await startPath(page, `[force-outline-failure] ${uniqueTopic("doomed outline")}`);

    const failed = page.getByTestId("onboarding-failed");
    await expect(failed).toBeVisible({ timeout: GENERATION_TIMEOUT });
    // An error, not a refusal — the two surfaces are deliberately different.
    await expect(failed).toHaveAttribute("data-variant", "error");
    await expect(page.getByTestId("onboarding-refused")).toHaveCount(0);
    // Not a dead spinner: the retry is right there.
    const retry = page.getByRole("button", { name: "Try again" });
    await expect(retry).toBeEnabled();

    // --- retrying is a real round trip ---------------------------------------
    // The retry resets the poll, so the surface is torn down and rebuilt from a
    // fresh `GET`. It lands back on the failed surface (the sentinel is in the
    // stored topic, so the same generation fails the same way) — with a working
    // retry still offered, and no rate-limit or retry-error notice.
    await retry.click();
    await expect(page.getByTestId("onboarding-failed")).toBeVisible({
      timeout: GENERATION_TIMEOUT,
    });
    await expect(page.getByTestId("onboarding-retry-ratelimit")).toHaveCount(0);
    await expect(page.getByTestId("onboarding-retry-error")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Try again" })).toBeEnabled();

    // --- the documented recovery: a different topic --------------------------
    await page.getByRole("button", { name: "Edit topic" }).click();
    const recoveredTopic = uniqueTopic("Graph colouring");
    await page.locator("#onboarding-topic").fill(recoveredTopic);
    await page.getByRole("button", { name: "Build my path" }).click();
    await page.waitForURL(/\/paths\//, { timeout: GENERATION_TIMEOUT });
    const recoveredPath = pathIdFromUrl(page.url());
    await expect(page.getByTestId("path-rail")).toBeVisible();

    // --- the earlier path and its progress are untouched ---------------------
    await gotoSwitcher(page);
    const healthyRow = pathRow(page, healthyPath);
    await expectProgress(page, healthyRow.getByTestId("path-item-progress"), 1, healthyTotal);
    await expect(pathRow(page, recoveredPath)).toBeVisible();
    await gotoPath(page, healthyPath);
    await expectRailState(page, 0, "data-unlock-state", "complete");
  });

  test("a failed lesson offers a retry and leaves the rest of the path intact", async ({
    page,
  }) => {
    // Lesson 2 fails deterministically; lesson 1 generates normally.
    const topic = `[force-lesson-failure:2] ${uniqueTopic("broken chain")}`;
    const pathId = await createPath(page, topic);
    const total = await railLessons(page).count();

    // Lesson 1 is real content and can be completed, which unlocks lesson 2.
    await completeLessonAt(page, 0);
    await expectRailState(page, 1, "data-generation-state", "failed");
    await expectRailState(page, 1, "data-unlock-state", "available");

    // --- opening it is an error with a retry, not a spinner ------------------
    await railLessons(page).nth(1).click();
    const failed = page.getByTestId("lesson-failed");
    await waitForSurface(page, "lesson-failed");
    await expect(failed).toHaveAttribute("data-variant", "error");
    await expect(page.getByTestId("lesson-generating")).toHaveCount(0);
    await expect(page.getByTestId("lesson-generation-stalled")).toHaveCount(0);
    // No content leaked out of a failed generation.
    await expect(page.getByTestId("lesson-read-passage")).toHaveCount(0);
    await expect(page.getByTestId("quick-check")).toHaveCount(0);

    await expect(page.getByTestId("lesson-retry-button")).toBeEnabled();
    await page.getByTestId("lesson-retry-button").click();
    // The re-claimed lesson passes back through `generating`, and this view is
    // the one that stops polling (and freezes on the stall notice) if that takes
    // longer than 45s — so wait for the failed surface through a reload, which
    // is what the notice itself tells the learner to do.
    await waitForSurface(page, "lesson-failed");
    await expect(page.getByTestId("lesson-retry-ratelimit")).toHaveCount(0);
    await expect(page.getByTestId("lesson-retry-error")).toHaveCount(0);
    await expect(page.getByTestId("lesson-retry-button")).toBeEnabled();

    // --- the rest of the path is untouched -----------------------------------
    await gotoPath(page, pathId);
    await expectRailState(page, 0, "data-unlock-state", "complete");
    await expectProgress(page, page.getByTestId("path-progress"), 1, total);

    // The completed lesson still reads back in full.
    await railLessons(page).nth(0).click();
    await waitForSurface(page, "lesson-read-passage");
    await expect(page.getByTestId("lesson-completed")).toBeVisible();
  });
});
