import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PathUnit } from "../lib/api";
import {
  COMPLETE_PATH_UNITS,
  FRESH_PATH_UNITS,
  MID_PATH_UNITS,
  configurePaths,
  seedPath,
} from "../mocks/paths";
import { App } from "./app";

// Path view (§5.4, TDD §8): the units/lessons rail renders every state from a
// single `GET /paths/{id}` payload — complete / available / locked — plus the
// header progress and the path-complete treatment. Driven end to end through the
// real router, TanStack Query, and MSW (the same seam onboarding tests use).
// Assertions use plain vitest matchers to mirror onboarding.test.tsx's style.

async function gotoPath(id: string): Promise<HTMLElement> {
  window.history.pushState({}, "", `/paths/${id}`);
  render(<App />);
  return screen.findByTestId("path-view");
}

function unlockStateOf(lessonId: string): string | null {
  return screen.getByTestId(`lesson-${lessonId}`).getAttribute("data-unlock-state");
}

// Flush a macrotask inside `act` so any async router navigation a click may have
// (wrongly) fired has a chance to commit before we assert it did NOT happen. A
// synchronous assert right after the click passes even when navigation fired, so
// the locked-inert test would be vacuous without this settle.
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("Path view — /paths/$pathId", () => {
  it("[AL-062] renders a fresh path: first lesson available, the rest locked", async () => {
    seedPath({ id: "p-fresh", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoPath("p-fresh");

    const [l1, l2, l3] = FRESH_PATH_UNITS[0].lessons;
    expect((await screen.findByTestId("path-progress")).textContent).toMatch(/0 of 3 lessons/i);
    expect(unlockStateOf(l1.id)).toBe("available");
    expect(unlockStateOf(l2.id)).toBe("locked");
    expect(unlockStateOf(l3.id)).toBe("locked");
    // Not complete → no completion treatment.
    expect(screen.queryByTestId("path-complete")).toBeNull();
  });

  it("[AL-062] renders a mid path: complete, available, and locked all at once", async () => {
    seedPath({ id: "p-mid", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    await gotoPath("p-mid");

    const flat = MID_PATH_UNITS.flatMap((u) => u.lessons);
    expect((await screen.findByTestId("path-progress")).textContent).toMatch(/2 of 4 lessons/i);
    expect(unlockStateOf(flat[0].id)).toBe("complete");
    expect(unlockStateOf(flat[1].id)).toBe("complete");
    expect(unlockStateOf(flat[2].id)).toBe("available");
    expect(unlockStateOf(flat[3].id)).toBe("locked");
    expect(screen.queryByTestId("path-complete")).toBeNull();
  });

  it("[AL-062] renders a complete path: every lesson complete + completion treatment", async () => {
    seedPath({
      id: "p-done",
      topic: "TypeScript",
      level: "work_in_it",
      units: COMPLETE_PATH_UNITS,
    });
    await gotoPath("p-done");

    expect((await screen.findByTestId("path-progress")).textContent).toMatch(/3 of 3 lessons/i);
    for (const lesson of COMPLETE_PATH_UNITS.flatMap((u) => u.lessons)) {
      expect(unlockStateOf(lesson.id)).toBe("complete");
    }
    // The path-complete treatment appears only when everything is done.
    await screen.findByTestId("path-complete");
  });

  it("[AL-062] a locked lesson is inert; the available lesson opens its own id", async () => {
    seedPath({ id: "p-fresh", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoPath("p-fresh");

    const [available, locked] = FRESH_PATH_UNITS[0].lessons;
    const lockedRow = await screen.findByTestId(`lesson-${locked.id}`);
    // Native `disabled` (not merely aria) is what makes the row inert and drops
    // it out of the tab order — assert the real property, not the ARIA mirror.
    expect((lockedRow as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(lockedRow);
    await settle();
    // Still on the path view: the locked click fired no navigation.
    expect(screen.queryByTestId("lesson-view")).toBeNull();
    screen.getByTestId("path-view");

    // The available lesson DOES navigate — and lands on *that* lesson's id,
    // exercising the id seam AL-063 keys on (data-testid="lesson-view-id").
    fireEvent.click(screen.getByTestId(`lesson-${available.id}`));
    expect((await screen.findByTestId("lesson-view-id")).textContent).toBe(available.id);
  });

  it("[AL-062] tapping the available lesson navigates to the lesson view", async () => {
    seedPath({ id: "p-fresh", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoPath("p-fresh");

    fireEvent.click(await screen.findByTestId(`lesson-${FRESH_PATH_UNITS[0].lessons[0].id}`));
    await screen.findByTestId("lesson-view");
  });

  it("[AL-062] tapping a complete lesson navigates too (revisit, §5.4)", async () => {
    seedPath({
      id: "p-done",
      topic: "TypeScript",
      level: "work_in_it",
      units: COMPLETE_PATH_UNITS,
    });
    await gotoPath("p-done");

    fireEvent.click(await screen.findByTestId(`lesson-${COMPLETE_PATH_UNITS[0].lessons[0].id}`));
    await screen.findByTestId("lesson-view");
  });

  it("[AL-062] a deep-linked refused path shows a graceful message, no rail", async () => {
    seedPath({ id: "p-refused", topic: "refuse-me", level: "new_to_it", resolution: "refused" });
    await gotoPath("p-refused");

    const refused = await screen.findByTestId("path-refused");
    expect(refused.getAttribute("data-variant")).toBe("refusal");
    // A refusal is not the rail and not the error surface.
    expect(screen.queryByTestId("path-rail")).toBeNull();
    expect(screen.queryByTestId("path-failed")).toBeNull();
  });

  it("[AL-062] a deep-linked failed path offers retry, not a dead-end", async () => {
    seedPath({ id: "p-failed", topic: "fail-me", level: "new_to_it", resolution: "failed" });
    await gotoPath("p-failed");

    const failed = await screen.findByTestId("path-failed");
    expect(failed.getAttribute("data-variant")).toBe("error");
    // One tap re-claims the outline (mock resolves it to ready) → the rail lands.
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await screen.findByTestId("path-rail");
  });

  it("[AL-062] a rate-limited retry surfaces the daily-cap notice, not silence", async () => {
    seedPath({ id: "p-failed", topic: "fail-me", level: "new_to_it", resolution: "failed" });
    configurePaths({ retryRateLimited: true });
    await gotoPath("p-failed");

    await screen.findByTestId("path-failed");
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await screen.findByTestId("path-retry-ratelimit");
    // Still on the failed surface with retry available — not a dead-end.
    screen.getByTestId("path-failed");
  });

  it("[AL-062] a retry that errors surfaces a generic retry error, not silence", async () => {
    seedPath({ id: "p-failed", topic: "fail-me", level: "new_to_it", resolution: "failed" });
    configurePaths({ retryFails: true });
    await gotoPath("p-failed");

    await screen.findByTestId("path-failed");
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await screen.findByTestId("path-retry-error");
    expect(screen.queryByTestId("path-retry-ratelimit")).toBeNull();
  });

  it("[AL-062] a deep-linked still-generating path shows the drafting state", async () => {
    seedPath({
      id: "p-drafting",
      topic: "TypeScript",
      level: "new_to_it",
      resolution: "ready",
      pollsRemaining: 3,
    });
    await gotoPath("p-drafting");

    await screen.findByTestId("path-generating");
    expect(screen.queryByTestId("path-rail")).toBeNull();
  });

  it("[AL-062] a deep link to a missing path shows the unavailable state, no rail", async () => {
    // Never seeded → the mock 404s. The poll must not loop forever on it.
    await gotoPath("p-missing-0000");

    // The query retries once (app default) before erroring, so allow for the
    // retry delay before the unavailable surface settles.
    await screen.findByTestId("path-unavailable", {}, { timeout: 3000 });
    expect(screen.queryByTestId("path-rail")).toBeNull();
  });

  it("[AL-062] a still-generating lesson shows the Preparing badge + generation state", async () => {
    const units: PathUnit[] = [
      {
        id: "u7000000-0000-4000-8000-000000000001",
        title: "Foundations & types",
        lessons: [
          {
            id: "l7000000-0000-4000-8000-000000000001",
            title: "What TypeScript adds",
            position_in_path: 0,
            generation_state: "generating",
            unlock_state: "available",
          },
        ],
      },
    ];
    seedPath({ id: "p-prep", topic: "TypeScript", level: "new_to_it", units });
    await gotoPath("p-prep");

    const row = await screen.findByTestId(`lesson-${units[0].lessons[0].id}`);
    expect(row.getAttribute("data-generation-state")).toBe("generating");
    expect(row.textContent).toMatch(/preparing/i);
  });
});
