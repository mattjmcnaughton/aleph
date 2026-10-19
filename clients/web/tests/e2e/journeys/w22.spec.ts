// W22 — Completing a lesson visibly advances the streak (PRD §3; Streaks TDD
// D10, §11).
//
// Create a path, complete its first lesson, and watch the home streak line
// move — twice over: once **in the same interaction** (no reload, no network
// wait — the optimistic bump D10 patches into the cache), and once more after
// a full reload, which is what proves the server agrees rather than the UI
// having merely guessed well.
//
// **The shared account makes this a delta test, not a literal one.** Every
// journey in this suite runs as the one checked-in learner (docs/ci.md), and
// the Daily streak (unlike a path's own progress) is a property of the whole
// account — so by the time this spec runs, other journeys earlier in the same
// CI run have very likely already completed a lesson "today". D10's own text
// says the optimistic bump is a no-op on the day's second-and-later
// completion (pinned by `completion-refresh.test.tsx`), so which of the two
// branches this spec is actually exercising depends on the account's own
// `completed_today` at the moment it starts — read once, off the wire, and
// asserted against rather than assumed. Both branches are real product
// behaviour; this spec proves whichever one the account is actually in,
// which is what "residue from other specs is invisible to it" (docs/ci.md)
// means for a global counter rather than a per-path one.
//
// The "no network wait" half is not a timing hope: the summary request is
// held open with `page.route` (the same technique `w12.spec.ts` uses to prove
// an in-flight UI state), so the assertion can only pass if the bumped number
// was already sitting in the cache before that request was ever allowed to
// resolve.

import { type Page, expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  completeLessonAt,
  createPath,
  fetchProgressSummary,
  pathRow,
  uniqueTopic,
  waitForSurface,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

/**
 * Navigate home through the header's own logo link (`components/app-header.tsx`)
 * — a TanStack Router `Link`, resolved client-side. Deliberately not
 * `gotoSwitcher` (`fixtures/journey.ts`), which is a `page.goto` and therefore
 * remounts the whole app: a fresh `App()` mount makes a fresh `QueryClient`
 * (`app/app.tsx`), which would empty exactly the cache D10's optimistic patch
 * lives in. "The same interaction" requires staying in this tab's one
 * `QueryClient` the whole way through.
 */
async function goHomeInPlace(page: Page): Promise<void> {
  await page.getByRole("link", { name: "Aleph home" }).click();
  await expect(page.getByTestId("paths-list")).toBeVisible({ timeout: ACTION_TIMEOUT });
}

const SUMMARY_ROUTE = /\/api\/v1\/progress\/summary(\?.*)?$/;

test.describe("W22 completing a lesson visibly advances the streak", { tag: "@w22" }, () => {
  test("the streak line moves without a network wait, and a reload agrees", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Riverbank erosion"));

    // Warm the summary query in this tab first: D10's patch only ever touches
    // an *existing* cache entry (TDD §8 — "a no-op when the cache is cold"),
    // so home has to have been visited, in this `QueryClient`, before the
    // completion below. Read the account's starting point off the wire rather
    // than assuming it — see the header note on the shared account.
    await goHomeInPlace(page);
    await expect(page.getByTestId("streak-line")).toBeVisible();
    const before = await fetchProgressSummary(page);

    // --- complete the lesson, staying in this tab the whole time --------------
    await pathRow(page, pathId).getByTestId("path-item-open").click();
    await waitForSurface(page, "path-rail");

    if (before.completed_today === 0) {
      // This account's actual first completion of the day: the optimistic
      // bump fires. Hold the summary request open *before* the completion
      // even happens, so the invalidated refetch it triggers cannot possibly
      // land before the assertion below runs — the only way this can pass is
      // if the number the learner sees came from the cache, not the network.
      let release = (): void => {};
      const held = new Promise<void>((resolve) => {
        release = resolve;
      });
      await page.route(SUMMARY_ROUTE, async (route) => {
        await held;
        await route.continue();
      });

      await completeLessonAt(page, 0);
      await goHomeInPlace(page);

      // `toContainText`, not an exact match: on a long-lived local database
      // (`justfile`'s `just test-e2e` reuses `aleph_e2e` across runs, unlike
      // CI's fresh-per-job Postgres) a `best` from a previous day could still
      // exceed this bump and add a "· best N" clause (TDD §14 R5) — this spec
      // does not need to rule that out to prove the two numbers this branch is
      // actually about.
      await expect(page.getByTestId("streak-line")).toContainText(
        `🔥 ${before.current_streak + 1}-day streak`,
      );
      await expect(page.getByTestId("streak-line")).toContainText("1 lesson today");

      release();
      await page.unroute(SUMMARY_ROUTE);
    } else {
      // Some earlier journey in this run already claimed "today" for this
      // shared account (docs/ci.md), so D10's *other* half applies instead: a
      // no-op on the day's second-and-later completion (PRD §3 — a day
      // counter, not a lesson counter), pinned for real by
      // `completion-refresh.test.tsx`. There is nothing optimistic to observe
      // without a reload here, so this branch proves the number is
      // unchanged in place instead.
      await completeLessonAt(page, 0);
      await goHomeInPlace(page);
      await expect(page.getByTestId("streak-line")).toContainText(
        `${before.current_streak}-day streak`,
      );
    }

    // A single completion on a brand-new path is never a 2-day path streak.
    await expect(pathRow(page, pathId).getByTestId("streak-chip")).toHaveCount(0);

    // --- the reload: the server agrees either way ------------------------------
    await page.reload();
    await waitForSurface(page, "streak-line");
    const after = await fetchProgressSummary(page);
    expect(after.completed_today).toBeGreaterThan(before.completed_today);
    expect(after.current_streak).toBeGreaterThanOrEqual(before.current_streak);
    await expect(page.getByTestId("streak-line")).toContainText(
      `${after.current_streak}-day streak`,
    );
  });
});
