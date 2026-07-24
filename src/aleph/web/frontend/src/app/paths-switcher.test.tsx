import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  COMPLETE_PATH_UNITS,
  FRESH_PATH_UNITS,
  MID_PATH_UNITS,
  configurePaths,
  deletedPathIds,
  forgetPath,
  pathsListRequestCount,
  seedPath,
} from "../mocks/paths";
import { App } from "./app";

// "Your paths" switcher (§5.5, TDD §8) — the signed-in home route. It lists the
// learner's paths from a single `GET /paths` payload (topic, level, status,
// progress), routes to /new for another path, and deletes one behind an inline
// confirm (W5). W4 lives here too: each row carries its own progress and links
// to its own path view, so switching never crosses two paths' positions.
//
// Driven end to end through the real router, TanStack Query, and the MSW paths
// fake — the same seam the onboarding/path-view/lesson-view tests use.

async function gotoHome(): Promise<HTMLElement> {
  window.history.pushState({}, "", "/");
  render(<App />);
  return screen.findByTestId("paths-switcher");
}

async function findItem(pathId: string): Promise<HTMLElement> {
  return waitFor(() => {
    const item = screen
      .getAllByTestId("path-list-item")
      .find((el) => el.getAttribute("data-path-id") === pathId);
    if (!item) throw new Error(`no list item for path ${pathId}`);
    return item;
  });
}

function itemIds(): (string | null)[] {
  return screen.getAllByTestId("path-list-item").map((el) => el.getAttribute("data-path-id"));
}

