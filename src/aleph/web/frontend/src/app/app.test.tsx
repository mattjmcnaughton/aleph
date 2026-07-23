import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE } from "../lib/api";
import { signedOutSession } from "../mocks/handlers";
import { server } from "../mocks/server";
import { App } from "./app";

describe("App shell + auth gate", () => {
  it("[AL-060] renders the signed-in shell for an authenticated learner", async () => {
    window.history.pushState({}, "", "/");

    render(<App />);

    expect(await screen.findByRole("heading", { name: /Welcome back, Dev\./ })).toBeTruthy();
    // App chrome is present when signed in.
    expect(screen.getByRole("link", { name: "Aleph home" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "New path" })).toBeTruthy();
  });

  it("[AL-060] redirects an unauthenticated visitor to the login screen", async () => {
    server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(signedOutSession)));
    window.history.pushState({}, "", "/");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Sign in to Aleph" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Continue with Keycloak (dev)" })).toBeTruthy();
    // No app chrome on the login surface.
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });

  it("[AL-060] names the configured OIDC provider on the login screen", async () => {
    server.use(
      http.get(`${API_V1_BASE}/auth/session`, () =>
        HttpResponse.json({ authenticated: false, provider: "auth0", user: null }),
      ),
    );
    window.history.pushState({}, "", "/login");

    render(<App />);

    expect(await screen.findByRole("link", { name: "Continue with Auth0" })).toBeTruthy();
  });

  it("[AL-060] signs out and lands back on the login screen", async () => {
    window.history.pushState({}, "", "/");

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Sign in to Aleph" })).toBeTruthy();
    });
  });
});
