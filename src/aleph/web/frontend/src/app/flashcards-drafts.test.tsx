import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_V1_BASE, type AuthSession, type PathUnit } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import {
  configureFlashcards,
  flashcardDraftsPollRequestCount,
  flashcardKeepRequests,
  flashcardTriggerRequests,
  seedFlashcardDraftRun,
} from "../mocks/flashcards";
import { seedLesson } from "../mocks/lessons";
import { seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// The drafts block below a lesson's completion state (PRD §3, Phase 3 TDD
// D5/§5.2/§8) — driven end to end (`completion-refresh.test.tsx`'s own seam):
// completing a lesson fires the trigger, the poll resolves against the fake's
// drafting run, and the keep/skip actions post exactly what the learner chose.

const flashcardsSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { flashcards: true } },
};

function useFlashcardsSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(flashcardsSession)));
}

const PATH_ID = "p9700000-0000-4000-8000-000000000001";
const LESSON_ID = "l9700000-0000-4000-8000-000000000001";

const UNITS: PathUnit[] = [
  {
    id: "u9700000-0000-4000-8000-000000000001",
    title: "Foundations & types",
    lessons: [
      {
        id: LESSON_ID,
        title: "Generic constraints",
        position_in_path: 0,
        generation_state: "generated",
        unlock_state: "available",
      },
    ],
  },
];

function seedJourney(): void {
  seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", units: UNITS });
  seedLesson({ id: LESSON_ID, path_id: PATH_ID, correctIndex: 0 });
}

/** Answer the Quick check and mark the lesson complete (`completion-refresh.test.tsx`'s `workTheLesson`). */
async function completeTheLesson(): Promise<void> {
  window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
  render(<App />);
  fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
  fireEvent.click(screen.getByTestId("quick-check-submit"));
  fireEvent.click(await screen.findByTestId("lesson-complete-button"));
  await screen.findByTestId("lesson-completed");
}

