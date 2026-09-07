import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { configureSettings, settingsPatchRequests } from "../mocks/settings";
import { App } from "./app";

// `/settings` (CONTEXT.md: Settings / Auto-draft): the header's gear leads
// here; the Auto-draft switch reads the session's `user.settings` and writes
// straight through `PATCH /settings`, folding the response back into that
// cached session so the switch shows what the server holds.

const flashcardsSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { flashcards: true } },
};

function useFlashcardsSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(flashcardsSession)));
}

describe("Settings (CONTEXT.md: Settings / Auto-draft)", () => {
  it("the app header's gear leads to /settings, where Auto-draft reads on by default", async () => {
    useFlashcardsSession();
    window.history.pushState({}, "", "/");
    render(<App />);

    fireEvent.click(await screen.findByRole("link", { name: "Settings" }));

    await screen.findByTestId("settings-page");
    const toggle = screen.getByRole("switch", { name: "Draft flashcards automatically" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(window.location.pathname).toBe("/settings");
  });

  it("flipping Auto-draft off PATCHes only that setting and the switch reflects the saved state", async () => {
    useFlashcardsSession();
    window.history.pushState({}, "", "/settings");
    render(<App />);

    const toggle = await screen.findByRole("switch", { name: "Draft flashcards automatically" });
    fireEvent.click(toggle);

    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("false"));
    expect(settingsPatchRequests()).toEqual([{ auto_draft_flashcards: false }]);

    // And back on — a second flip sends the opposite, never a stale value.
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
    expect(settingsPatchRequests()).toEqual([
      { auto_draft_flashcards: false },
      { auto_draft_flashcards: true },
    ]);
  });

  it("a failed save leaves the switch where it was and says so", async () => {
    useFlashcardsSession();
    configureSettings({ patchFails: true });
    window.history.pushState({}, "", "/settings");
    render(<App />);

    const toggle = await screen.findByRole("switch", { name: "Draft flashcards automatically" });
    fireEvent.click(toggle);

    await screen.findByTestId("settings-save-error");
    // Never optimistic: the switch only ever shows what the server holds.
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect((toggle as HTMLButtonElement).disabled).toBe(false);
  });

  it("with the flashcards flag off there is nothing to change", async () => {
    // The default fake learner ships `flashcards: false`.
    window.history.pushState({}, "", "/settings");
    render(<App />);

    await screen.findByTestId("settings-empty");
    expect(screen.queryByRole("switch")).toBeNull();
  });
});
