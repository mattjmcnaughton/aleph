import { render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession, type ProgressSummary } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { seedPath } from "../mocks/paths";
import { configureProgress, progressRequestCount, zeroActivity } from "../mocks/progress";
import { server } from "../mocks/server";
import { App } from "./app";

// The streak line + activity strip above "Your paths", and the per-path chip
// inside each row (PRD §3/§4.3, Streaks TDD §8). Driven end to end through the
// real router, TanStack Query and MSW — the same seam `paths-switcher.test.tsx`
// uses, since this surface lives on the exact same route.
//
// Two rules this file exists to pin, both from TDD §5.4/§8:
//  1. **No flag, no fetch.** `mocks/handlers.ts`'s default learner ships
//     `streaks: false` (like `tutor`), so every other test in the suite that
//     never touches this file proves the surface stays dark by construction.
//     This file is what proves it *explicitly*, and asserts the request never
//     even goes out (`skipToken`), not just that nothing renders.
//  2. **The streak line is decoration and must fail as decoration** (§5.4's
//     last row) — a failed `GET /progress/summary` must never prevent the
//     paths list, which is the product, from rendering.

const TODAY = "2026-08-02";

/** A learner with `streaks` on — the default fake session ships it dark. */
const streaksSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { streaks: true } },
};

function useStreaksSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(streaksSession)));
}

function summary(overrides: Partial<ProgressSummary> = {}): ProgressSummary {
  return {
    today: TODAY,
    current_streak: 5,
    best_streak: 5,
    completed_today: 1,
    activity: zeroActivity(TODAY),
    paths: [],
    ...overrides,
  };
}

async function gotoHome(): Promise<void> {
  window.history.pushState({}, "", "/");
  render(<App />);
  await screen.findByTestId("paths-switcher");
}

/** The row for one path id (`paths-switcher.test.tsx`'s `findItem`, sync here
 *  since every test awaits the streak line first, by which point the list —
 *  fetched alongside it, not after — has always already resolved). */
function itemFor(pathId: string): HTMLElement {
  const item = screen
    .getAllByTestId("path-list-item")
    .find((el) => el.getAttribute("data-path-id") === pathId);
  if (!item) throw new Error(`no list item for path ${pathId}`);
  return item;
}

describe("Streaks — the home surface (PRD §3/§4.3, TDD §8)", () => {
  it("[TDD §8] the flag off: no streak surface, and no request at all", async () => {
    // The default fake learner (`mocks/handlers.ts`) ships `streaks: false`.
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it" });
    await gotoHome();

    await screen.findByTestId("path-list-item");
    expect(screen.queryByTestId("streak-line")).toBeNull();
    expect(screen.queryByTestId("activity-strip")).toBeNull();
    expect(screen.queryByTestId("streak-chip")).toBeNull();
    // `skipToken`, not a fetch that happened to answer with nothing — the
    // component-level "no request" property, proved at the route.
    expect(progressRequestCount()).toBe(0);
  });

  it("[TDD §8] the flag on: the streak line and the strip render above the list", async () => {
    useStreaksSession();
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it" });
    configureProgress({ summary: summary() });
    await gotoHome();

    expect((await screen.findByTestId("streak-line")).textContent).toBe(
      "🔥 5-day streak · 1 lesson today",
    );
    expect(screen.getAllByTestId("activity-cell")).toHaveLength(45);
    expect(progressRequestCount()).toBe(1);
  });

  it("[D5/§8] a chip appears only for a path in `paths`, and only at ≥ 2 days", async () => {
    useStreaksSession();
    seedPath({ id: "p-streak", topic: "TypeScript", level: "new_to_it" });
    seedPath({ id: "p-fresh", topic: "Rust ownership", level: "new_to_it" });
    seedPath({ id: "p-one-day", topic: "Git internals", level: "new_to_it" });
    configureProgress({
      summary: summary({
        paths: [
          { path_id: "p-streak", current_streak: 3, best_streak: 3, completed_today: 1 },
          // Below the chip's own threshold (PRD §4.3) — present in the
          // payload, but the chip hides itself; this pins that the *route*
          // does not also filter it out redundantly.
          { path_id: "p-one-day", current_streak: 1, best_streak: 4, completed_today: 0 },
          // `p-fresh` has no row at all — absent means zero (D5).
        ],
      }),
    });
    await gotoHome();
    await screen.findByTestId("streak-line");

    const streakRow = within(itemFor("p-streak"));
    expect(streakRow.getByTestId("streak-chip").textContent).toBe("3-day");

    const oneDayRow = within(itemFor("p-one-day"));
    expect(oneDayRow.queryByTestId("streak-chip")).toBeNull();

    const freshRow = within(itemFor("p-fresh"));
    expect(freshRow.queryByTestId("streak-chip")).toBeNull();
  });

  it("[TDD §5.4] a failed summary query still renders the paths list", async () => {
    useStreaksSession();
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it" });
    configureProgress({ serverError: true });
    await gotoHome();

    // The product — the list — is unaffected by the decoration's failure.
    await screen.findByTestId("path-list-item");
    await waitFor(() => {
      expect(progressRequestCount()).toBeGreaterThan(0);
    });
    expect(screen.queryByTestId("streak-line")).toBeNull();
    expect(screen.queryByTestId("activity-strip")).toBeNull();
    expect(screen.queryByTestId("streak-chip")).toBeNull();
  });
});
