import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { COMPLETE_PATH_UNITS, FRESH_PATH_UNITS, MID_PATH_UNITS, seedPath } from "../mocks/paths";
import { App } from "./app";

// Home as a **workbench** rather than a shelf (design critique): resuming the
// path the learner was last working on is one tap and the first thing on the
// page, the list is ordered by what they last did rather than by what they
// last thought of, and a path with nothing left to do stops competing with the
// paths that do.
//
// Driven end to end through the real router, TanStack Query and the MSW paths
// fake — the same seam every other home test uses.

async function gotoHome(): Promise<HTMLElement> {
  window.history.pushState({}, "", "/");
  render(<App />);
  return screen.findByTestId("paths-switcher");
}

function itemIds(): (string | null)[] {
  return screen.getAllByTestId("path-list-item").map((el) => el.getAttribute("data-path-id"));
}

describe("Home — resuming work", () => {
  it("orders the list by last activity, not by when the path was created", async () => {
    // Seeded oldest-first, so creation order alone would put `p-new` on top.
    seedPath({
      id: "p-old",
      topic: "Rust ownership",
      level: "some_experience",
      units: MID_PATH_UNITS,
      lastActivityAt: "2026-08-12T09:00:00Z",
    });
    seedPath({
      id: "p-new",
      topic: "Kubernetes",
      level: "new_to_it",
      units: FRESH_PATH_UNITS,
    });
    await gotoHome();

    await screen.findByTestId("paths-list");
    // The older idea leads because it is the one being worked; the never-worked
    // path sorts into the nulls-last group behind it.
    expect(itemIds()).toEqual(["p-old", "p-new"]);
  });

  it("continues into the available lesson of the most recently worked path", async () => {
    seedPath({
      id: "p-worked",
      topic: "Rust ownership",
      level: "some_experience",
      units: MID_PATH_UNITS,
      lastActivityAt: "2026-08-12T09:00:00Z",
    });
    await gotoHome();

    const card = await screen.findByTestId("continue-card");
    expect(card.getAttribute("data-path-id")).toBe("p-worked");
    // `MID_PATH_UNITS` has lessons 1-2 complete, so the resume target is 3 —
    // the first incomplete one in `position_in_path` order, which is exactly
    // what `domains/progression` calls available.
    expect(screen.getByTestId("continue-card-lesson").textContent).toBe("Function types");
    expect(card.getAttribute("href")).toBe("/lessons/l2000000-0000-4000-8000-000000000003");
    // Worked before, so it is a continuation and says so.
    expect(card.textContent).toMatch(/continue/i);
  });

  it("reads 'Start' — not 'Continue' — for a path with no completions yet", async () => {
    seedPath({
      id: "p-fresh",
      topic: "Kubernetes",
      level: "new_to_it",
      units: FRESH_PATH_UNITS,
    });
    await gotoHome();

    const card = await screen.findByTestId("continue-card");
    expect(card.textContent).toMatch(/start/i);
    expect(card.textContent).not.toMatch(/continue/i);
  });

  it("offers nothing to resume when every path is finished", async () => {
    seedPath({
      id: "p-done",
      topic: "Git internals",
      level: "work_in_it",
      units: COMPLETE_PATH_UNITS,
    });
    await gotoHome();

    await screen.findByTestId("path-list-item");
    // A finished path has no available lesson, so there is nowhere to continue
    // to — and a dead card is worse than no card.
    expect(screen.queryByTestId("continue-card")).toBeNull();
  });

  it("sorts a finished path out of the working list into its own group", async () => {
    seedPath({
      id: "p-working",
      topic: "Rust ownership",
      level: "some_experience",
      units: MID_PATH_UNITS,
    });
    seedPath({
      id: "p-done",
      topic: "Git internals",
      level: "work_in_it",
      units: COMPLETE_PATH_UNITS,
    });
    await gotoHome();

    const list = await screen.findByTestId("paths-list");
    const finished = screen.getByTestId("finished-paths");

    expect(within_(list)).toEqual(["p-working"]);
    expect(within_(finished)).toEqual(["p-done"]);
  });

  it("drops the onboarding pitch once the learner has paths, keeps it when they don't", async () => {
    await gotoHome();
    // Nothing seeded: the empty state is genuinely someone's first screen.
    await screen.findByTestId("paths-empty");
    expect(screen.getByTestId("home-intro")).toBeTruthy();
  });

  it("hides the onboarding pitch from a returning learner", async () => {
    seedPath({ id: "p-one", topic: "Rust ownership", level: "some_experience" });
    await gotoHome();

    await screen.findByTestId("path-list-item");
    await waitFor(() => expect(screen.queryByTestId("home-intro")).toBeNull());
  });
});

/** The path ids of the rows inside one container, in document order. */
function within_(container: HTMLElement): (string | null)[] {
  return [...container.querySelectorAll('[data-testid="path-list-item"]')].map((el) =>
    el.getAttribute("data-path-id"),
  );
}
