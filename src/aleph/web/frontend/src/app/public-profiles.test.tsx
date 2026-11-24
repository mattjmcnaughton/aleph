import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { learnerUser, signedOutSession } from "../mocks/handlers";
import { createPathBodies, seedPath } from "../mocks/paths";
import {
  AI_BEAT_ID,
  AI_BRIEF_ID,
  RUST_PATH_ID,
  configurePublicSettings,
  publicSettingsBodies,
  seedPublicWorld,
} from "../mocks/public";
import { server } from "../mocks/server";
import { App } from "./app";

// Public profiles (TDD §6, §9), end to end through the real router and the
// MSW fakes: anonymous reads, the Copy prefill, and the owner settings.

function useSession(session: AuthSession) {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

function profilesOn() {
  useSession({
    authenticated: true,
    provider: "keycloak",
    user: { ...learnerUser, feature_flags: { public_profiles: true } },
  });
}

function goto(url: string) {
  window.history.pushState({}, "", url);
  render(<App />);
}

describe("public pages, signed out", () => {
  it("shows the profile with the public header and the invitation", async () => {
    useSession(signedOutSession);
    seedPublicWorld();
    goto("/@matt");
    expect(await screen.findByRole("heading", { name: "Matt McNaughton" })).toBeVisible();
    expect(screen.getByTestId("public-header")).toBeVisible();
    expect(screen.getAllByTestId("public-path-row")).toHaveLength(3);
    expect(screen.getByTestId("join-footer")).toBeVisible();
    expect(window.location.pathname).toBe("/@matt");
  });

  it("offers sign-in with a typed copy intent on a shared path, never a URL", async () => {
    useSession(signedOutSession);
    seedPublicWorld();
    goto(`/@matt/paths/${RUST_PATH_ID}`);
    const card = await screen.findByTestId("copy-card");
    const link = within(card).getByRole("link", { name: "Sign in to start this path" });
    expect(link.getAttribute("href")).toBe(`/auth/login?copy=path:matt:${RUST_PATH_ID}`);
    // Guidance is plain text, the outline shows titles only.
    expect(screen.getByTestId("public-path-inputs").textContent).toContain("I write Go at work");
    expect(screen.getByText("Matt is here")).toBeVisible();
  });

  it("renders every absent case as the same page", async () => {
    useSession(signedOutSession);
    goto("/@nobody");
    expect(await screen.findByTestId("public-not-here")).toBeVisible();
  });

  it("renders a Brief with Mermaid as code and an unsafe Source as text", async () => {
    useSession(signedOutSession);
    seedPublicWorld();
    goto(`/@matt/beats/${AI_BEAT_ID}/briefs/${AI_BRIEF_ID}`);
    const body = await screen.findByTestId("public-brief-body");
    expect(body.querySelector("pre")?.textContent).toContain("graph LR");
    const sources = screen.getAllByTestId("brief-source");
    expect(within(sources[0]).getByRole("link").getAttribute("href")).toBe(
      "https://example.com/continuous-batching",
    );
    expect(within(sources[2]).queryByRole("link")).toBeNull();
    expect(sources[2].textContent).toContain("javascript:alert(1)");
  });
});

describe("Copy prefill", () => {
  it("fills empty fields from the shared path, keeps typed ones, and credits on submit", async () => {
    seedPublicWorld();
    goto(`/new?from=matt/${RUST_PATH_ID}&topic=My%20own%20topic`);
    await screen.findByTestId("copy-from-notice");
    const topic = screen.getByRole("textbox", { name: /topic/i }) as HTMLInputElement;
    expect(topic.value).toBe("My own topic");
    const guidance = screen.getByRole("textbox", { name: /guidance/i }) as HTMLTextAreaElement;
    await waitFor(() => expect(guidance.value).toContain("I write Go at work"));

    fireEvent.click(screen.getByRole("button", { name: /build my path/i }));
    await waitFor(() => expect(createPathBodies()).toHaveLength(1));
    expect(createPathBodies()[0]).toMatchObject({
      topic: "My own topic",
      level: "some_experience",
      source_path_id: RUST_PATH_ID,
    });
  });

  it("opens an empty form with a note when the source is gone, and never submits", async () => {
    goto(`/new?from=matt/${RUST_PATH_ID}`);
    expect(await screen.findByTestId("copy-from-missing")).toBeVisible();
    expect((screen.getByRole("textbox", { name: /topic/i }) as HTMLInputElement).value).toBe("");
    expect(createPathBodies()).toHaveLength(0);
  });
});

describe("Settings → Public profile", () => {
  it("picks a handle, turns the profile on, and shares a path", async () => {
    profilesOn();
    seedPath({ id: "p-1", topic: "Rust ownership", level: "new_to_it" });
    goto("/settings/public-profile");
    fireEvent.click(await screen.findByTestId("public-profile-switch"));
    expect(screen.getByTestId("public-profile-note").textContent).toContain("Sharing is live");
    fireEvent.change(screen.getByTestId("public-handle-input"), { target: { value: "Matt" } });
    fireEvent.click(screen.getByRole("button", { name: "Turn on my profile" }));

    expect(await screen.findByTestId("public-profile-link")).toHaveTextContent("matt");
    const bodies = publicSettingsBodies();
    expect(bodies[bodies.length - 1]).toMatchObject({ handle: "matt", profile_public: true });

    const row = await screen.findByTestId("share-path");
    const box = within(row).getByRole("checkbox") as HTMLInputElement;
    fireEvent.click(box);
    await waitFor(() => expect(box.checked).toBe(true));
  });

  it("says when a handle is taken", async () => {
    profilesOn();
    configurePublicSettings({ takenHandles: ["matt"] });
    goto("/settings/public-profile");
    fireEvent.click(await screen.findByTestId("public-profile-switch"));
    fireEvent.change(screen.getByTestId("public-handle-input"), { target: { value: "matt" } });
    fireEvent.click(screen.getByRole("button", { name: "Turn on my profile" }));
    expect(await screen.findByTestId("public-handle-error")).toHaveTextContent("taken");
  });
});
