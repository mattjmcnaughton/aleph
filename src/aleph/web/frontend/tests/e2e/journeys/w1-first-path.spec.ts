// W1 — New learner, first path, first lesson (PRD §8, "the magic moment").
//
// Sign in -> topic + level -> outline generates -> land on the path -> open
// lesson 1 -> Read passage -> Quick check -> answer -> Outcome + explanation ->
// mark complete -> lesson 2 is available.

import { expect, test } from "@playwright/test";
import { ADMIN_STORAGE_STATE, DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  GENERATION_TIMEOUT,
  answerQuickCheck,
  backToPath,
  expectLessonContent,
  expectProgress,
  expectProgressInPlace,
  expectRailState,
  expectRailStateInPlace,
  gotoSwitcher,
  markComplete,
  openLessonAt,
  pathIdFromUrl,
  pathRow,
  railLessons,
  startPath,
  uniqueTopic,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W1 first path", { tag: "@w1" }, () => {
  test("a new topic becomes a path whose first lesson can be completed", async ({ page }) => {
    const topic = uniqueTopic("Rust ownership");

    // --- topic + level -> generating -----------------------------------------
    await startPath(page, topic, "some_experience");
    // The learner never faces a blank screen while the model works: either the
    // loading state is up, or generation already resolved and the rail is. Both
    // are the same guarantee — and on a fast machine the stub can beat the
    // assertion to the second one, so race the two surfaces rather than pin the
    // transient one.
    await expect(
      page.getByTestId("onboarding-generating").or(page.getByTestId("path-rail")),
    ).toBeVisible();

    // --- outline appears -> the path view ------------------------------------
    await page.waitForURL(/\/paths\//, { timeout: GENERATION_TIMEOUT });
    const pathId = pathIdFromUrl(page.url());
    await expect(page.getByRole("heading", { level: 1, name: topic, exact: true })).toBeVisible();

    const rail = railLessons(page);
    await expect(rail.first()).toBeVisible();
    const totalLessons = await rail.count();
    expect(totalLessons).toBeGreaterThanOrEqual(2);

    // A real outline: units with lessons, the first one available and the rest
    // locked behind it (the ordering invariant, §5.2).
    await expectRailState(page, 0, "data-unlock-state", "available");
    await expectRailState(page, 1, "data-unlock-state", "locked");
    await expectProgress(page, page.getByTestId("path-progress"), 0, totalLessons);

    // --- the first lesson generates -> open it -------------------------------
    await openLessonAt(page, 0);
    // Real content renders: a non-empty Read passage and a 3-4 option Quick
    // check (structure, never wording — TDD §12).
    await expectLessonContent(page);

    // --- answer -> Outcome + explanation -------------------------------------
    const reveal = await answerQuickCheck(page, 0);
    // The keyed answer is marked and the explanation is shown, whichever branch
    // the Attempt landed on (the Outcome is formative, not gating).
    expect(reveal.correctIndex).toBeGreaterThanOrEqual(0);
    expect(reveal.explanation.length).toBeGreaterThan(0);

    // --- mark complete -> lesson 2 unlocks -----------------------------------
    await markComplete(page);
    await backToPath(page);

    // In place, on the page the learner is looking at: a reload would refresh a
    // stale rail by itself and hide the cache-invalidation regression these
    // three assertions exist to catch (see `expectRailStateInPlace`).
    await expectRailStateInPlace(page, 0, "data-unlock-state", "complete");
    await expectRailStateInPlace(page, 1, "data-unlock-state", "available");
    await expectProgressInPlace(page.getByTestId("path-progress"), 1, totalLessons);

    // The path is on the learner's switcher, carrying the level they chose.
    await gotoSwitcher(page);
    const row = pathRow(page, pathId);
    await expect(row).toBeVisible();
    await expect(row.getByTestId("path-item-topic")).toHaveText(topic);
    await expect(row.getByTestId("path-item-level")).toHaveText("Some experience");
  });
});

// The same first-path journey for the one learner who sees more of it: an admin
// pins the per-path model slots (§5.3/D14) on the way in. The allowlist the
// session serves is the stub id alone under e2e (scripts/e2e_backend.py), which
// is exactly the point — the picker offers what the server will accept, and the
// journey continues into real content either way.
test.describe("W1 first path with the admin model picker", { tag: "@w1" }, () => {
  test.use({ storageState: ADMIN_STORAGE_STATE });

  test("an admin pins both model slots and still lands on a real path", async ({ page }) => {
    // The same onboarding a learner drives, with one extra step on the way in.
    await startPath(page, uniqueTopic("Model routing"), "new_to_it", async () => {
      await expect(page.getByTestId("model-picker")).toBeVisible();
      await page.getByTestId("model-picker-outline").selectOption("stub");
      await page.getByTestId("model-picker-lesson").selectOption("stub");
    });

    await page.waitForURL(/\/paths\//, { timeout: GENERATION_TIMEOUT });
    await expect(page.getByTestId("path-rail")).toBeVisible();

    await openLessonAt(page, 0);
    await expectLessonContent(page);
  });
});

// A non-admin never sees the picker at all — the server decides who may pin a
// model, and the surface follows (an override from a non-admin is a 403).
test.describe("W1 the model picker is admin-only", { tag: "@w1" }, () => {
  test("a regular learner is offered no model slots", async ({ page }) => {
    await page.goto("/new");
    await expect(page.locator("#onboarding-topic")).toBeVisible();
    await expect(page.getByTestId("model-picker")).toHaveCount(0);
  });
});
