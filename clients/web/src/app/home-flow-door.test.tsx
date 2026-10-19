import { HttpResponse, http } from "msw";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { COMPLETE_PATH_UNITS, MID_PATH_UNITS, seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// Home's door into Flow (flow TDD §2/§6, mock screen 01) — the flag gate and
// the "at least one flowable path" gate together, driven end to end through
// the real router and the paths fake.

function useFlowSession(): void {
  const session: AuthSession = {
    authenticated: true,
    provider: "keycloak",
    user: { ...learnerUser, feature_flags: { flow: true } },
  };
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

async function gotoHome(): Promise<void> {
  window.history.pushState({}, "", "/");
  render(<App />);
  await screen.findByTestId("paths-switcher");
}

describe("Home — the Flow door", () => {
  it("renders under Continue when the flag is on and a path is flowable", async () => {
    useFlowSession();
    seedPath({
      id: "p-mid",
      topic: "Rust ownership",
      level: "some_experience",
      units: MID_PATH_UNITS,
    });
    await gotoHome();

    const door = await screen.findByTestId("flow-door");
    expect(door.getAttribute("href")).toBe("/flow/new");
    // Sits directly under Continue (mock pin 1) — same order in the DOM.
    const continueCard = screen.getByTestId("continue-card");
    expect(
      continueCard.compareDocumentPosition(door) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("is absent when the flag is off, even with a flowable path", async () => {
    // The default learner session ships every flag off (mocks/handlers.ts).
    seedPath({
      id: "p-mid",
      topic: "Rust ownership",
      level: "some_experience",
      units: MID_PATH_UNITS,
    });
    await gotoHome();

    await screen.findByTestId("path-list-item");
    expect(screen.queryByTestId("flow-door")).toBeNull();
  });

  it("is absent when every path is finished, even with the flag on", async () => {
    useFlowSession();
    seedPath({
      id: "p-done",
      topic: "Git internals",
      level: "work_in_it",
      units: COMPLETE_PATH_UNITS,
    });
    await gotoHome();

    await screen.findByTestId("path-list-item");
    expect(screen.queryByTestId("flow-door")).toBeNull();
  });

  it("is absent with the flag on and no paths at all", async () => {
    useFlowSession();
    await gotoHome();

    await screen.findByTestId("paths-empty");
    expect(screen.queryByTestId("flow-door")).toBeNull();
  });
});
