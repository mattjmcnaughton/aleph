// W23 — A streak survives a missed day boundary, but breaks on a missed day
// (PRD §4.4; Streaks TDD D11, §11).
//
// `domains/streaks.compute_streaks` has one asymmetry worth a journey of its
// own: "the streak does not break at midnight, it breaks when a whole day
// passes empty" (PRD §4.4). One day with no completion is still inside the
// grace the *server's* own definition grants ("or at today-1 if today has no
// completion yet"); two days empty is a real gap. Playwright cannot wait for
// an actual day to pass, so this spec drives the one clock seam that exists
// for exactly this (D11): `shiftCompletions` backdates a path's own
// completions by SQL, in the harness's stub backend only
// (`scripts/e2e_backend.py`) — production never sees the router at all
// (pinned by `tests/unit/test_smoke.py`).
//
// **The shared account makes the global line a delta test, not a literal
// one** (the same reasoning `w22.spec.ts`'s header states at length): every
// journey in this suite runs as one checked-in learner (docs/ci.md), and the
// Daily streak is an account-wide number. By the time this spec runs, other
// journeys have very likely already completed a lesson "today" — so this
// spec reads its own baseline off the wire before touching anything, and
// every claim on the *global* line afterwards is stated as a delta against
// that baseline rather than a hard-coded number. On a freshly migrated e2e
// database with nothing else run yet (the baseline is a true zero), the
// deltas collapse to exactly the literal PRD §3 copy the TDD names —
// asserted directly in that case, alongside the general form.
//
// The **per-path** numbers (`paths[]` in the summary) need none of that
// hedging: this path's id is minted fresh for this test and no other spec
// ever holds it, so its own row is exact and literal at every step,
// regardless of what anyone else's completions do to the account line. Both
// signals are checked at every step — the account line for what a learner
// actually sees, the per-path row for an unambiguous pin on the exact grace-
// day mechanics.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  completeLessonAt,
  createPath,
  fetchProgressSummary,
  gotoSwitcher,
  shiftCompletions,
  uniqueTopic,
  waitForSurface,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe(
  "W23 a streak survives a missed day boundary but breaks on a missed day",
  { tag: "@w23" },
  () => {
    test("a 1-day gap keeps the streak alive; a 3-day gap breaks it", async ({ page }) => {
      // The account's own state before this path exists at all — read once, off
      // the wire, and never assumed to be zero (see the header note).
      await gotoSwitcher(page);
      const base = await fetchProgressSummary(page);

      const pathId = await createPath(page, uniqueTopic("Salt flats"));
      await completeLessonAt(page, 0);

      // --- one day back: the grace day (PRD §4.4) ------------------------------
      await shiftCompletions(page, { pathId, days: 1 });
      await gotoSwitcher(page);
      await expect(page.getByTestId("streak-line")).toBeVisible();
      const afterOneDay = await fetchProgressSummary(page);

      // Residue-immune half of the claim: adding one active day (yesterday, on
      // a path that had none there before) can only hold or extend a streak,
      // never shrink it to the zero/invitation state — true however many other
      // completions this shared account already carries for today.
      expect(afterOneDay.current_streak).toBeGreaterThanOrEqual(1);
      await expect(page.getByTestId("streak-line")).not.toHaveText(
        "Complete a lesson to start a streak",
      );
      await expect(page.getByTestId("streak-line")).toContainText(
        `${afterOneDay.current_streak}-day streak`,
      );
      // On a clean account (nothing else run today or yesterday — the common
      // case straight after a migration, TDD §11: fresh Postgres per CI job),
      // this is exactly the literal PRD §3 copy: a 1-day streak with no
      // "· best N" clause (`best` cannot yet exceed `current`, TDD §14 R5) and
      // no "lesson(s) today" clause (nothing is dated today on this path any
      // more). `toContainText` above already covers the general case; this is
      // the stronger, exact pin for the common one.
      if (base.current_streak === 0 && base.best_streak <= 1 && base.completed_today === 0) {
        await expect(page.getByTestId("streak-line")).toHaveText("🔥 1-day streak");
      }
      // This path's own row cannot lie about its single completion, wherever
      // it is dated: no other spec ever holds this path's freshly minted id.
      const ownRowAfterOneDay = afterOneDay.paths.find((row) => row.path_id === pathId);
      expect(ownRowAfterOneDay).toMatchObject({
        current_streak: 1,
        best_streak: 1,
        completed_today: 0,
      });

      // --- three days back total: the grace day is spent -----------------------
      // Neither today nor yesterday carries a completion on this path any more,
      // and this is the only path this test has ever touched, so whatever it
      // was contributing to the account's streak is now entirely gone.
      await shiftCompletions(page, { pathId, days: 2 });
      await page.reload();
      await waitForSurface(page, "streak-line");
      const afterThreeDays = await fetchProgressSummary(page);

      // The residue-immune claim, stated exactly: removing this path's only
      // contribution restores the account to precisely its pre-test baseline
      // — whether that baseline was a true zero or another learner's completion
      // earlier the same day — because a day three back never chains into
      // "today"/"yesterday" the way one day back does.
      expect(afterThreeDays.current_streak).toBe(base.current_streak);
      if (base.current_streak === 0) {
        await expect(page.getByTestId("streak-line")).toHaveText(
          "Complete a lesson to start a streak",
        );
      } else {
        await expect(page.getByTestId("streak-line")).toContainText(
          `${base.current_streak}-day streak`,
        );
      }
      // And this path's own row shows the real break PRD §4.4 describes: with
      // neither today nor yesterday active for it, its current streak is 0 —
      // the one number the account line's residue can never obscure. `best`
      // survives at 1 (TDD §5.1: "`best` survives, `current` does not").
      const ownRowAfterThreeDays = afterThreeDays.paths.find((row) => row.path_id === pathId);
      expect(ownRowAfterThreeDays).toMatchObject({ current_streak: 0, best_streak: 1 });
    });
  },
);
