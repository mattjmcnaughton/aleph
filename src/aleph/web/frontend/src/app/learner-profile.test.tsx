import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { configureAccount, storedAboutYou } from "../mocks/account";
import { learnerUser } from "../mocks/handlers";
import { createPathBodies } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// About you (docs/CONTEXT.md) and the Learning summary, end to end through the
// real router, TanStack Query polling and the MSW fakes.

function summaryOn() {
  const session: AuthSession = {
    authenticated: true,
    provider: "keycloak",
    user: { ...learnerUser, feature_flags: { learning_summary: true } },
  };
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

function goto(url: string) {
  window.history.pushState({}, "", url);
  render(<App />);
}

describe("About you", () => {
  it("saves, then clears, the account's note", async () => {
    goto("/settings/about-you");
    const input = (await screen.findByTestId("about-you-input")) as HTMLTextAreaElement;
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "  I'm a nurse. Clinical examples.  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByTestId("about-you-saved");
    expect(storedAboutYou()).toBe("I'm a nurse. Clinical examples.");

    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(storedAboutYou()).toBeNull());
  });

  it("is named on New path when set", async () => {
    configureAccount({ aboutYou: "Use clinical examples where they fit." });
    goto("/new");
    fireEvent.click(await screen.findByRole("button", { name: "I'll fill in the form myself" }));
    const applies = await screen.findByTestId("about-you-applies");
    expect(applies.textContent).toContain("About you applies");
    expect(applies.textContent).toContain("Use clinical examples where they fit.");
  });
});

describe("New path prefill", () => {
  it("seeds topic, level, guidance and teaching style from the URL, and submits them", async () => {
    goto(
      "/new?topic=Async%20Rust&level=work_in_it&guidance=Skip%20basics&teaching_style=Code%20first",
    );
    const topic = (await screen.findByRole("textbox", { name: /topic/i })) as HTMLInputElement;
    expect(topic.value).toBe("Async Rust");
    expect((screen.getByRole("radio", { name: /I work in it/i }) as HTMLInputElement).checked).toBe(
      true,
    );

    fireEvent.click(screen.getByRole("button", { name: /build my path/i }));

    await waitFor(() => expect(createPathBodies()).toHaveLength(1));
    expect(createPathBodies()[0]).toMatchObject({
      topic: "Async Rust",
      level: "work_in_it",
      guidance: "Skip basics",
      teaching_style: "Code first",
    });
  });

  it("ignores an unknown level", async () => {
    goto("/new?level=expert");
    // An invalid level is no prefill at all, so the page opens on the idea step.
    fireEvent.click(await screen.findByRole("button", { name: "I'll fill in the form myself" }));
    await screen.findByRole("textbox", { name: /topic/i });
    expect((screen.getByRole("radio", { name: /New to it/i }) as HTMLInputElement).checked).toBe(
      true,
    );
  });
});

describe("Learning summary", () => {
  it("writes on request, polls to ready, and links a suggestion to a prefilled New path", async () => {
    summaryOn();
    configureAccount({ pollsBeforeReady: 0 });
    goto("/learning");

    fireEvent.click(await screen.findByTestId("summary-write"));
    await screen.findByTestId("summary-generating");

    const rows = await screen.findAllByTestId("summary-suggestion", {}, { timeout: 5000 });
    expect(rows).toHaveLength(3);
    // Counted facts, not model text.
    expect(screen.getByTestId("summary-facts").textContent).toContain("8lessons complete");

    const start = within(rows[0]).getByTestId("summary-start");
    const href = new URL(start.getAttribute("href") ?? "", "http://x");
    expect(href.pathname).toBe("/new");
    expect(href.searchParams.get("topic")).toBe("Async Rust: borrows across .await");
    expect(href.searchParams.get("level")).toBe("some_experience");
    expect(href.searchParams.get("teaching_style")).toBe("Worked example first, then the rule.");

    // A Beat suggestion opens New beat, never New path.
    const beat = within(rows[2]).getByRole("link", { name: "Deploy analyst" });
    expect(beat.getAttribute("href")).toMatch(/^\/beats\/new\?/);
  }, 10_000);

  it("shows too early and failed as their own states", async () => {
    summaryOn();
    configureAccount({
      summary: {
        state: "too_early",
        facts: { lessons_complete: 1, paths: 1, beats: 0 },
        summary: null,
        written_at: null,
        min_lessons_complete: 3,
      },
    });
    goto("/learning");
    expect((await screen.findByTestId("summary-too-early")).textContent).toContain("After 3");
    expect(screen.queryByTestId("summary-write")).toBeNull();
  });

  it("reports a failed run with a retry", async () => {
    summaryOn();
    configureAccount({ failRun: true, pollsBeforeReady: 0 });
    goto("/learning");
    fireEvent.click(await screen.findByTestId("summary-write"));
    const failed = await screen.findByTestId("summary-failed", {}, { timeout: 5000 });
    expect(within(failed).getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("is hidden when the flag is off", async () => {
    goto("/learning");
    expect(await screen.findByText("This page isn't available.")).toBeTruthy();
  });
});
