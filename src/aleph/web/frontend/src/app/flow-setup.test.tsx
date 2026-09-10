import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { seedLesson } from "../mocks/lessons";
import { COMPLETE_PATH_UNITS, FRESH_PATH_UNITS, MID_PATH_UNITS, seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// `/flow/new`, the setup sheet (flow TDD §5.6, mock screen 02) — driven end to
// end through the real router, TanStack Query and the paths fake.

function useFlowSession(): void {
  const session: AuthSession = {
    authenticated: true,
    provider: "keycloak",
    user: { ...learnerUser, feature_flags: { flow: true } },
  };
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

const FRESH_ID = "p-fresh0-0000-4000-8000-000000000001";
const MID_ID = "p-mid000-0000-4000-8000-000000000001";
const DONE_ID = "p-done00-0000-4000-8000-000000000001";

/** Two live paths (3 and 2 lessons left) plus one finished (0 left). */
function seedThreePaths(): void {
  seedPath({ id: FRESH_ID, topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
  seedPath({
    id: MID_ID,
    topic: "SQL performance",
    level: "some_experience",
    units: MID_PATH_UNITS,
  });
  seedPath({
    id: DONE_ID,
    topic: "Git internals",
    level: "work_in_it",
    units: COMPLETE_PATH_UNITS,
  });
  // Every lesson these paths can open must exist in the lessons fake too, or
  // starting a flow would navigate onto a 404 lesson view.
  for (const [pathId, units] of [
    [FRESH_ID, FRESH_PATH_UNITS],
    [MID_ID, MID_PATH_UNITS],
  ] as const) {
    for (const unit of units) {
      for (const lesson of unit.lessons) {
        seedLesson({
          id: lesson.id,
          path_id: pathId,
          position_in_path: lesson.position_in_path,
          correctIndex: 0,
        });
      }
    }
  }
}

async function gotoSetup(search = ""): Promise<void> {
  window.history.pushState({}, "", `/flow/new${search}`);
  render(<App />);
  await screen.findByTestId("flow-setup");
}

/**
 * Force one path's pick to a known pressed state, regardless of whichever
 * path the default-selection effect landed on (`pickResumeTarget` — MID here,
 * since it is the only path with a completion and therefore `last_activity_at`
 * set). Tests that care about an exact scope call this rather than assume
 * which path started selected.
 */
function ensureSelected(pathId: string, want: boolean): void {
  const pick = screen.getByTestId(`flow-path-${pathId}`);
  if ((pick.getAttribute("aria-pressed") === "true") !== want) {
    fireEvent.click(pick);
  }
}

describe("Flow setup — /flow/new", () => {
  it("Start reads 'Pick a path to start' and disables with nothing selected", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup();
    await screen.findByTestId(`flow-path-${FRESH_ID}`);

    // Default selection lands on the resume target (MID) — deselect it to
    // reach the true empty-selection state.
    ensureSelected(MID_ID, false);

    const start = screen.getByTestId("flow-start") as HTMLButtonElement;
    expect(start.textContent).toBe("Pick a path to start");
    expect(start.disabled).toBe(true);
  });

  it("names the exact lesson count, capped at what's left, with the cap suffix", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup();
    await screen.findByTestId(`flow-path-${FRESH_ID}`);
    // Pin the scope to FRESH alone (3 left) regardless of the default pick.
    ensureSelected(MID_ID, false);
    ensureSelected(FRESH_ID, true);

    // FRESH has 3 left; 3 is under the 5-lesson default -> no cap.
    fireEvent.click(screen.getByTestId("flow-length-3"));
    await waitFor(() =>
      expect(screen.getByTestId("flow-start").textContent).toBe("Start flow · 3 lessons"),
    );

    // 8 requested against 3 available -> capped, with the suffix.
    fireEvent.click(screen.getByTestId("flow-length-8"));
    await waitFor(() =>
      expect(screen.getByTestId("flow-start").textContent).toBe(
        "Start flow · 3 lessons (all that's left)",
      ),
    );
  });

  it("'Until I stop' reads with no number at all", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup();
    await screen.findByTestId(`flow-path-${FRESH_ID}`);

    fireEvent.click(screen.getByTestId("flow-length-open"));
    expect(screen.getByTestId("flow-start").textContent).toBe("Start flow · until I stop");
  });

  it("sums 'N left' across the selected paths only, not every path", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup();
    await screen.findByTestId(`flow-path-${FRESH_ID}`);
    // FRESH (3 left) + MID (2 left) selected together -> 5, capped at 5.
    ensureSelected(MID_ID, true);
    ensureSelected(FRESH_ID, true);

    fireEvent.click(screen.getByTestId("flow-length-8"));
    await waitFor(() =>
      expect(screen.getByTestId("flow-start").textContent).toBe(
        "Start flow · 5 lessons (all that's left)",
      ),
    );
  });

  it("the finished path is listed, disabled, and excluded from Select all", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup();
    const donePick = (await screen.findByTestId(`flow-path-${DONE_ID}`)) as HTMLButtonElement;

    expect(donePick.disabled).toBe(true);
    expect(donePick.textContent).toMatch(/finished/i);

    fireEvent.click(screen.getByTestId("flow-select-all"));
    await waitFor(() => {
      expect(screen.getByTestId(`flow-path-${FRESH_ID}`).getAttribute("aria-pressed")).toBe("true");
      expect(screen.getByTestId(`flow-path-${MID_ID}`).getAttribute("aria-pressed")).toBe("true");
    });
    // Never selected — a disabled control has no aria-pressed toggle at all.
    expect(donePick.getAttribute("aria-pressed")).toBe("false");
  });

  it("Select all flips to Clear once both live paths are selected, and back", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup();
    await screen.findByTestId(`flow-path-${FRESH_ID}`);

    const selectAll = screen.getByTestId("flow-select-all");
    expect(selectAll.textContent).toBe("Select all");

    fireEvent.click(selectAll);
    await waitFor(() => expect(screen.getByTestId("flow-select-all").textContent).toBe("Clear"));

    fireEvent.click(screen.getByTestId("flow-select-all"));
    await waitFor(() =>
      expect(screen.getByTestId("flow-select-all").textContent).toBe("Select all"),
    );
  });

  it("the order control appears only once two paths are selected", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup();
    await screen.findByTestId(`flow-path-${FRESH_ID}`);

    // One path selected by default (the resume target) — order is hidden.
    expect(screen.queryByTestId("flow-order-interleave")).toBeNull();

    fireEvent.click(screen.getByTestId("flow-select-all"));
    await waitFor(() => expect(screen.getByTestId("flow-order-interleave")).toBeTruthy());
    expect(screen.getByTestId("flow-order-interleave").getAttribute("aria-pressed")).toBe("true");
  });

  it("Start writes the record and lands on the first lesson's route", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup();
    await screen.findByTestId(`flow-path-${FRESH_ID}`);
    ensureSelected(MID_ID, false);
    ensureSelected(FRESH_ID, true);

    fireEvent.click(screen.getByTestId("flow-start"));

    const idEl = await screen.findByTestId("lesson-view-id");
    // FRESH is the only scoped path, so its available lesson opens.
    expect(idEl.textContent).toBe(FRESH_PATH_UNITS[0].lessons[0].id);
  });

  it("preselects the path named by ?path= when it is eligible", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup(`?path=${MID_ID}`);

    const midPick = await screen.findByTestId(`flow-path-${MID_ID}`);
    await waitFor(() => expect(midPick.getAttribute("aria-pressed")).toBe("true"));
    expect(screen.getByTestId(`flow-path-${FRESH_ID}`).getAttribute("aria-pressed")).toBe("false");
  });

  it("Not now links back home", async () => {
    useFlowSession();
    seedThreePaths();
    await gotoSetup();
    await screen.findByTestId(`flow-path-${FRESH_ID}`);

    expect(screen.getByTestId("flow-not-now").getAttribute("href")).toBe("/");
  });

  it("shows the empty state, no picks, when no path is flowable", async () => {
    useFlowSession();
    seedPath({
      id: DONE_ID,
      topic: "Git internals",
      level: "work_in_it",
      units: COMPLETE_PATH_UNITS,
    });
    await gotoSetup();

    await screen.findByTestId("flow-new-empty");
    expect(screen.queryByTestId(`flow-path-${DONE_ID}`)).toBeNull();
  });

  it("renders the unavailable dead-end when the flag is off", async () => {
    // Default learner session ships every flag off — no `flow-setup` shell at
    // all in this branch, so this bypasses `gotoSetup`'s wait for it.
    window.history.pushState({}, "", "/flow/new");
    render(<App />);
    await screen.findByTestId("flow-new-unavailable");
  });
});
