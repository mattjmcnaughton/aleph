// W4 — Multiple paths, independent progress (PRD §8).
//
// Create path A and complete a lesson, create path B, then switch back to A
// through the "Your paths" list: A's progress is intact, B's is untouched, and
// each path resumes at its own position.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  completeLessonAt,
  createPath,
  expectProgress,
  expectProgressInPlace,
  expectRailState,
  expectRailStateInPlace,
  gotoSwitcher,
  pathRow,
  railLessons,
  uniqueTopic,
  waitForSurface,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W4 multiple paths", { tag: "@w4" }, () => {
  test("two paths keep separate progress and switching preserves each position", async ({
    page,
  }) => {
    // --- path A, one lesson in ------------------------------------------------
    const topicA = uniqueTopic("Elixir processes");
    const pathA = await createPath(page, topicA);
    const totalA = await railLessons(page).count();
    await completeLessonAt(page, 0);
    await completeLessonAt(page, 1);

    // --- path B, untouched ----------------------------------------------------
    const topicB = uniqueTopic("Colour theory");
    const pathB = await createPath(page, topicB);
    const totalB = await railLessons(page).count();
    await expectProgress(page, page.getByTestId("path-progress"), 0, totalB);
    await expectRailState(page, 0, "data-unlock-state", "available");

    // --- the switcher shows both, each with its own roll-up -------------------
    await gotoSwitcher(page);
    const rowA = pathRow(page, pathA);
    const rowB = pathRow(page, pathB);
    await expect(rowA).toBeVisible();
    await expect(rowB).toBeVisible();
    await expect(rowA.getByTestId("path-item-topic")).toHaveText(topicA);
    await expect(rowB.getByTestId("path-item-topic")).toHaveText(topicB);
    await expectProgress(page, rowA.getByTestId("path-item-progress"), 2, totalA);
    await expectProgress(page, rowB.getByTestId("path-item-progress"), 0, totalB);
    await expect(rowA.getByTestId("path-item-status")).toHaveText("In progress");
    await expect(rowB.getByTestId("path-item-status")).toHaveText("Not started");

    // --- switch back to A: it resumes exactly where it was --------------------
    // Asserted in place: following the switcher's own link mounts the path view
    // on a payload fetched right then, so "A resumed where it was" has to hold
    // on the surface the tap produced — not after a reload the learner would
    // never perform. (Unlike W1/W3's closing beats this is not a cache-staleness
    // net: creating path B navigated the SPA afresh, emptying the query cache.)
    await rowA.getByTestId("path-item-open").click();
    await expect(page).toHaveURL(new RegExp(`/paths/${pathA}$`));
    await waitForSurface(page, "path-rail");
    await expectProgressInPlace(page.getByTestId("path-progress"), 2, totalA);
    await expectRailStateInPlace(page, 1, "data-unlock-state", "complete");
    await expectRailStateInPlace(page, 2, "data-unlock-state", "available");

    // --- and B is still at its own (untouched) position -----------------------
    await gotoSwitcher(page);
    await pathRow(page, pathB).getByTestId("path-item-open").click();
    await expect(page).toHaveURL(new RegExp(`/paths/${pathB}$`));
    await expectProgress(page, page.getByTestId("path-progress"), 0, totalB);
    await expectRailState(page, 0, "data-unlock-state", "available");
  });
});
