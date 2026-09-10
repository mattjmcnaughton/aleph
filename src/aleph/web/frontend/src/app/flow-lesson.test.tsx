import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API_V1_BASE, type AuthSession, type PathUnit } from "../lib/api";
import type { FlowRecord } from "../lib/flow";
import { readFlow, writeFlow } from "../lib/flow";
import { learnerUser } from "../mocks/handlers";
import { seedFlashcardDraftRun } from "../mocks/flashcards";
import { lessonGetRequestCount, seedLesson } from "../mocks/lessons";
import { seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// The lesson route inside a flow (flow TDD §5.3/§5.4/§5.7, §9, mock screen
// 03) — driven end to end through the real router, TanStack Query, and the
// paths/lessons fakes. Records are seeded directly with `writeFlow`, matching
// how a real record would look mid-flow, rather than driven through the
// setup sheet (that path is `flow-setup.test.tsx`'s own coverage).

function useFlowSession(extra: Record<string, boolean> = {}): void {
  const session: AuthSession = {
    authenticated: true,
    provider: "keycloak",
    user: { ...learnerUser, feature_flags: { flow: true, ...extra } },
  };
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

const PATH_A = "flow-path-a";
const PATH_B = "flow-path-b";
const A = ["flow-a-1", "flow-a-2", "flow-a-3"];
const B = ["flow-b-1", "flow-b-2", "flow-b-3"];

function unitsAt(pathId: string, topic: string, ids: string[], completedCount: number): PathUnit[] {
  return [
    {
      id: `${pathId}-u1`,
      title: "Unit",
      lessons: ids.map((id, i) => ({
        id,
        title: `${topic} lesson ${i + 1}`,
        position_in_path: i + 1,
        generation_state: "generated",
        unlock_state:
          i < completedCount ? "complete" : i === completedCount ? "available" : "locked",
      })),
    },
  ];
}

/** Keep the paths store and the lessons store in agreement (neither fake
 *  updates the other on its own — `completion-refresh.test.tsx`'s own
 *  precedent for why this sync has to be spelled out by hand). */
function applyProgress(pathId: string, topic: string, ids: string[], completedCount: number): void {
  seedPath({
    id: pathId,
    topic,
    level: "new_to_it",
    units: unitsAt(pathId, topic, ids, completedCount),
  });
  ids.forEach((id, i) => {
    seedLesson({
      id,
      path_id: pathId,
      position_in_path: i + 1,
      title: `${topic} lesson ${i + 1}`,
      correctIndex: 0,
      unlock_state: i < completedCount ? "complete" : i === completedCount ? "available" : "locked",
      ...(i < completedCount ? { attemptSelectedIndex: 0 } : {}),
    });
  });
}

function wireCompletionSync(pathId: string, topic: string, ids: string[]): void {
  ids.forEach((id, i) => {
    server.use(
      http.post(`${API_V1_BASE}/lessons/${id}/complete`, () => {
        applyProgress(pathId, topic, ids, i + 1);
        return HttpResponse.json({
          id,
          unlock_state: "complete",
          path_completion:
            i === ids.length - 1
              ? {
                  lesson_count: ids.length,
                  first_completed_at: "2026-01-01T00:00:00Z",
                  completed_at: "2026-01-01T00:05:00Z",
                }
              : null,
        });
      }),
    );
  });
}

/** Two three-lesson paths, both fresh, wired so completing a lesson advances
 *  both fakes together and unlocks the next one in the same path. */
function seedTwoPathFlow(): void {
  applyProgress(PATH_A, "Path A", A, 0);
  applyProgress(PATH_B, "Path B", B, 0);
  wireCompletionSync(PATH_A, "Path A", A);
  wireCompletionSync(PATH_B, "Path B", B);
}

function baseRecord(overrides: Partial<FlowRecord>): FlowRecord {
  return {
    version: 1,
    startedAt: "2026-01-01T00:00:00.000Z",
    length: 3,
    order: "interleave",
    paths: [],
    cursor: 0,
    current: null,
    next: null,
    completed: [],
    endedReason: null,
    ...overrides,
  };
}

/** Answer the Quick check and mark the open lesson complete. */
async function workTheLesson(): Promise<void> {
  fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
  fireEvent.click(screen.getByTestId("quick-check-submit"));
  fireEvent.click(await screen.findByTestId("lesson-complete-button"));
}

afterEach(() => {
  vi.useRealTimers();
});

describe("Lesson route in a flow", () => {
  it("the bar is position-based, drafts are suppressed, and the advance card opens at 5", async () => {
    useFlowSession({ flashcards: true });
    seedTwoPathFlow();
    seedFlashcardDraftRun(A[0], {
      state: "generated",
      cards: [{ id: "c1", front: "Front", back: "Back" }],
    });
    writeFlow(
      baseRecord({
        paths: [PATH_A, PATH_B],
        cursor: 0,
        current: { lessonId: A[0], pathId: PATH_A },
        next: { lessonId: B[0], pathId: PATH_B },
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);

    // Position, not `completed.length` (fix plan item 3): the very first
    // lesson is "1 of 3", never "0 of 3".
    const bar = await screen.findByTestId("flow-bar");
    expect(bar.textContent).toContain("Flow · 1 of 3");

    await workTheLesson();

    await screen.findByTestId("flow-advance");
    expect(screen.getByTestId("flow-advance-count").textContent).toBe("5");
    // Generated for this very lesson, yet nothing renders: the batch waits
    // for the receipt (D7) instead of interrupting the flow.
    expect(screen.queryByTestId("draft-list")).toBeNull();
    // Still "1 of 3": the advance screen is still position 1 — position only
    // moves to "2 of 3" once the learner is actually on lesson 2.
    expect(screen.getByTestId("flow-bar").textContent).toContain("Flow · 1 of 3");
  });

  it("'Go now' skips the count and opens the next lesson immediately", async () => {
    useFlowSession();
    seedTwoPathFlow();
    writeFlow(
      baseRecord({
        paths: [PATH_A, PATH_B],
        cursor: 0,
        current: { lessonId: A[0], pathId: PATH_A },
        next: { lessonId: B[0], pathId: PATH_B },
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);
    await workTheLesson();
    await screen.findByTestId("flow-advance");

    fireEvent.click(screen.getByTestId("flow-advance-go"));

    expect((await screen.findByTestId("lesson-view-id")).textContent).toBe(B[0]);
    expect(readFlow()?.current).toEqual({ lessonId: B[0], pathId: PATH_B });
  });

  it("[fake timers] the count reaches zero on its own, and Pause genuinely holds it", async () => {
    useFlowSession();
    seedTwoPathFlow();
    writeFlow(
      baseRecord({
        paths: [PATH_A, PATH_B],
        cursor: 0,
        current: { lessonId: A[0], pathId: PATH_A },
        next: { lessonId: B[0], pathId: PATH_B },
      }),
    );

    vi.useFakeTimers();
    try {
      window.history.pushState({}, "", `/lessons/${A[0]}`);
      render(<App />);

      // Let the auth/session + lesson fetch settle (both resolve on their own
      // microtask ticks; `advanceTimersByTimeAsync(0)` is what flushes them
      // under fake timers — `lesson-view.test.tsx`'s own precedent).
      for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(200);
      expect(screen.getByTestId("lesson-read-passage")).toBeTruthy();

      fireEvent.click(screen.getAllByTestId("quick-check-option")[0]);
      fireEvent.click(screen.getByTestId("quick-check-submit"));
      await vi.advanceTimersByTimeAsync(0);
      fireEvent.click(screen.getByTestId("lesson-complete-button"));
      await vi.advanceTimersByTimeAsync(0);

      expect(screen.getByTestId("flow-advance-count").textContent).toBe("5");

      await vi.advanceTimersByTimeAsync(2000);
      expect(screen.getByTestId("flow-advance-count").textContent).toBe("3");

      fireEvent.click(screen.getByTestId("flow-advance-pause"));
      await vi.advanceTimersByTimeAsync(3000);
      // Paused: three more real seconds must not have moved it at all.
      expect(screen.getByTestId("flow-advance-count").textContent).toBe("3");
      expect(screen.getByTestId("lesson-view-id").textContent).toBe(A[0]);

      fireEvent.click(screen.getByTestId("flow-advance-pause"));
      // Generous slack (well past the 3 ticks the remaining count needs) —
      // this loop only has to prove the count reaches zero and navigates on
      // its own, not pin the exact millisecond it does so.
      for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(1000);

      expect(screen.getByTestId("lesson-view-id").textContent).toBe(B[0]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("starts paused when the completion also finishes the path (§9)", async () => {
    useFlowSession();
    // A single-lesson path, so its own completion is a path completion too.
    applyProgress(PATH_A, "Path A", [A[0]], 0);
    applyProgress(PATH_B, "Path B", B, 0);
    wireCompletionSync(PATH_A, "Path A", [A[0]]);
    wireCompletionSync(PATH_B, "Path B", B);
    writeFlow(
      baseRecord({
        length: null, // until I stop — the flow itself must not end here
        paths: [PATH_A, PATH_B],
        cursor: 0,
        current: { lessonId: A[0], pathId: PATH_A },
        next: { lessonId: B[0], pathId: PATH_B },
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);
    await workTheLesson();

    const completed = await screen.findByTestId("lesson-completed");
    expect(completed.getAttribute("data-variant")).toBe("path-complete");
    await screen.findByTestId("flow-advance");
    expect(screen.getByTestId("flow-advance-pause").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("flow-advance-pause").textContent).toBe("Resume");
  });

  it("reaching the flow's length navigates straight to the receipt", async () => {
    useFlowSession();
    seedTwoPathFlow();
    writeFlow(
      baseRecord({
        length: 1,
        paths: [PATH_A, PATH_B],
        cursor: 0,
        current: { lessonId: A[0], pathId: PATH_A },
        next: { lessonId: B[0], pathId: PATH_B },
        completed: [],
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);
    await workTheLesson();

    await screen.findByTestId("flow-receipt");
    expect(readFlow()?.endedReason).toBe("length");
  });

  it("a scope that runs dry ends the flow with endedReason 'dry'", async () => {
    useFlowSession();
    // Scope is PATH_A alone, one lesson from being finished — completing it
    // both finishes the path and empties the only path in scope.
    applyProgress(PATH_A, "Path A", A, 2);
    wireCompletionSync(PATH_A, "Path A", A);
    writeFlow(
      baseRecord({
        length: null,
        paths: [PATH_A],
        cursor: 0,
        current: { lessonId: A[2], pathId: PATH_A },
        next: null,
        completed: [
          { lessonId: A[0], pathId: PATH_A, title: "Path A lesson 1", outcome: "correct" },
          { lessonId: A[1], pathId: PATH_A, title: "Path A lesson 2", outcome: "correct" },
        ],
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[2]}`);
    render(<App />);
    await workTheLesson();

    await screen.findByTestId("flow-receipt");
    expect(readFlow()?.endedReason).toBe("dry");
  });

  it("a reload on the advance screen does not end the flow (fix plan item 1)", async () => {
    // `advanceRecord` moves `current` to A[1] the instant A[0]'s Mark
    // complete succeeds; the route stays mounted on A[0] (the advance
    // screen) until the learner taps on. A reload right there re-mounts on
    // A[0] with the record already pointing past it — the on-track check
    // must recognise A[0] as the *last* completion, not a stale lesson.
    useFlowSession();
    applyProgress(PATH_A, "Path A", A, 1); // A[0] complete, A[1] available server-side
    applyProgress(PATH_B, "Path B", B, 0);
    wireCompletionSync(PATH_A, "Path A", A);
    wireCompletionSync(PATH_B, "Path B", B);
    writeFlow(
      baseRecord({
        paths: [PATH_A, PATH_B],
        current: { lessonId: A[1], pathId: PATH_A },
        next: null,
        completed: [
          { lessonId: A[0], pathId: PATH_A, title: "Path A lesson 1", outcome: "correct" },
        ],
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);

    await screen.findByTestId("flow-advance");
    expect(readFlow()).not.toBeNull();
    expect(readFlow()?.current).toEqual({ lessonId: A[1], pathId: PATH_A });
  });

  it("an earlier completed lesson (not the last) still clears the record", async () => {
    // Same shape, but A[0] is no longer the *last* completion — B[0] is,
    // reached on some other path. Landing back on A[0] (path rail, browser
    // back) must still end the flow.
    useFlowSession();
    applyProgress(PATH_A, "Path A", A, 1);
    applyProgress(PATH_B, "Path B", B, 1);
    wireCompletionSync(PATH_A, "Path A", A);
    wireCompletionSync(PATH_B, "Path B", B);
    writeFlow(
      baseRecord({
        paths: [PATH_A, PATH_B],
        current: { lessonId: A[1], pathId: PATH_A },
        next: null,
        completed: [
          { lessonId: A[0], pathId: PATH_A, title: "Path A lesson 1", outcome: "correct" },
          { lessonId: B[0], pathId: PATH_B, title: "Path B lesson 1", outcome: "correct" },
        ],
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);

    await screen.findByTestId("lesson-view");
    await waitFor(() => expect(readFlow()).toBeNull());
  });

  it("opening a lesson the flow did not send the learner to clears the record", async () => {
    useFlowSession();
    seedTwoPathFlow();
    writeFlow(
      baseRecord({
        paths: [PATH_A, PATH_B],
        current: { lessonId: A[0], pathId: PATH_A },
        next: { lessonId: B[0], pathId: PATH_B },
      }),
    );

    // Navigating straight to a lesson the flow was not pointed at — the path
    // rail, the sidebar, or (as here) a typed/deep link.
    window.history.pushState({}, "", `/lessons/${A[1]}`);
    render(<App />);

    await screen.findByTestId("lesson-view");
    await waitFor(() => expect(readFlow()).toBeNull());
    // And the bar is gone with it — this is an ordinary lesson view now.
    expect(screen.queryByTestId("flow-bar")).toBeNull();
  });

  it("the look-ahead prefetches the next lesson's GET once the paths cache is warm (D8)", async () => {
    // The realistic order (§9's own reasoning): a completion's fresh fetch
    // (§5.3 step 2) is what primes the cache the *next* lesson's open effect
    // reads from — a cold first open (this test starts from nothing warmed)
    // has no cache to draw against yet, so the look-ahead only lands from the
    // second lesson on. That is the plan's own assumption, not a gap this
    // test papers over.
    useFlowSession();
    seedTwoPathFlow();
    writeFlow(
      baseRecord({
        paths: [PATH_A, PATH_B],
        cursor: 0,
        current: { lessonId: A[0], pathId: PATH_A },
        next: { lessonId: B[0], pathId: PATH_B },
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);
    await workTheLesson();
    fireEvent.click(await screen.findByTestId("flow-advance-go"));
    await screen.findByTestId("lesson-read-passage");
    expect(screen.getByTestId("lesson-view-id").textContent).toBe(B[0]);

    // B[0]'s own open effect now finds a warm cache (A[0]'s completion just
    // fetched it) and draws + prefetches interleave's next pick, A[1] — the
    // GET that is itself A[1]'s generation trigger, fire-and-forget.
    await waitFor(() => expect(readFlow()?.next).toEqual({ lessonId: A[1], pathId: PATH_A }));
    await waitFor(() => expect(lessonGetRequestCount(A[1])).toBeGreaterThan(0));
  });

  it("a serial scope's look-ahead lands on the same path, so no prefetch GET fires (fix plan item 2)", async () => {
    useFlowSession();
    seedTwoPathFlow();
    writeFlow(
      baseRecord({
        order: "serial",
        paths: [PATH_A, PATH_B],
        cursor: 0,
        current: { lessonId: A[0], pathId: PATH_A },
        next: null,
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);
    await workTheLesson();
    fireEvent.click(await screen.findByTestId("flow-advance-go"));
    await screen.findByTestId("lesson-read-passage");
    expect(screen.getByTestId("lesson-view-id").textContent).toBe(A[1]);

    // A[1]'s own open effect draws again — serial sticks with path A while it
    // still has lessons left, so the draw lands on the same path as
    // `current` and there is nothing to look ahead to: `next` stays null,
    // and A[2] (which only a prefetch would ever reach in this test) is
    // never requested.
    await waitFor(() => expect(readFlow()?.current).toEqual({ lessonId: A[1], pathId: PATH_A }));
    expect(readFlow()?.next).toBeNull();
    expect(lessonGetRequestCount(A[2])).toBe(0);
  });

  it("draws the look-ahead once the paths list itself lands, even on a cold open (fix plan item 6)", async () => {
    // A reload straight onto a lesson whose record already has `next ===
    // null` — the paths list has nothing cached yet, so the open effect's
    // first pass (which runs before the list's own fetch resolves) returns
    // early. Without `pathsListLoaded` in the effect's deps, nothing would
    // ever re-run it once the list did land, and this lesson would go
    // without a look-ahead until the *next* navigation.
    useFlowSession();
    seedTwoPathFlow();
    writeFlow(
      baseRecord({
        paths: [PATH_A, PATH_B],
        // Matches what `startFlow` would already have left behind: drawing
        // A[0] as `current` moved the interleave cursor on to B's scope slot
        // (index 1) — this is what makes the look-ahead land on a
        // *different* path once it is drawn.
        cursor: 1,
        current: { lessonId: A[0], pathId: PATH_A },
        next: null,
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);

    await waitFor(() => expect(readFlow()?.next).toEqual({ lessonId: B[0], pathId: PATH_B }));
    await waitFor(() => expect(lessonGetRequestCount(B[0])).toBe(1));
  });

  it("falls back to a generic 'the next lesson' when the paths list has no title for it yet (fix plan item 7)", async () => {
    // `flow.current` points at a path this test never seeds into the paths
    // list, so `buildFlowAdvance` can never resolve a title for it — the
    // same shape as the list still catching up to a completion (§9), just
    // pinned rather than timing-dependent.
    useFlowSession();
    applyProgress(PATH_A, "Path A", A, 1);
    wireCompletionSync(PATH_A, "Path A", A);
    writeFlow(
      baseRecord({
        paths: [PATH_A, "flow-path-ghost"],
        current: { lessonId: "ghost-lesson", pathId: "flow-path-ghost" },
        next: null,
        completed: [
          { lessonId: A[0], pathId: PATH_A, title: "Path A lesson 1", outcome: "correct" },
        ],
      }),
    );

    window.history.pushState({}, "", `/lessons/${A[0]}`);
    render(<App />);

    const advance = await screen.findByTestId("flow-advance");
    expect(advance.textContent).toContain("Next: the next lesson");
  });
});
