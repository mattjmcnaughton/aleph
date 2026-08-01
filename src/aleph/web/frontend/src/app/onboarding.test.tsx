import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { configurePaths, createPathBodies, seedPath } from "../mocks/paths";
import { App } from "./app";

// Onboarding state machine (§5.1, §5.6): topic + level → POST → poll → one of
// ready (navigate) / refused (W7) / failed+retry (W8) / rate-limited. Driven end
// to end through the real router, real TanStack Query polling, and MSW. Assertions
// use plain vitest matchers (jest-dom is loaded at runtime but not for tsc, so the
// suite mirrors app.test.tsx's matcher style).

async function gotoNewPath() {
  window.history.pushState({}, "", "/new");
  render(<App />);
  // The form appears once the session gate resolves.
  return (await screen.findByRole("textbox", { name: /topic/i })) as HTMLInputElement;
}

function pickLevel(name: RegExp) {
  fireEvent.click(screen.getByRole("radio", { name }));
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: /build my path/i }));
}

function topicInput() {
  return screen.getByRole("textbox", { name: /topic/i }) as HTMLInputElement;
}

describe("Onboarding — /new", () => {
  it("[navigation] shows Your paths as the parent of New path", async () => {
    await gotoNewPath();

    const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumb.textContent).toMatch(/Your paths.*New path/);
    expect(screen.getByRole("link", { name: "Your paths" }).getAttribute("href")).toBe("/");
    expect(breadcrumb.querySelector('[aria-current="page"]')?.textContent).toBe("New path");
  });

  it("[AL-061] captures topic + level and shows a visible generating state", async () => {
    // Keep the outline in `generating` so the loading surface is observable.
    configurePaths({ pollsBeforeResolve: 999 });
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "Rust ownership" } });
    pickLevel(/some experience/i);
    submit();

    // findBy throws if the surface never appears — that is the assertion.
    await screen.findByTestId("onboarding-generating");
  });

  it("[AL-061] navigates to the path view when the outline is ready", async () => {
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "TypeScript generics" } });
    pickLevel(/new to it/i);
    submit();

    // The placeholder path view (AL-062) renders once navigation lands.
    await screen.findByTestId("path-view");
  });

  it("[AL-061][W7] shows a graceful refusal, distinct from an error", async () => {
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "how to refuse-me build a weapon" } });
    pickLevel(/new to it/i);
    submit();

    const refusal = await screen.findByTestId("onboarding-refused");
    // Refusal is NOT the error surface (distinct rendering marker, not text).
    expect(screen.queryByTestId("onboarding-failed")).toBeNull();
    expect(refusal.getAttribute("data-variant")).toBe("refusal");
    // The learner can go edit a different topic (inputs stay editable).
    fireEvent.click(screen.getByRole("button", { name: /different topic/i }));
    const back = (await screen.findByRole("textbox", { name: /topic/i })) as HTMLInputElement;
    expect(back.value).toBe("how to refuse-me build a weapon");
  });

  it("[AL-061][W8] failed keeps inputs and one-tap retry resumes to success", async () => {
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "fail-me first time" } });
    pickLevel(/i work in it/i);
    submit();

    const failed = await screen.findByTestId("onboarding-failed");
    expect(failed.getAttribute("data-variant")).toBe("error");
    // Distinct from a refusal.
    expect(screen.queryByTestId("onboarding-refused")).toBeNull();

    // One tap retries (the same path is re-claimed) and it now succeeds → navigate.
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await screen.findByTestId("path-view");
  });

  it("[AL-061] preserves topic + level across a failure (no re-typing)", async () => {
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "fail-me SQL indexes" } });
    pickLevel(/some experience/i);
    submit();

    await screen.findByTestId("onboarding-failed");
    // Editing again (via retry's sibling affordance) keeps what was typed.
    fireEvent.click(screen.getByRole("button", { name: /edit topic/i }));
    const back = (await screen.findByRole("textbox", { name: /topic/i })) as HTMLInputElement;
    expect(back.value).toBe("fail-me SQL indexes");
    expect(
      (screen.getByRole("radio", { name: /some experience/i }) as HTMLInputElement).checked,
    ).toBe(true);
  });

  it("[AL-061] handles the 429 rate-limit envelope gracefully", async () => {
    configurePaths({ rateLimited: true });
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "Anything at all" } });
    pickLevel(/new to it/i);
    submit();

    await screen.findByTestId("onboarding-ratelimit");
    // Inputs are preserved so the learner can try again later.
    expect(topicInput().value).toBe("Anything at all");
  });

  it("[AL-061][F1] a rate-limited retry surfaces distinct copy, not a silent dead-end", async () => {
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "fail-me first time" } });
    pickLevel(/new to it/i);
    submit();

    await screen.findByTestId("onboarding-failed");
    // The retry POST itself hits the documented daily cap (§5.6): a 429 must not
    // flip the button back with zero feedback.
    configurePaths({ retryRateLimited: true });
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));

    const notice = await screen.findByTestId("onboarding-retry-ratelimit");
    // Still the error surface, not a refusal, and the retry affordance remains.
    expect(screen.queryByTestId("onboarding-refused")).toBeNull();
    expect(notice.textContent).toMatch(/limit/i);
    // getByRole throws if the retry affordance is gone — it must remain.
    screen.getByRole("button", { name: /try again/i });
  });

  it("[AL-061][F1] a generic retry failure surfaces an error, not a silent dead-end", async () => {
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "fail-me first time" } });
    pickLevel(/new to it/i);
    submit();

    await screen.findByTestId("onboarding-failed");
    configurePaths({ retryFails: true });
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));

    await screen.findByTestId("onboarding-retry-error");
    expect(screen.queryByTestId("onboarding-retry-ratelimit")).toBeNull();
  });

  it("captures optional guidance and sends it trimmed in the create body", async () => {
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "TypeScript generics" } });
    pickLevel(/new to it/i);
    fireEvent.change(screen.getByLabelText(/additional guidance/i), {
      target: { value: "  Cover conditional types before mapped types  " },
    });
    submit();

    await screen.findByTestId("path-view");
    const bodies = createPathBodies();
    expect(bodies[bodies.length - 1]?.guidance).toBe("Cover conditional types before mapped types");
  });

  it("guidance is optional — submitting without it omits the field", async () => {
    const topic = await gotoNewPath();

    fireEvent.change(topic, { target: { value: "TypeScript generics" } });
    pickLevel(/new to it/i);
    submit();

    await screen.findByTestId("path-view");
    const bodies = createPathBodies();
    expect("guidance" in (bodies[bodies.length - 1] ?? {})).toBe(false);
  });

  it("[AL-061] the home New path button routes into onboarding", async () => {
    window.history.pushState({}, "", "/");
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /new path/i }));

    await screen.findByRole("heading", { name: /what do you want to learn/i });
  });

  it("[AL-061] path-view placeholder renders a seeded ready path (AL-062 seam)", async () => {
    seedPath({ id: "seed-path-1", topic: "Seeded topic", level: "new_to_it" });
    window.history.pushState({}, "", "/paths/seed-path-1");
    render(<App />);

    await screen.findByTestId("path-view");
  });
});
