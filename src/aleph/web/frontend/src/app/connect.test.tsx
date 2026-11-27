import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { EXAMPLE_CONSENT, configureConsent } from "../mocks/api-keys";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { navigation } from "../routes/connect";
import { App } from "./app";

// /connect: the consent step of the MCP OAuth flow, through the real router.

function signedIn() {
  const value: AuthSession = {
    authenticated: true,
    provider: "keycloak",
    user: { ...learnerUser, feature_flags: { mcp: true } },
  };
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(value)));
}

function goto(url: string) {
  window.history.pushState({}, "", url);
  render(<App />);
}

afterEach(() => vi.restoreAllMocks());

describe("Connect an app", () => {
  it("names the app and where it returns, then sends the browser back with a code", async () => {
    signedIn();
    configureConsent(EXAMPLE_CONSENT);
    const leave = vi.spyOn(navigation, "leave").mockImplementation(() => {});
    goto("/connect");

    expect(await screen.findByRole("heading", { name: "Connect Claude Code?" })).toBeVisible();
    expect(screen.getByTestId("connect-client-host")).toHaveTextContent("claude.ai");
    expect(screen.getByTestId("connect-redirect-host")).toHaveTextContent("localhost:53682");
    expect(screen.getByTestId("connect-loopback-note")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(leave).toHaveBeenCalledTimes(1));
    expect(leave.mock.calls[0][0]).toContain("code=demo-code");
  });

  it("returns access_denied on Cancel", async () => {
    signedIn();
    configureConsent({ ...EXAMPLE_CONSENT, loopback_only: false });
    const leave = vi.spyOn(navigation, "leave").mockImplementation(() => {});
    goto("/connect");
    await screen.findByRole("heading", { name: "Connect Claude Code?" });
    expect(screen.queryByTestId("connect-loopback-note")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(leave).toHaveBeenCalledTimes(1));
    expect(leave.mock.calls[0][0]).toContain("error=access_denied");
  });

  it("says so when no request is waiting, or the app's request was refused", async () => {
    signedIn();
    goto("/connect");
    expect(await screen.findByRole("heading", { name: "No app is waiting" })).toBeVisible();
  });

  it("explains a refused request without contacting the app", async () => {
    signedIn();
    goto("/connect?error=invalid_redirect");
    expect(await screen.findByTestId("connect-error")).toHaveTextContent("hasn't listed");
  });
});
