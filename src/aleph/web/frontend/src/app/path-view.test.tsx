import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PathUnit } from "../lib/api";
import {
  COMPLETE_PATH_UNITS,
  FRESH_PATH_UNITS,
  MID_PATH_UNITS,
  configurePaths,
  pathRenameRequestCount,
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
  it("[navigation] shows a breadcrumb back to Your paths", async () => {
    seedPath({ id: "p-crumb", topic: "Cell biology", level: "new_to_it" });
    await gotoPath("p-crumb");

    const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumb.textContent).toMatch(/Your paths.*Cell biology/);
    expect(screen.getByRole("link", { name: "Your paths" }).getAttribute("href")).toBe("/");
    expect(breadcrumb.querySelector('[aria-current="page"]')?.textContent).toBe("Cell biology");
  });

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

  it("renders the path's title, not the topic, in the h1", async () => {
    seedPath({
      id: "p-title",
      topic: "TypeScript",
      title: "TS from the ground up",
      level: "new_to_it",
      units: FRESH_PATH_UNITS,
    });
    await gotoPath("p-title");

    const heading = await screen.findByRole("heading", { name: /ts from the ground up/i });
    expect(heading.textContent).toBe("TS from the ground up");
    // The frozen generation input never leaks onto the h1.
    expect(screen.queryByText("TypeScript")).toBeNull();
  });

  it("renaming round-trips through the PATCH and updates the h1 + switcher row", async () => {
    seedPath({ id: "p-rename", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoPath("p-rename");

    await screen.findByRole("heading", { name: "TypeScript" });
    fireEvent.click(screen.getByRole("button", { name: /rename path/i }));

    const input = screen.getByRole("textbox", { name: /path title/i }) as HTMLInputElement;
    expect(input.value).toBe("TypeScript");
    fireEvent.change(input, { target: { value: "TypeScript from scratch" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    // The heading follows the rename.
    await screen.findByRole("heading", { name: "TypeScript from scratch" });
    expect(screen.queryByRole("textbox", { name: /path title/i })).toBeNull();

    // The sidebar switcher row (a different query, invalidated on success) follows.
    await screen.findByTestId("sidebar-path-item");
    expect(screen.getByTestId("sidebar-path-item").textContent).toContain(
      "TypeScript from scratch",
    );
  });

  it("Enter saves the rename", async () => {
    seedPath({ id: "p-enter", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoPath("p-enter");

    fireEvent.click(await screen.findByRole("button", { name: /rename path/i }));
    const input = screen.getByRole("textbox", { name: /path title/i });
    fireEvent.change(input, { target: { value: "Renamed via Enter" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await screen.findByRole("heading", { name: "Renamed via Enter" });
  });

  it("Escape cancels the rename without sending a request", async () => {
    seedPath({ id: "p-escape", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoPath("p-escape");

    fireEvent.click(await screen.findByRole("button", { name: /rename path/i }));
    const input = screen.getByRole("textbox", { name: /path title/i });
    fireEvent.change(input, { target: { value: "Abandoned edit" } });
    fireEvent.keyDown(input, { key: "Escape" });

    // Back to the plain heading, unchanged — and no PATCH was ever sent.
    await screen.findByRole("heading", { name: "TypeScript" });
    expect(screen.queryByRole("textbox", { name: /path title/i })).toBeNull();
    expect(pathRenameRequestCount()).toBe(0);
  });

  it("Cancel button discards the edit the same way Escape does", async () => {
    seedPath({ id: "p-cancel", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoPath("p-cancel");

    fireEvent.click(await screen.findByRole("button", { name: /rename path/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /path title/i }), {
      target: { value: "Abandoned edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    await screen.findByRole("heading", { name: "TypeScript" });
    expect(pathRenameRequestCount()).toBe(0);
  });

  it("Save is disabled while the trimmed value is empty", async () => {
    seedPath({ id: "p-blank", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoPath("p-blank");

    fireEvent.click(await screen.findByRole("button", { name: /rename path/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /path title/i }), {
      target: { value: "   " },
    });

    expect((screen.getByRole("button", { name: /^save$/i }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("F3: switching paths mid-rename does not carry the draft onto the new path", async () => {
    // `/paths/A` -> `/paths/B` via the sidebar switcher re-renders this route
    // rather than remounting it (the same hazard `use-shaping-rail.ts`'s
    // `currentPathRef` comment documents for the shaping rail) — so without
    // `key={detail.id}` on `PathTitle`, its `editing`/`draft` state would
    // survive the switch: the rename form would still be open, still holding
    // path A's typed draft, now sitting over path B.
    seedPath({ id: "p-key-a", topic: "Path A", level: "new_to_it", units: FRESH_PATH_UNITS });
    seedPath({ id: "p-key-b", topic: "Path B", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoPath("p-key-a");

    await screen.findByRole("heading", { name: "Path A" });
    fireEvent.click(screen.getByRole("button", { name: /rename path/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /path title/i }), {
      target: { value: "Renamed for A" },
    });

    const items = await screen.findAllByTestId("sidebar-path-item");
    const toB = items.find((el) => el.getAttribute("data-path-id") === "p-key-b");
    if (!toB) throw new Error("no sidebar-path-item for p-key-b");
    fireEvent.click(toB);

    // Path B's own heading renders plainly — the rename form did NOT follow.
    await screen.findByRole("heading", { name: "Path B" });
    expect(screen.queryByRole("textbox", { name: /path title/i })).toBeNull();
    expect(screen.queryByText("Renamed for A")).toBeNull();
    // And nothing was ever sent — there was no way left to press Save on it.
    expect(pathRenameRequestCount()).toBe(0);
  });

  it("a failed rename keeps the typed value open and surfaces an inline error", async () => {
    seedPath({
      id: "p-rename-fail",
      topic: "TypeScript",
      level: "new_to_it",
      units: FRESH_PATH_UNITS,
    });
    configurePaths({ renameFails: true });
    await gotoPath("p-rename-fail");

    fireEvent.click(await screen.findByRole("button", { name: /rename path/i }));
    const input = screen.getByRole("textbox", { name: /path title/i }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Won't stick" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await screen.findByRole("alert");
    // The input is still open, still carrying exactly what was typed — nothing lost.
    const stillOpen = screen.getByRole("textbox", { name: /path title/i }) as HTMLInputElement;
    expect(stillOpen.value).toBe("Won't stick");
    // The h1 never changed to the failed attempt.
    expect(screen.queryByRole("heading", { name: "Won't stick" })).toBeNull();
  });
});
