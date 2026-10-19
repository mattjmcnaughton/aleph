import { fireEvent, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { seedBeat } from "../mocks/beats";
import { learnerUser } from "../mocks/handlers";
import { MID_PATH_UNITS, seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// Home's two work lists collapse. Driven end to end through the real router,
// TanStack Query, and the MSW fakes — the seam every other home test uses.
//
// The rule under test is the same for both sections: the kicker is the toggle,
// it reports `aria-expanded`, the region it names goes away, and a collapsed
// section still says how much is inside it. Nothing about the *data* changes —
// collapsing is presentation, so the queries and the rows behind them are
// untouched, which is why closing and reopening restores the same list.

const analystSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { analyst: true } },
};

async function gotoHome(): Promise<void> {
  window.history.pushState({}, "", "/");
  render(<App />);
  await screen.findByTestId("paths-switcher");
}

describe("Home — collapsible sections", () => {
  it("[collapse] 'Your paths' folds the list away and says what it is hiding", async () => {
    seedPath({ id: "p-one", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    seedPath({ id: "p-two", topic: "Rust ownership", level: "work_in_it", units: MID_PATH_UNITS });
    await gotoHome();
    await screen.findByTestId("paths-list");

    const toggle = screen.getByTestId("paths-section-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    // The toggle names the region it discloses, and that region exists.
    const regionId = toggle.getAttribute("aria-controls");
    expect(regionId).toBeTruthy();
    expect(document.getElementById(regionId as string)).toBeTruthy();

    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // Unmounted, not painted out of sight: a collapsed section leaves no
    // focusable rows behind for a keyboard user to tab into.
    expect(screen.queryByTestId("path-list-item")).toBeNull();
    // …but the region it names is still there for `aria-controls` to resolve.
    expect(document.getElementById(regionId as string)).toBeTruthy();
    // A collapsed section is not a silent one: the count survives the fold.
    expect(screen.getByText("2 paths")).toBeTruthy();

    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByTestId("path-list-item")).toHaveLength(2);
    expect(screen.queryByText("2 paths")).toBeNull();
  });

  it("[collapse] a one-path account reads '1 path', not '1 paths'", async () => {
    seedPath({ id: "p-one", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    await gotoHome();
    await screen.findByTestId("paths-list");

    fireEvent.click(screen.getByTestId("paths-section-toggle"));

    expect(screen.getByText("1 path")).toBeTruthy();
  });

  it("[collapse] 'Your beats' folds independently of 'Your paths'", async () => {
    server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(analystSession)));
    seedPath({ id: "p-one", topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    seedBeat({ id: "beat-one", topic: "EU AI regulation", level: "some_experience" });
    await gotoHome();
    await screen.findByTestId("beats-list");

    const beats = screen.getByTestId("beats-section-toggle");
    fireEvent.click(beats);

    expect(beats.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("beat-list-item")).toBeNull();
    expect(screen.getByText("1 Beat")).toBeTruthy();
    // The other section is untouched — two toggles, two pieces of state.
    expect(screen.getByTestId("paths-section-toggle").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByTestId("path-list-item")).toHaveLength(1);
    // Deploying another Beat is still reachable with the list folded — from
    // the New menu, which is the section's only door now and sits outside
    // anything that can collapse.
    expect(screen.getByTestId("new-menu-button")).toBeTruthy();

    fireEvent.click(beats);
    expect(screen.getAllByTestId("beat-list-item")).toHaveLength(1);
  });

  it("[collapse] the empty state collapses too — it is the section's content", async () => {
    await gotoHome();
    await screen.findByTestId("paths-empty");

    fireEvent.click(screen.getByTestId("paths-section-toggle"));

    expect(screen.queryByTestId("paths-empty")).toBeNull();
    expect(screen.getByText("0 paths")).toBeTruthy();
  });
});
