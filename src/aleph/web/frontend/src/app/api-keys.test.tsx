import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { configureApiKeys } from "../mocks/api-keys";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { App } from "./app";

// Settings → API keys, through the real router and the MSW fakes.

function session(mcp: boolean) {
  const value: AuthSession = {
    authenticated: true,
    provider: "keycloak",
    user: { ...learnerUser, feature_flags: { mcp } },
  };
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(value)));
}

function goto(url: string) {
  window.history.pushState({}, "", url);
  render(<App />);
}

describe("API keys settings", () => {
  it("is closed while the mcp flag is off", async () => {
    session(false);
    goto("/settings/api-keys");
    expect(await screen.findByTestId("api-keys-unavailable")).toBeVisible();
    expect(screen.queryByTestId("api-key-name")).toBeNull();
  });

  it("shows a new key once, puts it in the config, and forgets it on dismiss", async () => {
    session(true);
    goto("/settings/api-keys");
    expect(await screen.findByText("No keys yet.")).toBeVisible();
    expect(screen.getByTestId("mcp-config").textContent).toContain("Bearer YOUR_API_KEY");

    fireEvent.change(screen.getByTestId("api-key-name"), { target: { value: "Claude" } });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));

    const shown = await screen.findByTestId("new-api-key-value");
    const secret = (shown as HTMLInputElement).value;
    expect(secret).toMatch(/^k3yDemo/);
    expect(shown).toHaveAttribute("type", "password");
    expect(screen.getByTestId("mcp-config").textContent).toContain(`Bearer ${secret}`);
    expect(await screen.findByTestId("api-key-row")).toHaveTextContent("Claude");

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("new-api-key")).toBeNull();
    expect(document.body.textContent).not.toContain(secret);
  });

  it("revokes only after confirming, and keeps the revoked key listed", async () => {
    session(true);
    configureApiKeys([
      {
        id: "00000000-0000-4000-8000-0000000000aa",
        name: "Cursor",
        created_at: "2026-11-20T10:00:00Z",
        revoked_at: null,
      },
    ]);
    goto("/settings/api-keys");
    const row = await screen.findByTestId("api-key-row");
    fireEvent.click(within(row).getByRole("button", { name: "Revoke Cursor" }));
    fireEvent.click(within(row).getByRole("button", { name: "Cancel" }));
    expect(within(row).queryByText("Revoked")).toBeNull();

    fireEvent.click(within(row).getByRole("button", { name: "Revoke Cursor" }));
    fireEvent.click(within(row).getByRole("button", { name: "Revoke key" }));
    await waitFor(() =>
      expect(within(screen.getByTestId("api-key-row")).getByText("Revoked")).toBeVisible(),
    );
    expect(screen.getByTestId("api-keys-message")).toHaveTextContent("API key revoked.");
  });
});

describe("Connected apps", () => {
  it("lists apps connected by OAuth apart from keys, and disconnects after confirming", async () => {
    session(true);
    configureApiKeys(
      [],
      [
        {
          id: "00000000-0000-4000-8000-0000000000c1",
          name: "Claude Code",
          client_host: "claude.ai",
          created_at: "2026-11-25T10:00:00Z",
        },
      ],
    );
    goto("/settings/api-keys");
    const row = await screen.findByTestId("connection-row");
    expect(row).toHaveTextContent("Claude Code");
    expect(row).toHaveTextContent("claude.ai");
    expect(screen.getByText("No keys yet.")).toBeVisible();

    fireEvent.click(within(row).getByRole("button", { name: "Disconnect Claude Code" }));
    fireEvent.click(within(row).getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("No connected apps.")).toBeVisible();
    expect(screen.getByTestId("api-keys-message")).toHaveTextContent("Claude Code disconnected.");
  });
});
