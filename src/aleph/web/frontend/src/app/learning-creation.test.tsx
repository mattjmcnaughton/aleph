import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { focusManager } from "@tanstack/react-query";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { App } from "./app";

const id = "11111111-1111-4111-8111-111111111111";
function seed(kind: "path" | "beat", status = "active") {
  server.use(
    http.get(`${API_V1_BASE}/auth/session`, () =>
      HttpResponse.json({
        authenticated: true,
        provider: "keycloak",
        user: { ...learnerUser, feature_flags: { learning_tutor: true, analyst: true } },
      }),
    ),
    http.get(`${API_V1_BASE}/learning-tutor/`, () => HttpResponse.json({ revision: 7 })),
    http.get(`${API_V1_BASE}/learning-tutor/recommendations/${id}`, () =>
      HttpResponse.json({
        id,
        kind,
        status,
        topic: "Electricity",
        level: "some_experience",
        guidance: "Foundations",
        target_available: false,
      }),
    ),
  );
}
describe("Recommendation creation", () => {
  it.each(["path", "beat"] as const)(
    "fences late %s creation when the account changes",
    async (kind) => {
      seed(kind);
      let switched = false;
      let received = false;
      let release!: () => void;
      const pending = new Promise<void>((resolve) => {
        release = resolve;
      });
      server.use(
        http.get(`${API_V1_BASE}/auth/session`, () =>
          HttpResponse.json({
            authenticated: true,
            provider: "keycloak",
            user: {
              ...learnerUser,
              id: switched ? "second-account" : learnerUser.id,
              feature_flags: { learning_tutor: true, analyst: true },
            },
          }),
        ),
        http.get(`${API_V1_BASE}/learning-tutor/recommendations/${id}`, () =>
          switched
            ? new HttpResponse(null, { status: 404 })
            : HttpResponse.json({
                id,
                kind,
                status: "active",
                topic: "Private topic",
                level: "some_experience",
              }),
        ),
        http.post(`${API_V1_BASE}/${kind === "path" ? "paths" : "beats"}`, async () => {
          received = true;
          await pending;
          return HttpResponse.json({ id, status: "generating" });
        }),
      );
      const route = `${kind === "path" ? "/new" : "/beats/new"}?recommendation=${id}`;
      window.history.pushState({}, "", route);
      render(<App />);
      await screen.findByRole("textbox", { name: "Topic" });
      fireEvent.click(
        screen.getByRole("button", { name: kind === "path" ? "Build my path" : "Deploy analyst" }),
      );
      await waitFor(() => expect(received).toBe(true));
      switched = true;
      await act(async () => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
      });
      await screen.findByText("Recommendation unavailable");
      await act(async () => {
        release();
        await pending;
      });
      expect(screen.queryByRole("textbox", { name: "Topic" })).toBeNull();
      expect(window.location.pathname).toBe(kind === "path" ? "/new" : "/beats/new");
      focusManager.setFocused(undefined);
    },
  );
  it.each(["path", "beat"] as const)(
    "keeps %s dirty fields on draft transport errors but respects authoritative 404",
    async (kind) => {
      seed(kind);
      window.history.pushState(
        {},
        "",
        `${kind === "path" ? "/new" : "/beats/new"}?recommendation=${id}`,
      );
      render(<App />);
      const topic = await screen.findByRole("textbox", { name: "Topic" });
      fireEvent.change(topic, { target: { value: "Unsaved edit" } });
      let reads = 0;
      let status = 503;
      server.use(
        http.get(`${API_V1_BASE}/learning-tutor/recommendations/${id}`, () => {
          reads++;
          return new HttpResponse(null, { status });
        }),
      );
      await act(async () => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
      });
      await waitFor(() => expect(reads).toBe(1));
      await act(async () => {});
      expect(screen.getByRole("textbox", { name: "Topic" })).toHaveValue("Unsaved edit");
      status = 404;
      await act(async () => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
      });
      await screen.findByText("Recommendation unavailable");
      expect(screen.queryByRole("textbox", { name: "Topic" })).toBeNull();
      focusManager.setFocused(undefined);
    },
  );
  it.each(["path", "beat"] as const)(
    "keeps %s uncertain receipt after an accepted draft refetch",
    async (kind) => {
      seed(kind);
      const bodies: unknown[] = [];
      server.use(
        http.post(`${API_V1_BASE}/${kind === "path" ? "paths" : "beats"}`, async ({ request }) => {
          bodies.push(await request.json());
          return HttpResponse.error();
        }),
      );
      window.history.pushState(
        {},
        "",
        `${kind === "path" ? "/new" : "/beats/new"}?recommendation=${id}`,
      );
      render(<App />);
      await screen.findByRole("textbox", { name: "Topic" });
      fireEvent.click(
        screen.getByRole("button", { name: kind === "path" ? "Build my path" : "Deploy analyst" }),
      );
      await screen.findByRole("button", { name: "Recover creation" });
      let reads = 0;
      server.use(
        http.get(`${API_V1_BASE}/learning-tutor/recommendations/${id}`, () => {
          reads++;
          return HttpResponse.json({
            id,
            kind,
            status: "accepted",
            target_id: id,
            target_available: true,
          });
        }),
      );
      await act(async () => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
      });
      await waitFor(() => expect(reads).toBe(1));
      await act(async () => {});
      fireEvent.click(screen.getByRole("button", { name: "Recover creation" }));
      await waitFor(() => expect(bodies).toHaveLength(2));
      expect(bodies[1]).toEqual(bodies[0]);
      focusManager.setFocused(undefined);
    },
  );
  it.each(["path", "beat"] as const)(
    "retains %s edits and original POST after transient refetch failure",
    async (kind) => {
      seed(kind);
      let failReads = false;
      let failedReads = 0;
      const bodies: unknown[] = [];
      server.use(
        http.get(`${API_V1_BASE}/learning-tutor/`, () => {
          if (failReads) {
            failedReads++;
            return new HttpResponse(null, { status: 503 });
          }
          return HttpResponse.json({ revision: 7 });
        }),
        http.post(`${API_V1_BASE}/${kind === "path" ? "paths" : "beats"}`, async ({ request }) => {
          bodies.push(await request.json());
          failReads = true;
          return HttpResponse.error();
        }),
      );
      window.history.pushState(
        {},
        "",
        `${kind === "path" ? "/new" : "/beats/new"}?recommendation=${id}`,
      );
      render(<App />);
      const topic = await screen.findByRole("textbox", { name: "Topic" });
      fireEvent.change(topic, { target: { value: "Original edited topic" } });
      fireEvent.click(
        screen.getByRole("button", { name: kind === "path" ? "Build my path" : "Deploy analyst" }),
      );
      await waitFor(() => expect(failedReads).toBeGreaterThan(0));
      // Let the application's single retry finish, not merely the first HTTP attempt.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 1200));
      });
      await screen.findByRole("button", { name: "Recover creation" });
      expect(screen.getByRole("textbox", { name: "Topic" })).toHaveValue("Original edited topic");
      fireEvent.change(topic, { target: { value: "New unsent topic" } });
      fireEvent.click(screen.getByRole("button", { name: "Recover creation" }));
      await waitFor(() => expect(bodies).toHaveLength(2));
      expect(bodies[1]).toEqual(bodies[0]);
    },
  );
  it("keeps accepted path generation mounted across draft refetch", async () => {
    seed("path");
    let accepted = false;
    let acceptedReads = 0;
    server.use(
      http.post(`${API_V1_BASE}/paths`, () => {
        accepted = true;
        return HttpResponse.json({ id, status: "generating" });
      }),
      http.get(`${API_V1_BASE}/paths/${id}`, () =>
        HttpResponse.json({ id, status: "generating", topic: "Electricity", lessons: [] }),
      ),
      http.get(`${API_V1_BASE}/learning-tutor/recommendations/${id}`, () => {
        if (accepted) acceptedReads++;
        return HttpResponse.json({
          id,
          kind: "path",
          status: accepted ? "accepted" : "active",
          topic: "Electricity",
          level: "some_experience",
          target_id: id,
          target_available: true,
        });
      }),
    );
    window.history.pushState({}, "", `/new?recommendation=${id}`);
    render(<App />);
    await screen.findByRole("textbox", { name: "Topic" });
    fireEvent.click(screen.getByRole("button", { name: "Build my path" }));
    await waitFor(() => expect(accepted).toBe(true));
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    await waitFor(() => expect(acceptedReads).toBeGreaterThan(0));
    expect(screen.queryByText("Already accepted")).toBeNull();
    expect(screen.getByTestId("onboarding-generating")).toBeInTheDocument();
    focusManager.setFocused(undefined);
  });
  it("does not overwrite edited fields after refetch and replays the original uncertain payload", async () => {
    seed("path");
    let reads = 0;
    server.use(
      http.get(`${API_V1_BASE}/learning-tutor/recommendations/${id}`, () => {
        reads++;
        return HttpResponse.json({
          id,
          kind: "path",
          status: "active",
          topic: reads === 1 ? "Electricity" : "Changed server text",
          level: "some_experience",
          guidance: "Foundations",
        });
      }),
    );
    const bodies: Record<string, unknown>[] = [];
    server.use(
      http.post(`${API_V1_BASE}/paths`, async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.error();
      }),
    );
    window.history.pushState({}, "", `/new?recommendation=${id}`);
    render(<App />);
    const topic = await screen.findByRole("textbox", { name: "Topic" });
    fireEvent.change(topic, { target: { value: "My edit" } });
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    await waitFor(() => expect(reads).toBeGreaterThan(1));
    expect(topic).toHaveValue("My edit");
    fireEvent.click(screen.getByRole("button", { name: "Build my path" }));
    await screen.findByRole("button", { name: "Recover creation" });
    fireEvent.change(topic, { target: { value: "Later unsent edit" } });
    fireEvent.click(screen.getByRole("button", { name: "Recover creation" }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toEqual(bodies[0]);
    expect(bodies[1].topic).toBe("My edit");
    focusManager.setFocused(undefined);
  });
  it.each([404, 409])("never falls back to topic for an unavailable draft (%s)", async (status) => {
    seed("path");
    server.use(
      http.get(`${API_V1_BASE}/learning-tutor/recommendations/${id}`, () =>
        HttpResponse.json({ error: { code: "conflict" } }, { status }),
      ),
    );
    window.history.pushState({}, "", `/new?recommendation=${id}&topic=fallback`);
    render(<App />);
    await screen.findByText("Recommendation unavailable");
    expect(screen.queryByRole("textbox", { name: "Topic" })).toBeNull();
  });
  it.each(["path", "beat"] as const)(
    "seeds editable %s instead of topic URL, sends edited receipt",
    async (kind) => {
      seed(kind);
      const bodies: Record<string, unknown>[] = [];
      server.use(
        http.post(`${API_V1_BASE}/${kind === "path" ? "paths" : "beats"}`, async ({ request }) => {
          bodies.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json({ error: { code: "rate_limited" } }, { status: 429 });
        }),
      );
      window.history.pushState(
        {},
        "",
        `${kind === "path" ? "/new" : "/beats/new"}?recommendation=${id}&topic=ignored`,
      );
      render(<App />);
      const topic = await screen.findByRole("textbox", { name: "Topic" });
      expect(topic).toHaveValue("Electricity");
      fireEvent.change(topic, { target: { value: "Edited energy" } });
      fireEvent.click(
        screen.getByRole("button", { name: kind === "path" ? "Build my path" : "Deploy analyst" }),
      );
      await waitFor(() => expect(bodies).toHaveLength(1));
      expect(bodies[0]).toMatchObject({
        topic: "Edited energy",
        level: "some_experience",
        guidance: "Foundations",
        learning_recommendation_id: id,
        learning_tutor_revision: 7,
      });
      expect(topic).toHaveValue("Edited energy");
    },
  );
  it("does not recreate an accepted deleted target", async () => {
    seed("path", "accepted");
    window.history.pushState({}, "", `/new?recommendation=${id}`);
    render(<App />);
    await screen.findByText(/accepted target was deleted/);
    expect(screen.queryByRole("textbox", { name: "Topic" })).toBeNull();
  });
});
