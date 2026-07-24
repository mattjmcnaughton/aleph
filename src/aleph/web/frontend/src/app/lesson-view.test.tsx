import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { configureLessons, seedLesson } from "../mocks/lessons";
import { App } from "./app";

// Lesson view (§8, TDD): the Read passage → Quick check → Outcome/explanation →
// Mark complete surface for one lesson, plus its generating / failed / locked
// states. Driven end to end through the real router, TanStack Query polling, and
// MSW (the same seam the path-view + onboarding suites use). Assertions use plain
// vitest matchers to mirror path-view.test.tsx's style.

const PATH_ID = "p1000000-0000-4000-8000-000000000001";

async function gotoLesson(id: string): Promise<HTMLElement> {
  window.history.pushState({}, "", `/lessons/${id}`);
  render(<App />);
  return screen.findByTestId("lesson-view");
}

function options() {
  return screen.getAllByTestId("quick-check-option") as HTMLButtonElement[];
}

describe("Lesson view — /lessons/$lessonId", () => {
  it("[AL-063] renders a ready lesson: title, Read passage, Quick check stem + options", async () => {
    seedLesson({
      id: "les-ready",
      path_id: PATH_ID,
      title: "What TypeScript adds",
      readPassage: "TypeScript is JavaScript with types.",
      stem: "What does TypeScript add?",
      options: ["Static types", "A new runtime", "A CSS framework"],
      correctIndex: 0,
    });
    await gotoLesson("les-ready");

    expect((await screen.findByTestId("lesson-read-passage")).textContent).toMatch(/types/i);
    expect(screen.getByTestId("quick-check-stem").textContent).toMatch(/what does typescript add/i);
    expect(options()).toHaveLength(3);
    expect(screen.getByTestId("lesson-view-id").textContent).toBe("les-ready");
  });

  it("[AL-063][a11y] options are native single-select radios (C4)", async () => {
    seedLesson({ id: "les-radio", path_id: PATH_ID });
    await gotoLesson("les-radio");

    await screen.findByTestId("quick-check-stem");
    // Native radios give real single-select semantics for free.
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios).toHaveLength(3);
    fireEvent.click(radios[1]);
    expect(radios[1].checked).toBe(true);
    expect(radios[0].checked).toBe(false);
  });

  it("[AL-063][a11y] the reveal labels the correct option for assistive tech (C3)", async () => {
    seedLesson({ id: "les-a11y-reveal", path_id: PATH_ID, correctIndex: 2 });
    await gotoLesson("les-a11y-reveal");

    // Pick a wrong option so both prefixes appear.
    fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
    fireEvent.click(screen.getByTestId("quick-check-submit"));

    await screen.findByTestId("outcome-reveal");
    expect(screen.getByText(/^Correct answer:/i)).toBeTruthy();
    expect(screen.getByText(/^Your answer:/i)).toBeTruthy();
    // Options stay keyboard-inspectable after reveal (aria-disabled, not disabled).
    const revealed = screen.getAllByTestId("quick-check-option") as HTMLInputElement[];
    expect(revealed[2].getAttribute("aria-disabled")).toBe("true");
    expect(revealed[2].disabled).toBe(false);
  });

  it("[AL-063] submit is disabled until an option is selected", async () => {
    seedLesson({ id: "les-select", path_id: PATH_ID });
    await gotoLesson("les-select");

    const submit = (await screen.findByTestId("quick-check-submit")) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(options()[1]);
    expect(submit.disabled).toBe(false);
  });

  it("[AL-063][W6] pre-Attempt payload hides the answer: no correct marker, no explanation", async () => {
    seedLesson({
      id: "les-hidden",
      path_id: PATH_ID,
      options: ["Static types", "A new runtime", "A CSS framework"],
      correctIndex: 0,
      explanation: "SECRET_EXPLANATION_TOKEN",
    });
    await gotoLesson("les-hidden");

    await screen.findByTestId("quick-check-stem");
    // No option is marked correct, and the reveal + explanation are absent.
    expect(options().some((o) => o.getAttribute("data-correct") === "true")).toBe(false);
    expect(screen.queryByTestId("outcome-reveal")).toBeNull();
    expect(screen.queryByText(/SECRET_EXPLANATION_TOKEN/)).toBeNull();
  });

  it("[AL-063][W6] a correct Attempt reveals the correct outcome + explanation", async () => {
    seedLesson({
      id: "les-correct",
      path_id: PATH_ID,
      correctIndex: 1,
      explanation: "Because it layers types over JS.",
    });
    await gotoLesson("les-correct");

    fireEvent.click((await screen.findAllByTestId("quick-check-option"))[1]);
    fireEvent.click(screen.getByTestId("quick-check-submit"));

    const reveal = await screen.findByTestId("outcome-reveal");
    expect(reveal.getAttribute("data-outcome")).toBe("correct");
    expect(screen.getByText(/layers types over js/i)).toBeTruthy();
    // The keyed correct option is now marked.
    expect(options()[1].getAttribute("data-correct")).toBe("true");
  });

  it("[AL-063][W6] an incorrect Attempt still reveals correct option + explanation", async () => {
    seedLesson({
      id: "les-wrong",
      path_id: PATH_ID,
      correctIndex: 2,
      explanation: "The correct answer is the third one.",
    });
    await gotoLesson("les-wrong");

    // Pick index 0 (wrong; correct is 2).
    fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
    fireEvent.click(screen.getByTestId("quick-check-submit"));

    const reveal = await screen.findByTestId("outcome-reveal");
    expect(reveal.getAttribute("data-outcome")).toBe("incorrect");
    expect(screen.getByText(/third one/i)).toBeTruthy();
    // The correct option (index 2) is highlighted even though it wasn't chosen.
    expect(options()[2].getAttribute("data-correct")).toBe("true");
  });

  it("[AL-063] revealed-on-return: a non-null attempt renders the reveal with no interaction", async () => {
    seedLesson({
      id: "les-returned",
      path_id: PATH_ID,
      correctIndex: 0,
      attemptSelectedIndex: 0,
      explanation: "Already answered.",
    });
    await gotoLesson("les-returned");

    const reveal = await screen.findByTestId("outcome-reveal");
    expect(reveal.getAttribute("data-outcome")).toBe("correct");
    expect(screen.getByText(/already answered/i)).toBeTruthy();
    // No submit affordance once the Attempt exists.
    expect(screen.queryByTestId("quick-check-submit")).toBeNull();
  });

  it("[AL-063] a generating lesson shows the generating state, then content on resolve", async () => {
    seedLesson({ id: "les-gen", path_id: PATH_ID, pollsRemaining: 1 });
    await gotoLesson("les-gen");

    await screen.findByTestId("lesson-generating");
    // The poll flips it to generated and the content appears (2s cadence).
    await screen.findByTestId("lesson-read-passage", {}, { timeout: 4000 });
    expect(screen.queryByTestId("lesson-generating")).toBeNull();
  });

  it("[AL-063][C1] a lesson stuck generating degrades to a recovery notice with a back link", async () => {
    vi.useFakeTimers();
    try {
      // pollsRemaining far beyond the test window models the documented dead-end:
      // a reachable lesson whose content never resolves (its failed head, on the
      // path, is what needs retrying).
      seedLesson({ id: "les-stuck", path_id: PATH_ID, pollsRemaining: 999 });
      window.history.pushState({}, "", "/lessons/les-stuck");
      render(<App />);

      // Auth gate + first fetch settle → the spinner is up, not yet degraded.
      for (let i = 0; i < 3; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(screen.getByTestId("lesson-generating")).toBeTruthy();
      expect(screen.queryByTestId("lesson-generation-stalled")).toBeNull();

      // Past the stall timeout it degrades to a recovery notice pointing back to
      // the path (where the failed head's retry lives), and polling has stopped.
      for (let i = 0; i < 50; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(screen.getByTestId("lesson-generation-stalled")).toBeTruthy();
      expect(screen.queryByTestId("lesson-generating")).toBeNull();
      expect(screen.getByTestId("lesson-stalled-back").getAttribute("href")).toContain(PATH_ID);
    } finally {
      vi.useRealTimers();
    }
  });

  it("[AL-063][W8] a failed lesson shows an error, retry resumes to content", async () => {
    seedLesson({ id: "les-failed", path_id: PATH_ID, resolution: "failed" });
    await gotoLesson("les-failed");

    const error = await screen.findByTestId("lesson-failed");
    expect(error.getAttribute("data-variant")).toBe("error");
    fireEvent.click(screen.getByTestId("lesson-retry-button"));
    await screen.findByTestId("lesson-read-passage", {}, { timeout: 4000 });
  });

  it("[AL-063][C5][W8] retry restores the 2s poll cadence, not the 5s ceiling", async () => {
    vi.useFakeTimers();
    try {
      seedLesson({ id: "les-retry-cadence", path_id: PATH_ID, resolution: "failed" });
      // One generating poll after the retry, so the cadence — not an instant
      // resolve — is what the assertions pin down.
      configureLessons({ pollsBeforeResolve: 1 });
      window.history.pushState({}, "", "/lessons/les-retry-cadence");
      render(<App />);

      for (let i = 0; i < 3; i++) await vi.advanceTimersByTimeAsync(1000);
      fireEvent.click(screen.getByTestId("lesson-retry-button"));

      // The retry's own reset-driven refetch lands `generating`; content isn't up.
      await vi.advanceTimersByTimeAsync(0);
      expect(screen.queryByTestId("lesson-read-passage")).toBeNull();
      // Just before the 2s tick it is still generating (so the resolve is poll-
      // driven, not instant).
      await vi.advanceTimersByTimeAsync(1900);
      expect(screen.queryByTestId("lesson-read-passage")).toBeNull();
      // The first poll fires at 2s (resetQueries cleared dataUpdateCount) and the
      // content lands — had the cadence resumed at the 5s ceiling this would still
      // be blank here.
      await vi.advanceTimersByTimeAsync(300);
      expect(screen.getByTestId("lesson-read-passage")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("[AL-063][W8] a rate-limited retry surfaces the daily-cap notice, not silence", async () => {
    seedLesson({ id: "les-failed", path_id: PATH_ID, resolution: "failed" });
    configureLessons({ generateRateLimited: true });
    await gotoLesson("les-failed");

    await screen.findByTestId("lesson-failed");
    fireEvent.click(screen.getByTestId("lesson-retry-button"));
    await screen.findByTestId("lesson-retry-ratelimit");
    // Still on the error surface — not a dead-end.
    screen.getByTestId("lesson-failed");
  });

  it("[AL-063] a locked lesson shows a locked notice and no Quick check", async () => {
    seedLesson({ id: "les-locked", path_id: PATH_ID, unlock_state: "locked" });
    await gotoLesson("les-locked");

    await screen.findByTestId("lesson-locked");
    expect(screen.queryByTestId("quick-check")).toBeNull();
    // A link back to the path so the learner never dead-ends.
    expect(screen.getByTestId("lesson-locked-back").getAttribute("href")).toContain(PATH_ID);
  });

  it("[AL-063] Mark complete is hidden until the Quick check is attempted", async () => {
    seedLesson({ id: "les-gate", path_id: PATH_ID });
    await gotoLesson("les-gate");

    await screen.findByTestId("quick-check-stem");
    // Before an Attempt, no Mark complete affordance.
    expect(screen.queryByTestId("lesson-complete-button")).toBeNull();
  });

  it("[AL-063] Mark complete flow posts /complete and shows the completed state", async () => {
    seedLesson({ id: "les-complete", path_id: PATH_ID, correctIndex: 0 });
    await gotoLesson("les-complete");

    // Attempt first (reveal), then Mark complete becomes available.
    fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
    fireEvent.click(screen.getByTestId("quick-check-submit"));
    fireEvent.click(await screen.findByTestId("lesson-complete-button"));

    await screen.findByTestId("lesson-completed");
    expect(screen.queryByTestId("lesson-complete-button")).toBeNull();
    expect(screen.getByTestId("lesson-completed-back").getAttribute("href")).toContain(PATH_ID);
  });

  it("[AL-063][C2] a failed Attempt POST surfaces an inline notice, not silence", async () => {
    seedLesson({ id: "les-attempt-fail", path_id: PATH_ID });
    configureLessons({ attemptFails: true });
    await gotoLesson("les-attempt-fail");

    fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
    fireEvent.click(screen.getByTestId("quick-check-submit"));

    // The error notice appears and the learner can retry (no reveal happened).
    await screen.findByTestId("quick-check-error");
    expect(screen.queryByTestId("outcome-reveal")).toBeNull();
    expect((screen.getByTestId("quick-check-submit") as HTMLButtonElement).disabled).toBe(false);
  });

  it("[AL-063][C2] a failed complete POST surfaces an inline notice, not silence", async () => {
    seedLesson({ id: "les-complete-fail", path_id: PATH_ID, correctIndex: 0 });
    configureLessons({ completeFails: true });
    await gotoLesson("les-complete-fail");

    fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
    fireEvent.click(screen.getByTestId("quick-check-submit"));
    fireEvent.click(await screen.findByTestId("lesson-complete-button"));

    // The error notice appears and the lesson is not marked complete.
    await screen.findByTestId("lesson-complete-error");
    expect(screen.queryByTestId("lesson-completed")).toBeNull();
    expect(screen.getByTestId("lesson-complete-button")).toBeTruthy();
  });

  it("[AL-063][C5] a complete lesson with no Attempt stays answerable (contract §6)", async () => {
    // Completion is orthogonal to the Attempt: a complete lesson still lets the
    // learner answer its Quick check (docs/api.md §6).
    seedLesson({
      id: "les-complete-noattempt",
      path_id: PATH_ID,
      unlock_state: "complete",
      correctIndex: 0,
    });
    await gotoLesson("les-complete-noattempt");

    // Already-complete treatment is shown...
    await screen.findByTestId("lesson-completed");
    // ...yet the Quick check is still present and answerable.
    const submit = await screen.findByTestId("quick-check-submit");
    fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
    expect((submit as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(submit);
    await screen.findByTestId("outcome-reveal");
  });

  it("[AL-063] revisiting a complete lesson shows the completed state", async () => {
    seedLesson({
      id: "les-revisit",
      path_id: PATH_ID,
      unlock_state: "complete",
      attemptSelectedIndex: 0,
      correctIndex: 0,
    });
    await gotoLesson("les-revisit");

    await screen.findByTestId("lesson-completed");
    // The content is still there to revisit, with the reveal shown.
    await screen.findByTestId("lesson-read-passage");
    await screen.findByTestId("outcome-reveal");
  });

  it("[AL-063] a missing lesson shows an unavailable state, no content", async () => {
    await gotoLesson("les-missing-0000");

    await screen.findByTestId("lesson-unavailable", {}, { timeout: 3000 });
    expect(screen.queryByTestId("lesson-read-passage")).toBeNull();
    // The id seam is still present (path-view + e2e depend on it).
    expect(screen.getByTestId("lesson-view-id").textContent).toBe("les-missing-0000");
  });
});
