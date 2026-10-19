import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { TOPIC_MAX_LENGTH } from "../lib/onboarding";
import {
  adminSession,
  adminSessionNoModels,
  adminUser,
  authenticatedSession,
  signedOutSession,
} from "../mocks/handlers";
import { ADMIN_MODEL_ALLOWLIST } from "../mocks/models";
import { configurePaths, createPathBodies } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// Admin model picker on /new (AL-065, TDD §5.3/D14, docs/api.md). Two axes:
// *visibility* (admins only, options straight from `session.user.model_allowlist`)
// and *payload* (chosen ids ride `POST /api/v1/paths` as `model_outline` /
// `model_lesson`, unset slots omitted entirely). Driven through the real router,
// the real mutation, and MSW — the fake records every create body so the tests
// assert what was actually sent rather than what a spy saw.

function useSession(session: AuthSession) {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

async function gotoNewPath() {
  window.history.pushState({}, "", "/new");
  render(<App />);
  return (await screen.findByRole("textbox", { name: /topic/i })) as HTMLInputElement;
}

function selectModel(testid: "model-picker-outline" | "model-picker-lesson", value: string) {
  fireEvent.change(screen.getByTestId(testid), { target: { value } });
}

function optionValues(testid: "model-picker-outline" | "model-picker-lesson"): string[] {
  const select = screen.getByTestId(testid) as HTMLSelectElement;
  return [...select.options].map((option) => option.value);
}

/** The body of the most recent `POST /paths`, or undefined if none was made. */
function lastCreatePathBody(): Record<string, unknown> | undefined {
  const bodies = createPathBodies();
  return bodies[bodies.length - 1];
}

async function submitTopic(topic: string) {
  fireEvent.change(screen.getByRole("textbox", { name: /topic/i }), { target: { value: topic } });
  fireEvent.click(screen.getByRole("button", { name: /build my path/i }));
  await waitFor(() => expect(lastCreatePathBody()).toBeDefined());
}

describe("Admin model picker — /new", () => {
  it("[AL-065] is absent for a non-admin learner, and sends no model fields", async () => {
    await gotoNewPath();

    expect(screen.queryByTestId("model-picker")).toBeNull();
    expect(screen.queryByTestId("model-picker-outline")).toBeNull();
    expect(screen.queryByTestId("model-picker-lesson")).toBeNull();

    await submitTopic("TypeScript generics");

    const body = lastCreatePathBody() ?? {};
    // Absent keys, not null values — the backend 403s a non-admin *override*,
    // so an explicit `model_outline: null` would be a different payload.
    expect(Object.keys(body).sort()).toEqual(["level", "topic"]);
    expect("model_outline" in body).toBe(false);
    expect("model_lesson" in body).toBe(false);
  });

  it("[AL-065] leaves no trace for a signed-out visitor", async () => {
    useSession(signedOutSession);
    window.history.pushState({}, "", "/new");
    render(<App />);

    // The auth gate lands on /login; either way the picker never renders.
    await screen.findByRole("heading", { name: "Sign in to Aleph" });
    expect(screen.queryByTestId("model-picker")).toBeNull();
  });

  it("[AL-065] is absent when an admin's allowlist is empty (nothing to pick)", async () => {
    useSession(adminSessionNoModels);
    await gotoNewPath();

    expect(screen.queryByTestId("model-picker")).toBeNull();
  });

  it("[AL-065] renders both slots for an admin, options straight from the session", async () => {
    useSession(adminSession);
    await gotoNewPath();

    expect(screen.getByTestId("model-picker")).toBeTruthy();
    // Each slot is a labelled control, named for the API field it fills.
    expect(screen.getByLabelText(/outline model/i)).toBe(
      screen.getByTestId("model-picker-outline"),
    );
    expect(screen.getByLabelText(/lesson model/i)).toBe(screen.getByTestId("model-picker-lesson"));
    // Options are the session's allowlist, in the session's order, behind one
    // "server default" entry — never a hardcoded model list.
    const expected = ["", ...ADMIN_MODEL_ALLOWLIST];
    expect(optionValues("model-picker-outline")).toEqual(expected);
    expect(optionValues("model-picker-lesson")).toEqual(expected);
    // Default is unset: the server picks the configured slot model.
    expect((screen.getByTestId("model-picker-outline") as HTMLSelectElement).value).toBe("");
    expect((screen.getByTestId("model-picker-lesson") as HTMLSelectElement).value).toBe("");
  });

  it("[AL-065] renders exactly the ids a narrower allowlist carries", async () => {
    useSession({
      authenticated: true,
      provider: "keycloak",
      user: { ...adminUser, model_allowlist: ["z/last-model", "a/first-model"] },
    });
    await gotoNewPath();

    // Order preserved as served — the picker never sorts or relabels.
    expect(optionValues("model-picker-outline")).toEqual(["", "z/last-model", "a/first-model"]);
  });

  it("[AL-065] sends both chosen models on the create payload", async () => {
    useSession(adminSession);
    await gotoNewPath();

    selectModel("model-picker-outline", "anthropic/claude-opus-4-8");
    selectModel("model-picker-lesson", "anthropic/claude-haiku-4-5");
    await submitTopic("Rust ownership");

    expect(lastCreatePathBody()).toEqual({
      topic: "Rust ownership",
      level: "new_to_it",
      model_outline: "anthropic/claude-opus-4-8",
      model_lesson: "anthropic/claude-haiku-4-5",
    });
  });

  it("[AL-065] omits the slot left unset, sending only the chosen one", async () => {
    useSession(adminSession);
    await gotoNewPath();

    selectModel("model-picker-lesson", "minimax/minimax-m3");
    await submitTopic("SQL indexes");

    const body = lastCreatePathBody() ?? {};
    expect(body.model_lesson).toBe("minimax/minimax-m3");
    expect("model_outline" in body).toBe(false);
    expect(Object.keys(body).sort()).toEqual(["level", "model_lesson", "topic"]);
  });

  it("[AL-065] returning a slot to the default drops it from the payload again", async () => {
    useSession(adminSession);
    await gotoNewPath();

    selectModel("model-picker-outline", "openai/gpt-5.6-terra");
    selectModel("model-picker-outline", "");
    await submitTopic("Graph theory");

    expect("model_outline" in (lastCreatePathBody() ?? {})).toBe(false);
  });

  it("[AL-065] surfaces a 422 off-allowlist model inline, and stays recoverable", async () => {
    useSession(adminSession);
    // The allowlist changed after this session was issued: the id the picker
    // still offers is no longer accepted (docs/api.md — 422 validation_error).
    configurePaths({ modelAllowlist: ["anthropic/claude-sonnet-5"] });
    await gotoNewPath();

    selectModel("model-picker-outline", "anthropic/claude-opus-4-8");
    await submitTopic("Kubernetes operators");

    const error = await screen.findByTestId("model-picker-error");
    expect(error.textContent).toMatch(/model/i);
    // Announced, and tied to the controls it is about — a screen-reader user on
    // either slot hears why their pick came back.
    expect(error.getAttribute("role")).toBe("alert");
    for (const testid of ["model-picker-outline", "model-picker-lesson"] as const) {
      expect(screen.getByTestId(testid).getAttribute("aria-describedby")).toBe(error.id);
    }
    // Not a crash, not the generating state — the form is still standing with
    // the typed topic intact, and the generic error surface stayed out of it.
    expect(screen.queryByTestId("onboarding-generating")).toBeNull();
    expect(screen.queryByTestId("onboarding-error")).toBeNull();
    expect((screen.getByRole("textbox", { name: /topic/i }) as HTMLInputElement).value).toBe(
      "Kubernetes operators",
    );

    // Recoverable: pick an id the server still accepts and the path is created.
    selectModel("model-picker-outline", "anthropic/claude-sonnet-5");
    fireEvent.click(screen.getByRole("button", { name: /build my path/i }));
    await screen.findByTestId("path-view");
    expect(createPathBodies()).toHaveLength(2);
    expect(lastCreatePathBody()?.model_outline).toBe("anthropic/claude-sonnet-5");
  });

  it("[AL-065] refreshes the stale allowlist, and the rejection outlives the slots", async () => {
    // The allowlist was emptied server-side after this session was issued: the
    // create is rejected, and the refreshed session no longer offers anything.
    let sessionRequests = 0;
    server.use(
      http.get(`${API_V1_BASE}/auth/session`, () => {
        sessionRequests += 1;
        return HttpResponse.json(sessionRequests === 1 ? adminSession : adminSessionNoModels);
      }),
    );
    configurePaths({ modelAllowlist: [] });
    await gotoNewPath();

    selectModel("model-picker-outline", "anthropic/claude-opus-4-8");
    await submitTopic("Kubernetes operators");

    await screen.findByTestId("model-picker-error");
    // The rejection invalidates the session, so the picker stops offering ids
    // the server would only reject again — the worst outcome here would be the
    // error vanishing with them, leaving a failed create with nothing said.
    await waitFor(() => expect(screen.queryByTestId("model-picker")).toBeNull());
    expect(screen.getByTestId("model-picker-error")).toBeTruthy();
    expect(screen.queryByTestId("onboarding-error")).toBeNull();
    expect(sessionRequests).toBeGreaterThan(1);
  });

  it("[AL-065] caps the topic at the backend's TopicStr length", async () => {
    useSession(adminSession);
    const topic = await gotoNewPath();

    // The form is the first line of defence, so an over-long topic never
    // becomes a 422 the client has to disambiguate in the first place.
    expect(topic.maxLength).toBe(TOPIC_MAX_LENGTH);
  });

  it("[AL-065] an over-long topic's 422 reads as a generic failure, not a model rejection", async () => {
    useSession(adminSession);
    await gotoNewPath();

    // jsdom does not enforce `maxLength` on a programmatic value change, which
    // is exactly the escape hatch a paste/autofill takes in a real browser: the
    // server rejects it with the same `422 validation_error` an off-allowlist
    // model gets. No model was chosen, so the picker must not claim it.
    await submitTopic("x".repeat(TOPIC_MAX_LENGTH + 1));

    await screen.findByTestId("onboarding-error");
    expect(screen.queryByTestId("model-picker-error")).toBeNull();
    // The picker is still standing (this admin can still pick), just blameless.
    expect(screen.getByTestId("model-picker")).toBeTruthy();
  });

  it("[AL-065] a 422 on a payload with no model fields never reaches the picker copy", async () => {
    useSession(adminSession);
    // Any other validation_error the endpoint might grow: with no model field on
    // the rejected payload, attribution is structural, so it stays generic.
    server.use(
      http.post(`${API_V1_BASE}/paths`, () =>
        HttpResponse.json(
          { error: { code: "validation_error", message: "Something else was invalid." } },
          { status: 422 },
        ),
      ),
    );
    await gotoNewPath();
    // The override replaces the recording fake, so submit directly rather than
    // waiting on a body the fake never sees.
    fireEvent.change(screen.getByRole("textbox", { name: /topic/i }), {
      target: { value: "Category theory" },
    });
    fireEvent.click(screen.getByRole("button", { name: /build my path/i }));

    await screen.findByTestId("onboarding-error");
    expect(screen.queryByTestId("model-picker-error")).toBeNull();
  });

  it("[AL-065] a 403 override rejection falls back to the generic error, not a crash", async () => {
    useSession(adminSession);
    // Unreachable through the UI (the picker is admin-only), but the client must
    // not blow up if the server disagrees about who is an admin.
    server.use(
      http.post(`${API_V1_BASE}/paths`, () =>
        HttpResponse.json(
          { error: { code: "forbidden", message: "Model overrides are admin-only." } },
          { status: 403 },
        ),
      ),
    );
    await gotoNewPath();

    fireEvent.change(screen.getByRole("textbox", { name: /topic/i }), {
      target: { value: "Category theory" },
    });
    fireEvent.click(screen.getByRole("button", { name: /build my path/i }));

    await screen.findByTestId("onboarding-error");
    expect(screen.getByTestId("model-picker")).toBeTruthy();
  });

  it("[AL-065] disappears when the session flips from admin to plain learner", async () => {
    useSession(adminSession);
    await gotoNewPath();
    expect(screen.getByTestId("model-picker")).toBeTruthy();

    // Same surface, same test, next mount under a plain learner: nothing lingers.
    cleanup();
    useSession(authenticatedSession);
    await gotoNewPath();

    expect(screen.queryByTestId("model-picker")).toBeNull();
  });
});
