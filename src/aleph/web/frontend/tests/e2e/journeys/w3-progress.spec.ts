// W3 — Reach the north-star threshold (PRD §8).
//
// From a fresh path, complete lessons 1->4 in sequence, including the on-demand
// generation of the later ones: creation prefetches only a window ahead
// (`PREFETCH_N`, §14), so lesson 4 is generated because the learner walked to it,
// not because it was there from the start. Pass: four lessons complete, no dead
// ends, and the progress readouts — path view and switcher — agree.

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
} from "../fixtures/journey";

/** The north-star threshold: four lessons completed in one sitting (PRD §8). */
const TARGET = 4;

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W3 progress to the north-star threshold", { tag: "@w3" }, () => {
  test("four lessons complete in sequence and progress tracks every one", async ({ page }) => {
    const topic = uniqueTopic("Kubernetes scheduling");
    const pathId = await createPath(page, topic);

    const total = await railLessons(page).count();
    // The stub outline is 2-4 units of 3-4 lessons, so the threshold is always
    // reachable; assert it rather than silently testing fewer lessons.
    expect(total).toBeGreaterThanOrEqual(TARGET);

    for (let position = 0; position < TARGET; position += 1) {
      // Only the next lesson is open; the one after it is still locked. Walking
      // the rail in order is the whole point of the ordering invariant (§5.2).
      await expectRailState(page, position, "data-unlock-state", "available");

      // `completeLessonAt` waits for this lesson to finish generating, which for
      // the later positions is generation triggered by the learner's own walk.
      await completeLessonAt(page, position);

      // Post-completion, so asserted in place: these two are what go stale when
      // a completion fails to refresh the path surfaces, and a reload between
      // attempts would paper over exactly that (see `expectRailStateInPlace`).
      await expectRailStateInPlace(page, position, "data-unlock-state", "complete");
      await expectProgressInPlace(page.getByTestId("path-progress"), position + 1, total);
    }

    // Still no dead end: the fifth lesson (or the completion banner) is waiting —
    // unlocked by the same completion, and read off the same refreshed payload.
    if (total > TARGET) {
      await expectRailStateInPlace(page, TARGET, "data-unlock-state", "available");
    }

    // The switcher's roll-up reflects the same four completions.
    await gotoSwitcher(page);
    const row = pathRow(page, pathId);
    await expectProgress(page, row.getByTestId("path-item-progress"), TARGET, total);
    await expect(row.getByTestId("path-item-status")).toHaveText("In progress");
  });
});
