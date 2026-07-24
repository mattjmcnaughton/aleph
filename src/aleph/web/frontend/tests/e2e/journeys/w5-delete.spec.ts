// W5 — Delete a path / reset (PRD §8).
//
// Deletion is destructive and not undoable, so it costs a second, deliberate
// tap: the inline confirm can be backed out of, and confirming removes only the
// path it names. The account is left clean — the other path is still switchable
// and a fresh path can still be created.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  completeLessonAt,
  createPath,
  expectProgress,
  gotoSwitcher,
  pathRow,
  railLessons,
  uniqueTopic,
  waitForSurface,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W5 delete a path", { tag: "@w5" }, () => {
  test("delete takes a confirm and removes only the path it names", async ({ page }) => {
    // Path A carries progress, so its deletion has something to take with it.
    const topicA = uniqueTopic("Bayesian priors");
    const pathA = await createPath(page, topicA);
    await completeLessonAt(page, 0);

    const topicB = uniqueTopic("Sourdough hydration");
    const pathB = await createPath(page, topicB);
    const totalB = await railLessons(page).count();

    await gotoSwitcher(page);
    const rowA = pathRow(page, pathA);
    const rowB = pathRow(page, pathB);
    await expect(rowA).toBeVisible();
    await expect(rowB).toBeVisible();

    // --- the confirm step is real: backing out leaves the path alone ----------
    await rowA.getByTestId("path-delete-button").click();
    await expect(rowA.getByTestId("path-delete-confirm")).toBeVisible();
    await rowA.getByTestId("path-delete-cancel").click();
    await expect(rowA.getByTestId("path-delete-confirm")).toHaveCount(0);
    await expect(rowA).toBeVisible();

    // --- confirming removes the target, and only the target ------------------
    await rowA.getByTestId("path-delete-button").click();
    await expect(rowA.getByTestId("path-delete-confirm")).toBeVisible();
    await rowA.getByTestId("path-delete-confirm").click();

    // On the action budget, not the default expect budget: confirming runs a
    // DELETE that cascades a whole path's units, lessons and attempts, and the
    // list is not updated optimistically — it removes the row and refetches, so
    // this is a full server round trip on a machine that may be loaded.
    await expect(rowA).toHaveCount(0, { timeout: ACTION_TIMEOUT });
    await expect(page.getByTestId("path-delete-error")).toHaveCount(0, {
      timeout: ACTION_TIMEOUT,
    });
    await expect(rowB).toBeVisible({ timeout: ACTION_TIMEOUT });
    await expect(rowB.getByTestId("path-item-topic")).toHaveText(topicB);
    await expectProgress(page, rowB.getByTestId("path-item-progress"), 0, totalB);

    // The path and its progress are gone server-side too, not just off the list.
    await page.goto(`/paths/${pathA}`);
    await waitForSurface(page, "path-unavailable");

    // --- the account is left in a clean state --------------------------------
    await gotoSwitcher(page);
    await expect(pathRow(page, pathB)).toBeVisible();
    await pathRow(page, pathB).getByTestId("path-item-open").click();
    await waitForSurface(page, "path-rail");

    const pathC = await createPath(page, uniqueTopic("Kalman filters"));
    await gotoSwitcher(page);
    await expect(pathRow(page, pathC)).toBeVisible();
    await expect(pathRow(page, pathB)).toBeVisible();
  });
});
