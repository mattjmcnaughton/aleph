import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { App } from "./app";

const base = `${API_V1_BASE}/learning-tutor`;
function setup(enabled = true) {
  const state = {
    focus: null as string | null,
    revision: 0,
    focus_version: 0,
    epoch: 0,
    recommendations: [],
    latest_refresh: null,
    current_operation_id: null,
    history: { items: [], before: null },
  };
  let posts = 0;
  server.use(
    http.get(`${API_V1_BASE}/auth/session`, () =>
      HttpResponse.json({
        authenticated: true,
        provider: "keycloak",
        user: { ...learnerUser, feature_flags: { learning_tutor: enabled } },
      }),
    ),
    http.get(`${base}/`, () => HttpResponse.json(state)),
    http.get(`${base}/recommendations`, () => HttpResponse.json({ items: [], before: null })),
    http.post(`${base}/operations`, () => {
      posts++;
      return HttpResponse.json({ error: { code: "rate_limited" } }, { status: 429 });
    }),
    http.put(`${base}/focus`, async ({ request }) => {
      const body = (await request.json()) as { focus: string | null };
      state.focus = body.focus;
      state.revision++;
      return HttpResponse.json(state);
    }),
    http.delete(`${base}/conversation`, () => {
      state.epoch++;
      state.revision++;
      return HttpResponse.json(state);
    }),
  );
  return { state, posts: () => posts };
}

describe("Account-wide Tutor", () => {
  it("is unavailable with flag off, without calling generation", async () => {
    const fake = setup(false);
    window.history.pushState({}, "", "/tutor");
    render(<App />);
    await screen.findByText("Your account-wide Tutor isn't available.");
    expect(fake.posts()).toBe(0);
  });
  it("visits without POST, saves focus explicitly, and confirms clear preserves it", async () => {
    const fake = setup();
    window.history.pushState({}, "", "/tutor");
    render(<App />);
    const focus = await screen.findByLabelText("Learning focus");
    fireEvent.change(focus, { target: { value: "Electricity markets" } });
    expect(fake.state.focus).toBeNull();
    expect(fake.posts()).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Save focus" }));
    await waitFor(() => expect(fake.state.focus).toBe("Electricity markets"));
    fireEvent.click(screen.getByRole("button", { name: "Clear Learning conversation" }));
    expect(screen.getByRole("button", { name: "Keep conversation" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Confirm clear" }));
    await waitFor(() => expect(fake.state.epoch).toBe(1));
    expect(fake.state.focus).toBe("Electricity markets");
    expect(fake.posts()).toBe(0);
  });
  it("keeps input on rate limit, Enter inserts a line and Shift+Enter sends", async () => {
    const fake = setup();
    window.history.pushState({}, "", "/tutor");
    render(<App />);
    const composer = await screen.findByLabelText("Message your Tutor");
    fireEvent.change(composer, { target: { value: "Where should I start?" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(fake.posts()).toBe(0);
    fireEvent.keyDown(composer, { key: "Enter", shiftKey: true });
    await screen.findByText(/reached the limit/);
    expect(composer).toHaveValue("Where should I start?");
    expect(fake.posts()).toBe(1);
  });
});
