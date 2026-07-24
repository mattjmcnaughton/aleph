// W2 — Progress persists across sessions (PRD §8).
//
// Complete a lesson, sign out, sign back in from a clean slate, and find the
// path exactly as it was left — resuming at the right lesson. The point is that
// progress lives on the server, not in this browser: the session is torn down
// (app cookie *and* Keycloak's SSO cookie) so the second half of the journey
// re-enters credentials at the realm's login form, the way a learner returning
// days later does.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE, signIn } from "../fixtures/auth";
import {
  completeLessonAt,
  createPath,
  expectProgress,
  expectRailState,
  gotoPath,
  gotoSwitcher,
  pathRow,
  railLessons,
  uniqueTopic,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W2 resume across sessions", { tag: "@w2" }, () => {
  test("a completed lesson and the resume point survive signing out and back in", async ({
    page,
  }) => {
    const topic = uniqueTopic("Postgres indexes");
    const pathId = await createPath(page, topic);
    const total = await railLessons(page).count();

    await completeLessonAt(page, 0);
    await expectRailState(page, 0, "data-unlock-state", "complete");

    // --- leave -----------------------------------------------------------------
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "Sign in to Aleph" })).toBeVisible();

    // Drop every cookie, Keycloak's SSO session included, so signing back in is
    // a real credential round trip rather than a silent re-auth.
    await page.context().clearCookies();

    // --- come back -------------------------------------------------------------
    await signIn(page);

    // The switcher shows the path where it was left...
    await gotoSwitcher(page);
    const row = pathRow(page, pathId);
    await expect(row).toBeVisible();
    await expect(row.getByTestId("path-item-topic")).toHaveText(topic);
    await expectProgress(page, row.getByTestId("path-item-progress"), 1, total);

    // ...and so does the path itself: lesson 1 complete, lesson 2 the resume
    // point, with the rest still locked behind it.
    await gotoPath(page, pathId);
    await expectProgress(page, page.getByTestId("path-progress"), 1, total);
    await expectRailState(page, 0, "data-unlock-state", "complete");
    await expectRailState(page, 1, "data-unlock-state", "available");
    if (total > 2) {
      await expectRailState(page, 2, "data-unlock-state", "locked");
    }
  });
});
