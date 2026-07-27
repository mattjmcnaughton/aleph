import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { seedLesson } from "../mocks/lessons";
import { COMPLETE_PATH_UNITS, FRESH_PATH_UNITS, MID_PATH_UNITS, seedPath } from "../mocks/paths";
import { App } from "./app";

// The desktop shell (Turn 2, docs/architecture.md — Frontend): the sidebar
// (Switcher + Outline sections) and the desktop-only continue card / lesson
// nav footer. All of it is CSS-only at Tailwind's `lg` breakpoint (1024px) —
// the markup exists in the DOM at every width, `hidden lg:…`/`lg:block`
// utilities are what decide whether a real browser paints it. jsdom has no
// CSS, so every element this file queries is present and assertable
// regardless — that is precisely what lets these tests exercise the desktop
// layout without a viewport to switch. Driven end to end through the real
// router, TanStack Query, and the MSW fakes, the same seam every other route
// suite uses.

async function gotoPath(id: string): Promise<HTMLElement> {
  window.history.pushState({}, "", `/paths/${id}`);
  render(<App />);
  return screen.findByTestId("path-view");
}

async function gotoLesson(id: string): Promise<HTMLElement> {
  window.history.pushState({}, "", `/lessons/${id}`);
  render(<App />);
  return screen.findByTestId("lesson-view");
}

// Both helpers `waitFor`: the sidebar's own queries (the paths list; the
// breadcrumb path detail an outline reads) are separate `useQuery` calls from
// whichever one the route awaited to resolve `gotoPath`/`gotoLesson`, so a row
// can still be one tick away the moment the main surface has settled.
async function sidebarPathItem(pathId: string): Promise<HTMLElement> {
  return waitFor(() => {
    const item = screen
      .getAllByTestId("sidebar-path-item")
      .find((el) => el.getAttribute("data-path-id") === pathId);
    if (!item) throw new Error(`no sidebar-path-item for path ${pathId}`);
    return item;
  });
}

async function outlineRow(lessonId: string): Promise<HTMLElement> {
  return waitFor(() => {
    const row = screen
      .getAllByTestId("sidebar-lesson-item")
      .find((el) => el.getAttribute("data-lesson-id") === lessonId);
    if (!row) throw new Error(`no sidebar-lesson-item for lesson ${lessonId}`);
    return row;
  });
}

