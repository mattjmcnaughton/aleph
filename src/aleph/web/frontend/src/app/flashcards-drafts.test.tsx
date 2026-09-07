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
        position_in_path: 1,
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

/** The second lesson of the two-lesson path `seedNeighbourJourney` builds. */
const LESSON_2_ID = "l9700000-0000-4000-8000-000000000002";

/**
 * A path with two unlocked, generated lessons — the fixture for the desktop
 * prev/next footer (`LessonNav`), which is how a learner moves lesson→lesson
 * without going back through the path view.
 */
function seedNeighbourJourney(): void {
  seedPath({
    id: PATH_ID,
    topic: "TypeScript",
    level: "new_to_it",
    units: [
      {
        ...UNITS[0],
        lessons: [
          ...UNITS[0].lessons,
          {
            id: LESSON_2_ID,
            title: "Conditional types",
            position_in_path: 2,
            generation_state: "generated",
            unlock_state: "available",
          },
        ],
      },
    ],
  });
  seedLesson({ id: LESSON_ID, path_id: PATH_ID, correctIndex: 0 });
  seedLesson({ id: LESSON_2_ID, path_id: PATH_ID, position_in_path: 2, correctIndex: 0 });
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
    // Fired off the lesson *opening* now (AL-400 — the mount effect in
    // `routes/lessons.$lessonId.tsx`), not off the completion below it: by the
    // time this learner reaches Mark complete, drafting has already been
    // running for as long as the lesson took to read (D5 — still
    // non-blocking, mock screen 01's own pin, just earlier).
    expect(flashcardTriggerRequests()).toContain(LESSON_ID);
  });

  it("[AL-400] opening a generated, incomplete lesson fires the trigger, but the drafts block waits for completion", async () => {
    useFlashcardsSession();
    seedJourney();
    // Seeded as already `generated` so a trigger fired at open is a D7 no-op
    // on the backend — this test is about *when* the client fires, not about
    // drafting's own state machine.
    seedFlashcardDraftRun(LESSON_ID, {
      state: "generated",
      cards: [{ id: "d1", front: "What does `extends` mean?", back: "It constrains T." }],
    });

    window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
    render(<App />);

    // The open-time effect (AL-400) fires exactly once — the lesson is
    // generated and unlocked, even though it is not complete yet. The ref
    // holding the lesson id it last fired for is what keeps this at one
    // across re-renders (and StrictMode's double-invoke).
    await screen.findByTestId("lesson-read-passage");
    await waitFor(() =>
      expect(flashcardTriggerRequests().filter((id) => id === LESSON_ID)).toHaveLength(1),
    );

    // The trigger having fired is not the same as the proposal being shown —
    // that still waits for completion (mock screen 01), even though drafting
    // has been running underneath since the lesson opened.
    expect(screen.queryByTestId("draft-list")).toBeNull();

    fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
    fireEvent.click(screen.getByTestId("quick-check-submit"));
    fireEvent.click(await screen.findByTestId("lesson-complete-button"));
    await screen.findByTestId("lesson-completed");

    await screen.findByTestId("draft-list");
  });

  it("[AL-400] moving lesson→lesson through the prev/next footer fires the trigger for the lesson arrived at", async () => {
    useFlashcardsSession();
    seedNeighbourJourney();

    window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
    render(<App />);

    await screen.findByTestId("lesson-read-passage");
    await waitFor(() => expect(flashcardTriggerRequests()).toContain(LESSON_ID));

    // TanStack Router re-renders this route with new params rather than
    // remounting it, so the open-time trigger's guard has to be keyed by
    // lesson id — a per-instance `useRef(false)` latches on the first lesson
    // and every lesson reached this way (the desktop footer and the sidebar
    // outline, the main way through a path) silently skips open-time
    // drafting. Worse, an already-complete lesson reached this way could
    // never draft at all: the poll goes terminal on `not_started` and the
    // completion re-fire can never run for a lesson already complete.
    fireEvent.click(await screen.findByTestId("lesson-nav-next"));

    await waitFor(() => expect(screen.getByTestId("lesson-view-id").textContent).toBe(LESSON_2_ID));
    await waitFor(() => expect(flashcardTriggerRequests()).toContain(LESSON_2_ID));
  });

  it("[Auto-draft off] opening a lesson fires no trigger; the completed lesson offers to draft on request", async () => {
    // Auto-draft (CONTEXT.md: Settings) off: neither the open-time effect
    // (AL-400) nor the completion re-fire may start drafting on its own.
    server.use(
      http.get(`${API_V1_BASE}/auth/session`, () =>
        HttpResponse.json({
          ...flashcardsSession,
          user: { ...flashcardsSession.user, settings: { auto_draft_flashcards: false } },
        }),
      ),
    );
    seedJourney();

    await completeTheLesson();

    // No run was started at open or at completion — the poll answered
    // `not_started` and the block renders the learner's way to ask instead.
    const draftButton = await screen.findByTestId("flashcard-drafts-draft-button");
    expect(flashcardTriggerRequests()).toEqual([]);
    expect(screen.queryByTestId("draft-list")).toBeNull();

    // Asking is the same trigger the open-time effect would have fired. Seeded
    // `generated` first so the fake's D7 claim is a no-op and the poll's next
    // read (the trigger's `onSuccess` invalidation) resolves to cards.
    seedFlashcardDraftRun(LESSON_ID, {
      state: "generated",
      cards: [{ id: "d1", front: "What does `extends` mean?", back: "It constrains T." }],
    });
    fireEvent.click(draftButton);

    await waitFor(() => expect(flashcardTriggerRequests()).toEqual([LESSON_ID]));
    await screen.findByTestId("draft-list");
    expect(screen.queryByTestId("flashcard-drafts-manual")).toBeNull();
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

  it("[D7] a lesson already complete before this visit resumes its drafts", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", units: UNITS });
    // Already complete server-side, as if an earlier session finished it. The
    // mount effect (AL-400) still fires a trigger on this open — the lesson
    // reads generated + unlocked regardless of completion — but the seeded
    // run is already `generated`, so D7 makes that fire a structural no-op:
    // this is a "resumes without re-drafting" test, not a "no trigger sent"
    // one.
    seedLesson({ id: LESSON_ID, path_id: PATH_ID, correctIndex: 0, unlock_state: "complete" });
    seedFlashcardDraftRun(LESSON_ID, {
      state: "generated",
      cards: [{ id: "d1", front: "Card one", back: "Back one" }],
    });

    window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
    render(<App />);

    await screen.findByTestId("draft-list");
    // The property the title names, actually asserted: the open-time trigger
    // did fire, and D7 made it a no-op — the seeded card is still the one on
    // screen, not a re-drafted replacement, and exactly one `POST` was sent.
    expect(flashcardTriggerRequests().filter((id) => id === LESSON_ID)).toHaveLength(1);
    expect(screen.getAllByTestId("draft-card")).toHaveLength(1);
    expect(screen.getByTestId("draft-list").textContent).toContain("Card one");
  });

  it("[§5.6] a failed run is retried automatically when the lesson is reopened", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", units: UNITS });
    // Already complete, with a prior drafting attempt that failed — the state
    // a returning learner would find it in, no completion mutation involved.
    seedLesson({ id: LESSON_ID, path_id: PATH_ID, correctIndex: 0, unlock_state: "complete" });
    seedFlashcardDraftRun(LESSON_ID, { state: "failed", cards: [] });

    window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
    render(<App />);

    // AL-400: the mount effect fires on every generated+unlocked open
    // regardless of the run's prior state — D7 makes a `failed` run
    // re-claimable, so simply reopening this lesson is itself the retry; the
    // learner never has to see the failed card, let alone tap anything.
    // (`DraftList`'s own manual retry button — the affordance for a failure
    // *during* the current visit, §5.6 — is untouched and covered directly
    // in `draft-list.test.tsx`.)
    await screen.findByTestId("flashcard-drafts-generating");
    expect(flashcardTriggerRequests()).toContain(LESSON_ID);
  });

  it("[§5.6] a run that fails during the visit offers a manual retry that restarts the stopped poll", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", units: UNITS });
    // Complete, with no run yet: the open-time trigger claims one, and it is
    // that in-visit run which then fails. This is the one route the mount
    // effect cannot rescue — it already fired for this lesson — so the
    // manual affordance is the only way out, and the chain it depends on
    // lives here in the route, not in `DraftList`: retry button →
    // `triggerDraftsMutation.mutate()` → `onSuccess` →
    // `invalidateQueries(flashcardDraftsQueryKey)` → a poll that had gone
    // *terminal* on `failed` starts again and reaches `generating`.
    // `draft-list.test.tsx` only asserts the button calls its `onRetry`
    // prop; it cannot catch a dropped `invalidateQueries` nudge, which is
    // the dead-spinner regression this pins.
    seedLesson({ id: LESSON_ID, path_id: PATH_ID, correctIndex: 0, unlock_state: "complete" });

    window.history.pushState({}, "", `/lessons/${LESSON_ID}`);
    render(<App />);

    await screen.findByTestId("flashcard-drafts-generating", undefined, { timeout: 10_000 });
    expect(flashcardTriggerRequests().filter((id) => id === LESSON_ID)).toHaveLength(1);

    // The claimed run fails while the learner is sitting on the page; the
    // still-live poll is what surfaces it.
    seedFlashcardDraftRun(LESSON_ID, { state: "failed", cards: [] });
    await screen.findByTestId("flashcard-drafts-failed", undefined, { timeout: 10_000 });

    fireEvent.click(screen.getByTestId("flashcard-drafts-retry"));

    await screen.findByTestId("flashcard-drafts-generating", undefined, { timeout: 10_000 });
    expect(flashcardTriggerRequests().filter((id) => id === LESSON_ID)).toHaveLength(2);
  }, 30_000);

  it("[BLOCKER, finding 1] a trigger that claims no run stops polling at `not_started`", async () => {
    useFlashcardsSession();
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", units: UNITS });
    // Already complete, and — deliberately — no `seedFlashcardDraftRun` call:
    // the run row is sparse (D7), so an untriggered lesson is exactly "never
    // claimed a run", the real backend's `200 {state: "not_started", cards:
    // []}`. Before AL-400, a completed lesson landed here just by being
    // unseeded; now the mount effect fires a trigger on every generated,
    // unlocked open, so this scenario needs a trigger that genuinely claims
    // nothing — a capped (`429`) one is the honest way to reach it: the
    // learner did everything right, but the daily cap denies the claim, and
    // the poll is left exactly where finding 1's regression pins it — stuck
    // at `not_started`, not looping forever.
    configureFlashcards({ triggerDraftsError: "rate_limited" });
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
