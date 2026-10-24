import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { learningFake, learningReceipt } from "../mocks/learning-tutor";
import type { LearningOperationRequest } from "../lib/learning-tutor-types";
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
  it("keeps optional choices inline, submits only on Continue, and keeps free chat available", async () => {
    setup();
    const fake = learningFake();
    const turn = learningReceipt({
      kind: "turn",
      content: "Surprise me",
      request_id: "question",
      epoch: 0,
      revision: 0,
    });
    if (!turn.output) throw new Error("Expected successful fixture output");
    turn.output = {
      ...turn.output,
      question: {
        prompt: "How far would you like to wander?",
        options: ["A fresh angle", "Something completely different", "Surprise me"],
      },
    };
    fake.state.history.items = [turn];
    server.use(...fake.handlers);
    window.history.pushState({}, "", "/tutor");
    render(<App />);
    const choice = await screen.findByRole("radio", { name: "Something completely different" });
    expect(
      screen.queryByRole("button", { name: /Refresh recommendations|Find directions/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(choice);
    expect(fake.requests).toHaveLength(0);
    const composer = screen.getByLabelText("Message your Tutor");
    fireEvent.change(composer, { target: { value: "My own question" } });
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.change(composer, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(fake.requests).toHaveLength(1));
    expect(fake.requests[0]).toMatchObject({
      kind: "turn",
      content: "How far would you like to wander?\nSomething completely different",
    });
    await waitFor(() => expect(screen.queryByRole("radio")).not.toBeInTheDocument());
    expect(screen.getByLabelText("Message your Tutor")).toBeEnabled();
  });
  it("blocks a selected choice with an empty composer during pending and uncertain requests", async () => {
    setup();
    const fake = learningFake();
    const turn = learningReceipt({
      kind: "turn",
      content: "Explore",
      request_id: "question",
      epoch: 0,
      revision: 0,
    });
    if (!turn.output) throw new Error("Expected successful fixture output");
    turn.output = {
      ...turn.output,
      question: { prompt: "Which direction?", options: ["Music", "Economics"] },
    };
    fake.state.history.items = [turn];
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    let received = false;
    server.use(...fake.handlers);
    server.use(
      http.post(`${base}/operations`, async () => {
        received = true;
        await pending;
        return HttpResponse.error();
      }),
    );
    window.history.pushState({}, "", "/tutor");
    render(<App />);
    fireEvent.click(await screen.findByRole("radio", { name: "Music" }));
    const composer = screen.getByLabelText("Message your Tutor");
    fireEvent.change(composer, { target: { value: "Let me explain" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(received).toBe(true));
    fireEvent.change(composer, { target: { value: "" } });
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    release();
    await screen.findByText(/couldn't reach your Tutor/);
    expect(composer).toHaveValue("");
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });
  it.each(["succeeded", "failed", "uncertain"] as const)(
    "keeps pending text and handles delayed %s acknowledgement",
    async (status) => {
      setup();
      const fake = learningFake();
      server.use(...fake.handlers);
      let release!: () => void;
      const pending = new Promise<void>((resolve) => {
        release = resolve;
      });
      let received = false;
      server.use(
        http.post(`${base}/operations`, async ({ request }) => {
          const body = (await request.json()) as LearningOperationRequest;
          received = true;
          await pending;
          if (status === "uncertain") return HttpResponse.error();
          const receipt = learningReceipt(body, status);
          fake.receipts.set(receipt.id, receipt);
          return HttpResponse.json(receipt);
        }),
      );
      window.history.pushState({}, "", "/tutor");
      render(<App />);
      const composer = await screen.findByLabelText("Message your Tutor");
      fireEvent.change(composer, { target: { value: "Submitted question" } });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
      await waitFor(() => expect(received).toBe(true));
      expect(composer).toHaveValue("Submitted question");
      const waiting = within(screen.getByRole("region", { name: "Learning conversation" }));
      expect(waiting.getByRole("status")).toHaveTextContent(
        "Your Tutor is thinking. You can return here to recover the result.",
      );
      release();
      if (status === "succeeded") await waitFor(() => expect(composer).toHaveValue(""));
      else {
        await screen.findByText(
          status === "failed" ? /Your Tutor couldn't finish/ : /couldn't reach your Tutor/,
        );
        expect(composer).toHaveValue("Submitted question");
      }
    },
  );
  it("preserves typing made while focus Save is pending", async () => {
    const fake = setup();
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    let received = false;
    server.use(
      http.put(`${base}/focus`, async () => {
        received = true;
        await pending;
        fake.state.focus = "Submitted focus";
        fake.state.revision++;
        return HttpResponse.json(fake.state);
      }),
    );
    window.history.pushState({}, "", "/tutor");
    render(<App />);
    const focus = await screen.findByLabelText("Learning focus");
    fireEvent.change(focus, { target: { value: "Submitted focus" } });
    fireEvent.click(screen.getByRole("button", { name: "Save focus" }));
    await waitFor(() => expect(received).toBe(true));
    fireEvent.change(focus, { target: { value: "New unsaved focus" } });
    release();
    await screen.findByText(/Learning focus saved/);
    expect(focus).toHaveValue("New unsaved focus");
  });
  it.each([false, true])(
    "clears successful submitted text but preserves newer typing=%s",
    async (newer) => {
      setup();
      const fake = learningFake();
      server.use(...fake.handlers);
      window.history.pushState({}, "", "/tutor");
      render(<App />);
      const composer = await screen.findByLabelText("Message your Tutor");
      fireEvent.change(composer, { target: { value: "My question" } });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
      if (newer) fireEvent.change(composer, { target: { value: "Next question" } });
      await screen.findByTestId("learning-turn");
      await waitFor(() => expect(composer).toHaveValue(newer ? "Next question" : ""));
    },
  );
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