describe("Desktop sidebar — Switcher section", () => {
  it("[Turn 2] lists every path with its progress badge, the open one marked current", async () => {
    seedPath({ id: "p-mid", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    seedPath({
      id: "p-fresh",
      topic: "Rust ownership",
      level: "new_to_it",
      units: FRESH_PATH_UNITS,
    });
    await gotoPath("p-mid");

    await screen.findByTestId("desktop-sidebar");

    const current = await sidebarPathItem("p-mid");
    expect(current.getAttribute("data-current")).toBe("true");
    expect(current.textContent).toMatch(/2\/4/);

    const other = await sidebarPathItem("p-fresh");
    expect(other.getAttribute("data-current")).toBeNull();
    expect(other.textContent).toMatch(/0\/3/);
    expect(screen.getAllByTestId("sidebar-path-item")).toHaveLength(2);
  });

  it("[Turn 2] clicking another path's row opens that path's own view", async () => {
    seedPath({ id: "p-mid", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    seedPath({
      id: "p-fresh",
      topic: "Rust ownership",
      level: "new_to_it",
      units: FRESH_PATH_UNITS,
    });
    await gotoPath("p-mid");

    fireEvent.click(await sidebarPathItem("p-fresh"));

    await screen.findByTestId("path-view");
    expect((await screen.findByTestId("path-progress")).textContent).toMatch(/0 of 3 lessons/i);
  });

  it("[Turn 2] the toggle collapses and restores the path list", async () => {
    seedPath({ id: "p-mid", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    await gotoPath("p-mid");

    const toggle = await screen.findByTestId("sidebar-switcher-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await sidebarPathItem("p-mid");
    expect(screen.getAllByTestId("sidebar-path-item")).toHaveLength(1);

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryAllByTestId("sidebar-path-item")).toHaveLength(0);

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByTestId("sidebar-path-item")).toHaveLength(1);
  });

  it("[Turn 2] the New path button routes to onboarding", async () => {
    seedPath({ id: "p-mid", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    await gotoPath("p-mid");

    fireEvent.click(await screen.findByTestId("sidebar-new-path"));

    expect(await screen.findByRole("heading", { name: /what do you want to learn/i })).toBeTruthy();
  });

  it("[Turn 2] the Switcher route ('/') has no sidebar — nothing is selected yet", async () => {
    seedPath({ id: "p-mid", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    window.history.pushState({}, "", "/");
    render(<App />);

    await screen.findByTestId("paths-switcher");
    expect(screen.queryByTestId("desktop-sidebar")).toBeNull();
  });
});

describe("Desktop sidebar — Outline section", () => {
  const PATH_ID = "p-outline";
  const [unit1, unit2] = MID_PATH_UNITS;
  const [complete1, complete2] = unit1.lessons;
  const [available, locked] = unit2.lessons;

  function seedOutlinePath(): void {
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    seedLesson({
      id: complete1.id,
      path_id: PATH_ID,
      title: complete1.title,
      unlock_state: "complete",
    });
    seedLesson({
      id: complete2.id,
      path_id: PATH_ID,
      title: complete2.title,
      unlock_state: "complete",
    });
    seedLesson({
      id: available.id,
      path_id: PATH_ID,
      title: available.title,
      unlock_state: "available",
    });
    seedLesson({
      id: locked.id,
      path_id: PATH_ID,
      title: locked.title,
      unlock_state: "locked",
      resolution: "ungenerated",
    });
  }

  it("[Turn 2] rows carry each lesson's unlock state, the open lesson is active, a locked row is not a link", async () => {
    seedOutlinePath();
    await gotoLesson(available.id);

    const completeRow = await outlineRow(complete1.id);
    expect(completeRow.getAttribute("data-unlock-state")).toBe("complete");
    expect(completeRow.tagName).toBe("A");

    const lockedRow = await outlineRow(locked.id);
    expect(lockedRow.getAttribute("data-unlock-state")).toBe("locked");
    expect(lockedRow.tagName).toBe("BUTTON");
    expect((lockedRow as HTMLButtonElement).disabled).toBe(true);

    expect((await outlineRow(available.id)).getAttribute("data-active")).toBe("true");
    expect(completeRow.getAttribute("data-active")).toBeNull();
  });

  it("[Turn 2] a complete sibling in the outline navigates there when clicked", async () => {
    seedOutlinePath();
    await gotoLesson(available.id);

    fireEvent.click(await outlineRow(complete1.id));

    await waitFor(() => {
      expect(screen.getByTestId("lesson-view-id").textContent).toBe(complete1.id);
    });
  });
});

describe("Desktop path view — continue card", () => {
  it("[Turn 2] names the available lesson, its unit, and its position; links to it", async () => {
    seedPath({ id: "p-mid", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    await gotoPath("p-mid");

    const [, unit2] = MID_PATH_UNITS;
    const [available] = unit2.lessons;

    const card = await screen.findByTestId("path-continue");
    expect(within(card).getByText(available.title)).toBeTruthy();
    expect(card.textContent).toMatch(/functions & narrowing/i);
    expect(card.textContent).toMatch(/lesson 3 of 4/i);
    expect(within(card).getByTestId("path-continue-link").getAttribute("href")).toBe(
      `/lessons/${available.id}`,
    );
    // Two lessons are already complete, so the resume framing is truthful.
    expect(card.getAttribute("data-started")).toBe("true");
    expect(card.textContent).toMatch(/pick up where you left off/i);
  });

  it("[Turn 2] opens with 'Start your path' while nothing is complete yet", async () => {
    seedPath({
      id: "p-fresh",
      topic: "Rust ownership",
      level: "new_to_it",
      units: FRESH_PATH_UNITS,
    });
    await gotoPath("p-fresh");

    // Nothing has been left off yet — the card still points at the first
    // lesson, but it must not claim a history the learner does not have.
    const card = await screen.findByTestId("path-continue");
    expect(card.getAttribute("data-started")).toBeNull();
    expect(card.textContent).toMatch(/start your path/i);
    expect(card.textContent).not.toMatch(/left off/i);
  });

  it("[Turn 2] is absent once the path is complete", async () => {
    seedPath({
      id: "p-done",
      topic: "Git internals",
      level: "work_in_it",
      units: COMPLETE_PATH_UNITS,
    });
    await gotoPath("p-done");

    await screen.findByTestId("path-complete");
    expect(screen.queryByTestId("path-continue")).toBeNull();
  });
});

describe("Desktop lesson view — prev/next footer", () => {
  it("[Turn 2] links to the previous lesson; the next is disabled while locked, a link once available", async () => {
    const PATH_ID = "p-nav";
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    const [unit1, unit2] = MID_PATH_UNITS;
    const primitiveTypes = unit1.lessons[1]; // position 1, complete
    const functionTypes = unit2.lessons[0]; // position 2, available (current)
    // `position_in_path` on the *lesson* payload must match the path's own
    // numbering (LessonNav finds neighbours by it) — the real backend
    // guarantees this by construction; the fake needs telling explicitly.
    seedLesson({
      id: primitiveTypes.id,
      path_id: PATH_ID,
      title: primitiveTypes.title,
      position_in_path: primitiveTypes.position_in_path,
      unlock_state: "complete",
    });
    seedLesson({
      id: functionTypes.id,
      path_id: PATH_ID,
      title: functionTypes.title,
      position_in_path: functionTypes.position_in_path,
      unlock_state: "available",
    });

    await gotoLesson(functionTypes.id);

    const nav = await screen.findByTestId("lesson-nav");
    const prevLink = within(nav).getByTestId("lesson-nav-prev");
    expect(prevLink.textContent).toMatch(new RegExp(primitiveTypes.title));
    expect(prevLink.getAttribute("href")).toBe(`/lessons/${primitiveTypes.id}`);

    // Narrowing (position 3) is locked, so the next lesson is a disabled button.
    const nextButton = within(nav).getByTestId("lesson-nav-next");
    expect(nextButton.tagName).toBe("BUTTON");
    expect((nextButton as HTMLButtonElement).disabled).toBe(true);

    // Walk back: Primitive types' own next (Function types) is available, so
    // its footer renders that neighbour as a real link instead.
    fireEvent.click(prevLink);
    await waitFor(() => {
      expect(screen.getByTestId("lesson-view-id").textContent).toBe(primitiveTypes.id);
    });
    const nextFromPrimitive = await screen.findByTestId("lesson-nav-next");
    expect(nextFromPrimitive.tagName).toBe("A");
    expect(nextFromPrimitive.getAttribute("href")).toBe(`/lessons/${functionTypes.id}`);
  });
});