describe("Your paths switcher — /", () => {
  it("[AL-064] lists every path with topic, level, status and progress", async () => {
    seedPath({ id: "p-mid", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    seedPath({
      id: "p-done",
      topic: "Git internals",
      level: "work_in_it",
      units: COMPLETE_PATH_UNITS,
    });
    await gotoHome();

    await screen.findByTestId("paths-list");
    // Newest first (docs/api.md): the last-created path leads the list.
    expect(itemIds()).toEqual(["p-done", "p-mid"]);

    const mid = within(await findItem("p-mid"));
    expect(mid.getByTestId("path-item-topic").textContent).toBe("TypeScript");
    expect(mid.getByTestId("path-item-level").textContent).toMatch(/some experience/i);
    expect(mid.getByTestId("path-item-progress").textContent).toMatch(/2 of 4 lessons/i);

    const done = within(await findItem("p-done"));
    expect(done.getByTestId("path-item-topic").textContent).toBe("Git internals");
    expect(done.getByTestId("path-item-level").textContent).toMatch(/i work in it/i);
    expect(done.getByTestId("path-item-progress").textContent).toMatch(/3 of 3 lessons/i);
    expect(done.getByTestId("path-item-status").textContent).toMatch(/complete/i);

    // No paths missing and no empty state alongside a populated list.
    expect(screen.queryByTestId("paths-empty")).toBeNull();
  });

  it("[AL-064/W7/W8] renders refused and failed paths in their own states", async () => {
    seedPath({
      id: "p-refused",
      topic: "Something unsafe",
      level: "new_to_it",
      resolution: "refused",
    });
    seedPath({ id: "p-failed", topic: "Rust ownership", level: "new_to_it", resolution: "failed" });
    await gotoHome();

    const refused = await findItem("p-refused");
    expect(refused.getAttribute("data-status")).toBe("refused");
    // Refusal is iris, never the error treatment (CONTEXT: never conflated).
    expect(refused.getAttribute("data-variant")).toBe("refusal");
    expect(within(refused).getByTestId("path-item-status").textContent).toMatch(/out of scope/i);

    const failed = await findItem("p-failed");
    expect(failed.getAttribute("data-status")).toBe("failed");
    expect(failed.getAttribute("data-variant")).toBe("error");
    expect(within(failed).getByTestId("path-item-status").textContent).toMatch(/didn't finish/i);
  });

  it("[AL-064/W5] delete asks to confirm; cancelling keeps the path", async () => {
    seedPath({ id: "p-keep", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoHome();

    const item = await findItem("p-keep");
    // No confirm until the learner asks to delete — and no browser confirm().
    expect(within(item).queryByTestId("path-delete-confirm")).toBeNull();

    fireEvent.click(within(item).getByTestId("path-delete-button"));
    await within(item).findByTestId("path-delete-confirm");

    fireEvent.click(within(item).getByTestId("path-delete-cancel"));
    await waitFor(() => {
      expect(within(item).queryByTestId("path-delete-confirm")).toBeNull();
    });
    expect(itemIds()).toEqual(["p-keep"]);
    expect(deletedPathIds()).toEqual([]);
  });

  it("[AL-064/W5] confirming delete removes only the target path", async () => {
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    seedPath({ id: "p-two", topic: "Rust ownership", level: "work_in_it", units: MID_PATH_UNITS });
    await gotoHome();

    const target = await findItem("p-one");
    fireEvent.click(within(target).getByTestId("path-delete-button"));
    fireEvent.click(await within(target).findByTestId("path-delete-confirm"));

    // The list updates in place — no reload, no other path harmed.
    await waitFor(() => {
      expect(itemIds()).toEqual(["p-two"]);
    });
    expect(deletedPathIds()).toEqual(["p-one"]);
    expect(screen.getByTestId("path-item-topic").textContent).toBe("Rust ownership");
  });

  it("[AL-064/W4] two paths keep independent progress and open their own path view", async () => {
    seedPath({ id: "p-early", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    seedPath({
      id: "p-later",
      topic: "Rust ownership",
      level: "some_experience",
      units: MID_PATH_UNITS,
    });
    await gotoHome();

    const early = within(await findItem("p-early"));
    const later = within(await findItem("p-later"));
    expect(early.getByTestId("path-item-progress").textContent).toMatch(/0 of 3 lessons/i);
    expect(later.getByTestId("path-item-progress").textContent).toMatch(/2 of 4 lessons/i);
    expect(early.getByTestId("path-item-open").getAttribute("href")).toBe("/paths/p-early");
    expect(later.getByTestId("path-item-open").getAttribute("href")).toBe("/paths/p-later");

    // Opening one lands on that path's own view, at its own position.
    fireEvent.click(later.getByTestId("path-item-open"));
    await screen.findByTestId("path-view");
    expect((await screen.findByTestId("path-progress")).textContent).toMatch(/2 of 4 lessons/i);
  });

  it("[AL-064] the empty state invites the learner to create a first path", async () => {
    await gotoHome();

    const empty = await screen.findByTestId("paths-empty");
    expect(
      within(empty)
        .getByRole("link", { name: /first path/i })
        .getAttribute("href"),
    ).toBe("/new");
    expect(screen.queryByTestId("paths-list")).toBeNull();
  });

  it("[AL-064] the New path CTA routes to onboarding", async () => {
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoHome();

    fireEvent.click(await screen.findByTestId("new-path-button"));

    expect(await screen.findByRole("heading", { name: /what do you want to learn/i })).toBeTruthy();
  });

  it("[AL-064/W5] a path already gone server-side (404) still leaves the list", async () => {
    seedPath({ id: "p-gone", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    seedPath({ id: "p-stay", topic: "Rust ownership", level: "work_in_it", units: MID_PATH_UNITS });
    await gotoHome();
    await findItem("p-gone");

    // Another client (a second tab, an earlier DELETE whose 204 was lost) has
    // already removed it, so this DELETE 404s. That is the outcome the learner
    // asked for — the row must go, not stick behind a "try again" notice.
    forgetPath("p-gone");

    const target = await findItem("p-gone");
    fireEvent.click(within(target).getByTestId("path-delete-button"));
    fireEvent.click(await within(target).findByTestId("path-delete-confirm"));

    await waitFor(() => {
      expect(itemIds()).toEqual(["p-stay"]);
    });
    expect(screen.queryByTestId("path-delete-error")).toBeNull();
  });

  it("[AL-064/W5] only the row being deleted reads as deleting", async () => {
    // A real in-flight window: without it there is nothing to observe, and the
    // bug this pins is a *global* isPending painting the wrong row.
    configurePaths({ deleteDelayMs: 80 });
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    seedPath({ id: "p-two", topic: "Rust ownership", level: "work_in_it", units: MID_PATH_UNITS });
    await gotoHome();

    const first = await findItem("p-one");
    fireEvent.click(within(first).getByTestId("path-delete-button"));
    fireEvent.click(await within(first).findByTestId("path-delete-confirm"));
    await waitFor(() => {
      expect(within(first).getByTestId("path-delete-confirm").textContent).toMatch(/deleting/i);
    });

    // While that DELETE is still in flight, arm the *other* row's confirm: it is
    // not being deleted, so it must not borrow the first row's pending label.
    const second = await findItem("p-two");
    fireEvent.click(within(second).getByTestId("path-delete-button"));
    const secondConfirm = await within(second).findByTestId("path-delete-confirm");
    expect(secondConfirm.textContent).toMatch(/^delete$/i);
    expect((secondConfirm as HTMLButtonElement).disabled).toBe(false);

    await waitFor(() => {
      expect(deletedPathIds()).toEqual(["p-one"]);
    });
  });

  it("[AL-064] a generating row resolves in place, without a reload", async () => {
    // A path the learner started elsewhere and came back to: still generating on
    // the first payload, resolved by the next poll. Nothing else moves this row.
    seedPath({
      id: "p-new",
      topic: "TypeScript",
      level: "new_to_it",
      pollsRemaining: 1,
      units: FRESH_PATH_UNITS,
    });
    await gotoHome();

    const item = await findItem("p-new");
    expect(within(item).getByTestId("path-item-status").textContent).toMatch(/drafting/i);
    expect(within(item).queryByTestId("path-item-progress")).toBeNull();

    // The shared 2s→5s cadence brings the ready row in place of the draft one.
    await waitFor(
      () => {
        expect(within(item).getByTestId("path-item-status").textContent).toMatch(/not started/i);
      },
      { timeout: 4000 },
    );
    expect(within(item).getByTestId("path-item-progress").textContent).toMatch(/0 of 3 lessons/i);
  });

  it("[AL-064/C4] the list stops polling behind a row that never resolves", async () => {
    vi.useFakeTimers();
    try {
      seedPath({ id: "p-stuck", topic: "TypeScript", level: "new_to_it", pollsRemaining: 9999 });
      window.history.pushState({}, "", "/");
      render(<App />);

      // Settle the auth gate + first payload, then let the poll run a while.
      for (let i = 0; i < 20; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(pathsListRequestCount()).toBeGreaterThan(1);

      // Past the stall cap the poll is done — no more GETs, however long we wait.
      for (let i = 0; i < 60; i++) await vi.advanceTimersByTimeAsync(1000);
      const settled = pathsListRequestCount();
      for (let i = 0; i < 30; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(pathsListRequestCount()).toBe(settled);
      // The row itself is untouched — the path view owns its recovery.
      expect(screen.getByTestId("path-item-status").textContent).toMatch(/drafting/i);
    } finally {
      vi.useRealTimers();
    }
  });

  it("[AL-064/W5] keyboard focus never drops through the confirm step", async () => {
    seedPath({ id: "p-keep", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoHome();

    const item = await findItem("p-keep");
    const deleteButton = within(item).getByTestId("path-delete-button");
    fireEvent.click(deleteButton);

    // Opening the confirm replaces the button the learner was on: focus goes to
    // the safe default, never the destructive one.
    const cancel = await within(item).findByTestId("path-delete-cancel");
    expect(document.activeElement).toBe(cancel);

    fireEvent.click(cancel);
    await waitFor(() => {
      expect(document.activeElement).toBe(within(item).getByTestId("path-delete-button"));
    });
  });

  it("[AL-064/W5] focus moves on to a real control once a row is deleted", async () => {
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    seedPath({ id: "p-two", topic: "Rust ownership", level: "work_in_it", units: MID_PATH_UNITS });
    await gotoHome();
    // Newest first: p-two leads, so p-one's successor is… nothing below it.
    await waitFor(() => expect(itemIds()).toEqual(["p-two", "p-one"]));

    // Delete the leading row: focus lands on the next row's Delete button.
    const first = await findItem("p-two");
    fireEvent.click(within(first).getByTestId("path-delete-button"));
    fireEvent.click(await within(first).findByTestId("path-delete-confirm"));
    await waitFor(() => {
      expect(itemIds()).toEqual(["p-one"]);
    });
    const survivor = await findItem("p-one");
    expect(document.activeElement).toBe(within(survivor).getByTestId("path-delete-button"));

    // Deleting the last row leaves no row to land on — the New path CTA takes it.
    fireEvent.click(within(survivor).getByTestId("path-delete-button"));
    fireEvent.click(await within(survivor).findByTestId("path-delete-confirm"));
    await screen.findByTestId("paths-empty");
    expect(document.activeElement).toBe(screen.getByTestId("new-path-button"));
  });

  it("[AL-064] a path started in onboarding is on the list on the way back", async () => {
    // Keep the outline generating so onboarding doesn't hand off to the path
    // view — the learner backs out to home themselves, mid-generation.
    configurePaths({ pollsBeforeResolve: 999 });
    await gotoHome();
    // The empty list is now cached, and fresh for `staleTime` (30s).
    await screen.findByTestId("paths-empty");

    fireEvent.click(screen.getByTestId("new-path-button"));
    const topic = await screen.findByRole("textbox", { name: /topic/i });
    fireEvent.change(topic, { target: { value: "TypeScript generics" } });
    fireEvent.click(screen.getByRole("button", { name: /build my path/i }));
    await screen.findByTestId("onboarding-generating");

    fireEvent.click(screen.getByRole("link", { name: /aleph home/i }));

    const list = await screen.findByTestId("paths-list");
    expect(within(list).getByTestId("path-item-topic").textContent).toBe("TypeScript generics");
    expect(within(list).getByTestId("path-item-status").textContent).toMatch(/drafting/i);
    expect(screen.queryByTestId("paths-empty")).toBeNull();
  });

  it("[AL-064] a failed delete keeps the path and says so", async () => {
    configurePaths({ deleteFails: true });
    seedPath({ id: "p-one", topic: "TypeScript", level: "new_to_it", units: FRESH_PATH_UNITS });
    await gotoHome();

    const item = await findItem("p-one");
    fireEvent.click(within(item).getByTestId("path-delete-button"));
    fireEvent.click(await within(item).findByTestId("path-delete-confirm"));

    await screen.findByTestId("path-delete-error");
    expect(itemIds()).toEqual(["p-one"]);
  });
});
