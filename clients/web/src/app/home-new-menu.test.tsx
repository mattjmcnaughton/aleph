import { fireEvent, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { MID_PATH_UNITS, seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// Home's top-right "start something new" control (`components/new-menu.tsx`).
//
// Two shapes, one control: with the `analyst` flag on there are two top-level
// things a learner can start — a Path and a Beat — so it is a menu; with the
// flag off there is one, so it stays the plain "New path" button it has always
// been, testid included. That fallback is not a nicety: a dropdown holding a
// single item costs a tap and buys nothing, and the delete flow's focus rule
// (C3) still has to land somewhere real when the last path goes.

const analystSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { analyst: true } },
};

function useAnalystSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(analystSession)));
}

async function gotoHome(): Promise<void> {
  window.history.pushState({}, "", "/");
  render(<App />);
  await screen.findByTestId("paths-switcher");
}

describe("Home — the New menu", () => {
  it("[analyst off] is the plain New path button, with no menu at all", async () => {
    // The default fake learner (`mocks/handlers.ts`) ships `analyst: false`.
    await gotoHome();

    expect(screen.getByTestId("new-path-button")).toBeTruthy();
    expect(screen.queryByTestId("new-menu-button")).toBeNull();
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.click(screen.getByTestId("new-path-button"));

    expect(await screen.findByRole("heading", { name: /what do you want to learn/i })).toBeTruthy();
  });

  it("[analyst on] opens a menu offering a path and a Beat", async () => {
    useAnalystSession();
    await gotoHome();

    const trigger = await screen.findByTestId("new-menu-button");
    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    // Closed is closed: nothing of the menu is in the DOM until it is opened.
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.click(trigger);

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const items = screen.getAllByRole("menuitem");
    expect(items.map((item) => item.getAttribute("href"))).toEqual(["/new", "/beats/new"]);
    // Opening moves focus into the menu (the menu-button convention), so a
    // keyboard user is never stranded on a trigger they cannot step off.
    expect(document.activeElement).toBe(items[0]);
  });

  it("[analyst on] the path item routes to onboarding and closes the menu", async () => {
    useAnalystSession();
    await gotoHome();

    fireEvent.click(await screen.findByTestId("new-menu-button"));
    fireEvent.click(screen.getByTestId("new-path-button"));

    expect(await screen.findByRole("heading", { name: /what do you want to learn/i })).toBeTruthy();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("[analyst on] the Beat item routes to the deploy form", async () => {
    useAnalystSession();
    await gotoHome();

    fireEvent.click(await screen.findByTestId("new-menu-button"));
    fireEvent.click(screen.getByTestId("new-beat-menu-item"));

    expect(await screen.findByRole("heading", { name: /what should aleph keep watch on/i })).toBe(
      screen.getByRole("heading", { level: 1 }),
    );
  });

  it("[analyst on] Escape closes it and hands focus back to the trigger", async () => {
    useAnalystSession();
    await gotoHome();

    const trigger = await screen.findByTestId("new-menu-button");
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("[analyst on] arrow keys walk the items, wrapping at the ends", async () => {
    useAnalystSession();
    await gotoHome();

    fireEvent.click(await screen.findByTestId("new-menu-button"));
    const menu = screen.getByRole("menu");
    const [first, second] = screen.getAllByRole("menuitem");

    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(document.activeElement).toBe(second);
  });

  it("[analyst on] a click anywhere else dismisses it", async () => {
    useAnalystSession();
    seedPath({ id: "p-one", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    await gotoHome();

    fireEvent.click(await screen.findByTestId("new-menu-button"));
    expect(screen.getByRole("menu")).toBeTruthy();

    fireEvent.pointerDown(await screen.findByTestId("paths-list"));

    expect(screen.queryByRole("menu")).toBeNull();
  });
});