describe("Flashcards — drafting below a lesson's completion (PRD §3, Phase 3 TDD D5/§8)", () => {
  it("[W24] completing a lesson triggers drafting and the drafts render, all kept by default", async () => {
    useFlashcardsSession();
    seedJourney();
    seedFlashcardDraftRun(LESSON_ID, {
      state: "generated",
      cards: [
        { id: "d1", front: "What does `extends` mean?", back: "It constrains T." },
        { id: "d2", front: "Why constrain a generic?", back: "So the body can rely on it." },
      ],
    });

    await completeTheLesson();

    await screen.findByTestId("draft-list");
    expect(screen.getAllByTestId("draft-card")).toHaveLength(2);
    expect(screen.getByTestId("draft-keep-button").textContent).toBe("Keep 2 cards");
    // Fired off the completion itself (D5 — non-blocking, below the already-
    // recorded completion, mock screen 01's own pin).
    expect(flashcardTriggerRequests()).toContain(LESSON_ID);
  });

  it("keeping some of the drafts posts exactly those ids and the block clears", async () => {
    useFlashcardsSession();
    seedJourney();
    seedFlashcardDraftRun(LESSON_ID, {
      state: "generated",
      cards: [
        { id: "d1", front: "Card one", back: "Back one" },
        { id: "d2", front: "Card two", back: "Back two" },
        { id: "d3", front: "Card three", back: "Back three" },
      ],
    });

    await completeTheLesson();
    await screen.findByTestId("draft-list");

    fireEvent.click(screen.getAllByTestId("draft-toggle")[2]); // discard "Card three"
    fireEvent.click(screen.getByTestId("draft-keep-button"));

    await waitFor(() => expect(screen.queryByTestId("draft-list")).toBeNull());
    expect(flashcardKeepRequests()).toEqual([
      { lesson_id: LESSON_ID, kept_ids: ["d1", "d2"], tz_offset_minutes: expect.any(Number) },
    ]);
  });

  it("[PRD §3] 'Skip — keep none' discards every draft in one tap", async () => {
    useFlashcardsSession();
    seedJourney();
    seedFlashcardDraftRun(LESSON_ID, {
      state: "generated",
      cards: [{ id: "d1", front: "Card one", back: "Back one" }],
    });

    await completeTheLesson();
    await screen.findByTestId("draft-list");
    fireEvent.click(screen.getByTestId("draft-skip-button"));

    await waitFor(() => expect(screen.queryByTestId("draft-list")).toBeNull());
    expect(flashcardKeepRequests()).toEqual([
      { lesson_id: LESSON_ID, kept_ids: [], tz_offset_minutes: expect.any(Number) },
    ]);
  });

  it("[D7] a lesson already complete before this visit resumes its drafts, no new trigger needed", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", units: UNITS });
    // Already complete server-side, as if an earlier session finished it.
    seedLesson({ id: LESSON_ID, path_id: PATH_ID, correctIndex: 0, unlock_state: "complete" });
    seedFlashcardDraftRun(LESSON_ID, {
      state: "generated",
      cards: [{ id: "d1", front: "Card one", back: "Back one" }],
    });

    window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
    render(<App />);

    await screen.findByTestId("draft-list");
  });

  it("[§5.6] a failed run offers a retry, not a dead spinner", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", units: UNITS });
    // Already complete, with a prior drafting attempt that failed — the state
    // a returning learner would find it in, no completion mutation involved.
    seedLesson({ id: LESSON_ID, path_id: PATH_ID, correctIndex: 0, unlock_state: "complete" });
    seedFlashcardDraftRun(LESSON_ID, { state: "failed", cards: [] });

    window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
    render(<App />);
    await screen.findByTestId("flashcard-drafts-failed");

    fireEvent.click(screen.getByTestId("flashcard-drafts-retry"));

    // The re-claim (D7's `WHERE state = 'failed'` arm) — a genuine retry, not
    // a dead spinner: the failed card is gone, replaced by the generating one.
    await screen.findByTestId("flashcard-drafts-generating");
    expect(flashcardTriggerRequests()).toContain(LESSON_ID);
  });

  it("[BLOCKER, finding 1] a completed lesson with no draft run ever triggered stops polling at `not_started`", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", units: UNITS });
    // Already complete, and — deliberately — no `seedFlashcardDraftRun` call:
    // the run row is sparse (D7), so an unseeded lesson is exactly "never
    // triggered", the real backend's `200 {state: "not_started", cards: []}`.
    // This is the state of *every* already-completed lesson the moment the
    // flag flips on (finding 1's own framing) — not an edge case.
    seedLesson({ id: LESSON_ID, path_id: PATH_ID, correctIndex: 0, unlock_state: "complete" });

    vi.useFakeTimers();
    try {
      window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
      render(<App />);

      // Let the auth gate, the initial fetch, and a couple of settle-ticks land.
      for (let i = 0; i < 3; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(screen.getByTestId("lesson-completed")).toBeTruthy();
      expect(screen.queryByTestId("draft-list")).toBeNull();
      expect(screen.queryByTestId("flashcard-drafts-generating")).toBeNull();
      const settledCount = flashcardDraftsPollRequestCount(LESSON_ID);
      expect(settledCount).toBeGreaterThan(0);

      // Walk far past several would-be 5s polls. Before `isFlashcardDraftsTerminal`
      // learned `not_started` (the BLOCKER bug), this request count grew without
      // bound here; a poll correctly terminal at `not_started` holds it exactly
      // where it landed above.
      for (let i = 0; i < 30; i++) await vi.advanceTimersByTimeAsync(1000);
      expect(flashcardDraftsPollRequestCount(LESSON_ID)).toBe(settledCount);
    } finally {
      vi.useRealTimers();
    }
  });

  it("[§5.6, finding 3] a capped trigger (429) says drafting is unavailable today, not silence", async () => {
    useFlashcardsSession();
    seedJourney();
    configureFlashcards({ triggerDraftsError: "rate_limited" });

    await completeTheLesson();

    // The completion stands (already asserted by `completeTheLesson`'s own
    // `lesson-completed` wait); the drafts block says why nothing came of it,
    // rather than reading as "this lesson produced no cards".
    const notice = await screen.findByTestId("flashcard-drafts-trigger-error");
    expect(notice.textContent).toMatch(/unavailable today/i);
    expect(screen.queryByTestId("draft-list")).toBeNull();
    expect(flashcardTriggerRequests()).toContain(LESSON_ID);
  });

  it("[ticket 2/5] a keep that fails server-side shows the existing retry notice, not silence", async () => {
    useFlashcardsSession();
    seedJourney();
    seedFlashcardDraftRun(LESSON_ID, {
      state: "generated",
      cards: [{ id: "d1", front: "Card one", back: "Back one" }],
    });
    configureFlashcards({ keepFails: true });

    await completeTheLesson();
    await screen.findByTestId("draft-list");
    fireEvent.click(screen.getByTestId("draft-keep-button"));

    await screen.findByTestId("draft-keep-error");
    // The block stays put — a failed keep never silently clears the drafts.
    screen.getByTestId("draft-list");
  });

  it("[TDD §5.6, coverage gap] a failed due-summary fails as decoration here too, not just on home", async () => {
    useFlashcardsSession();
    seedJourney();
    server.use(
      http.get(`${API_V1_BASE}/reviews/summary`, () =>
        HttpResponse.json(
          { error: { code: "internal_error", message: "Something went wrong." } },
          { status: 500 },
        ),
      ),
    );

    window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
    render(<App />);

    // The lesson view — the product — is unaffected; only the pill (mounted
    // on every route, TDD §5.6's last row) is decoration here.
    await screen.findByTestId("lesson-read-passage");
    expect(screen.queryByTestId("review-pill")).toBeNull();
  });
});
