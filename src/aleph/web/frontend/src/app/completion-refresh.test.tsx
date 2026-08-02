import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession, type PathUnit } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { seedLesson } from "../mocks/lessons";
import { pathsListRequestCount, seedPath } from "../mocks/paths";
import {
  configureProgress,
  progressRequestCount,
  releaseProgress,
  zeroActivity,
} from "../mocks/progress";
import { server } from "../mocks/server";
import { App } from "./app";

// Marking a lesson complete moves state on surfaces the lesson view does not own
// (AL-090/W1): the rail's unlock states and both progress readouts belong to
// `GET /paths/{id}` and `GET /paths`. Neither corrects itself — the path poll
// stops once everything visible is terminal, the list poll once every row is,
// and both payloads stay fresh for the app-wide `staleTime` (30s). So completion
// invalidates the shared `["paths", …]` prefix (routes/lessons.$lessonId.tsx),
// and these two tests are what hold that line: revert the `invalidateQueries`
// call and both go red.
//
// Why they live in their own file rather than in lesson-view.test.tsx or
// path-view.test.tsx: the gap is *between* routes. Each per-route suite drives
// one surface with the other's payload mocked, so a path payload going stale
// while a lesson mutation runs is invisible to both by construction. These
// tests instead walk the learner's whole route — path view → lesson view →
// back — through the real router and one query client, and the fake advances
// *server* state on completion so a stale render and a fresh one differ.

const PATH_ID = "p9000000-0000-4000-8000-000000000001";
const FIRST_LESSON = "l9000000-0000-4000-8000-000000000001";
const SECOND_LESSON = "l9000000-0000-4000-8000-000000000002";

/** The rail as the server serves it, before and after the first completion. */
function units(first: "available" | "complete", second: "locked" | "available"): PathUnit[] {
  return [
    {
      id: "u9000000-0000-4000-8000-000000000001",
      title: "Foundations & types",
      lessons: [
        {
          id: FIRST_LESSON,
          title: "What TypeScript adds",
          position_in_path: 0,
          generation_state: "generated",
          unlock_state: first,
        },
        {
          id: SECOND_LESSON,
          title: "Primitive types",
          position_in_path: 1,
          // Generated in both payloads so the path view's poll is terminal
          // either way (`isPathViewTerminal`): a still-resolving lesson would
          // keep the poll running and refresh the rail on its own, which would
          // mask exactly the staleness under test.
          generation_state: "generated",
          unlock_state: second,
        },
      ],
    },
  ];
}

const BEFORE_UNITS = units("available", "locked");
const AFTER_UNITS = units("complete", "available");

function seedPathWith(unitsPayload: PathUnit[]): void {
  seedPath({ id: PATH_ID, topic: "TypeScript", level: "new_to_it", units: unitsPayload });
}

/**
 * A path one lesson in, plus the server move `POST /complete` makes.
 *
 * The stock fakes keep a lesson store and a path store apart, so nothing links
 * them; this handler is that link, and only that — it advances the path payload
 * to the state the real backend would serve after a completion. Everything the
 * tests assert is then a straight question about which payload the client shows.
 */
function seedJourney(): void {
  seedPathWith(BEFORE_UNITS);
  seedLesson({ id: FIRST_LESSON, path_id: PATH_ID, correctIndex: 0 });
  server.use(
    http.post(`${API_V1_BASE}/lessons/${FIRST_LESSON}/complete`, () => {
      seedPathWith(AFTER_UNITS);
      return HttpResponse.json({ id: FIRST_LESSON, unlock_state: "complete" });
    }),
  );
}

function unlockStateOf(lessonId: string): string | null {
  return screen.getByTestId(`lesson-${lessonId}`).getAttribute("data-unlock-state");
}

/** Answer the Quick check (non-gating) and mark the open lesson complete. */
async function workTheLesson(): Promise<void> {
  fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
  fireEvent.click(screen.getByTestId("quick-check-submit"));
  fireEvent.click(await screen.findByTestId("lesson-complete-button"));
  await screen.findByTestId("lesson-completed");
}

describe("Completion refreshes the path surfaces — cross-route (AL-090/W1)", () => {
  it("[AL-090][W1] the rail and progress are current on returning to the path", async () => {
    seedJourney();
    window.history.pushState({}, "", `/paths/${PATH_ID}`);
    render(<App />);

    // Warm the path view: this cached payload is the one that must not be
    // served again after the completion has moved past it.
    expect((await screen.findByTestId("path-progress")).textContent).toMatch(/0 of 2 lessons/i);
    expect(unlockStateOf(FIRST_LESSON)).toBe("available");
    expect(unlockStateOf(SECOND_LESSON)).toBe("locked");

    fireEvent.click(screen.getByTestId(`lesson-${FIRST_LESSON}`));
    await workTheLesson();

    // "Back to your path" — a client-side navigation well inside `staleTime`,
    // exactly the tap W1 ends on.
    fireEvent.click(screen.getByTestId("lesson-completed-back"));
    await screen.findByTestId("path-view");

    await waitFor(() => {
      expect(screen.getByTestId("path-progress").textContent).toMatch(/1 of 2 lessons/i);
    });
    expect(unlockStateOf(FIRST_LESSON)).toBe("complete");
    // ...and the next lesson is open, not still locked behind a stale payload.
    expect(unlockStateOf(SECOND_LESSON)).toBe("available");
  });

  it("[AL-090][W1] the switcher roll-up refreshes too — one invalidation, both surfaces", async () => {
    seedJourney();
    window.history.pushState({}, "", "/");
    render(<App />);

    // Warm the list the same way.
    const warmedRow = await screen.findByTestId("path-list-item");
    expect(warmedRow.getAttribute("data-path-id")).toBe(PATH_ID);
    expect(screen.getByTestId("path-item-progress").textContent).toMatch(/0 of 2 lessons/i);
    const listRequestsBefore = pathsListRequestCount();

    fireEvent.click(screen.getByTestId("path-item-open"));
    fireEvent.click(await screen.findByTestId(`lesson-${FIRST_LESSON}`));
    await workTheLesson();

    // Home through the header, the way a learner leaves a finished lesson.
    fireEvent.click(screen.getByRole("link", { name: "Aleph home" }));
    await screen.findByTestId("paths-switcher");

    await waitFor(() => {
      expect(screen.getByTestId("path-item-progress").textContent).toMatch(/1 of 2 lessons/i);
    });
    // A fresh `GET /paths` is what produced that readout — the list poll had
    // already stopped (every row terminal), so nothing else would have fired it.
    expect(pathsListRequestCount()).toBeGreaterThan(listRequestsBefore);
  });
});

// --- Streaks D10: the optimistic cache patch --------------------------------
//
// The completion mutation's `onSuccess` also patches `["progress", …]` (D10,
// `routes/lessons.$lessonId.tsx`) — the same file, the same reasoning as the
// suite above, applied to a second cache the mutation reaches into. Three
// properties, each pinned by its own test (TDD §11): a cold cache no-ops
// rather than fabricating a payload; a second same-day completion no-ops
// rather than double-counting; a first completion bumps the number in this
// interaction, and the refetch that follows is authoritative.
//
// The middle two hold `GET /progress/summary` open (`mocks/progress.ts`'s
// `hang`/`releaseProgress`) before completing a lesson, so whatever the streak
// line shows right after navigating back to home can only be the client's own
// cache — a request that cannot resolve cannot be what produced it. Without
// this, "the optimistic value is visible before any refetch" is a race no
// `waitFor` can pin.

const STREAK_PATH_ID = "p9600000-0000-4000-8000-000000000001";
const STREAK_LESSON_ID = "l9600000-0000-4000-8000-000000000001";
const TODAY = "2026-08-02";

/** A learner with `streaks` on — the default fake session ships it dark. */
const streaksSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { streaks: true } },
};

function useStreaksSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(streaksSession)));
}

/** One path, one available lesson — enough to drive a single completion. */
function seedStreakPath(): void {
  seedPath({
    id: STREAK_PATH_ID,
    topic: "TypeScript",
    level: "new_to_it",
    units: [
      {
        id: "u9600000-0000-4000-8000-000000000001",
        title: "Foundations & types",
        lessons: [
          {
            id: STREAK_LESSON_ID,
            title: "What TypeScript adds",
            position_in_path: 0,
            generation_state: "generated",
            unlock_state: "available",
          },
        ],
      },
    ],
  });
  seedLesson({ id: STREAK_LESSON_ID, path_id: STREAK_PATH_ID, correctIndex: 0 });
}

function progressSummaryFixture(overrides: {
  current_streak: number;
  best_streak: number;
  completed_today: number;
}) {
  // The last cell of `activity` is today (oldest-first, TDD §6) — kept in
  // lockstep with `completed_today` so the fixture itself never contradicts
  // the field the D10 patch and the grid both key off.
  const activity = zeroActivity(TODAY);
  activity[activity.length - 1] = {
    ...activity[activity.length - 1],
    count: overrides.completed_today,
  };
  return {
    today: TODAY,
    activity,
    paths: [],
    ...overrides,
  };
}

/** Today's cell — the last of the 45, oldest-first (TDD §6). */
function todayActivityCell(): HTMLElement {
  const cells = screen.getAllByTestId("activity-cell");
  return cells[cells.length - 1];
}

/** Home → the streak path's view → its one lesson, worked to completion. */
async function completeStreakLesson(): Promise<void> {
  fireEvent.click(screen.getByTestId("path-item-open"));
  fireEvent.click(await screen.findByTestId(`lesson-${STREAK_LESSON_ID}`));
  await workTheLesson();
  fireEvent.click(screen.getByRole("link", { name: "Aleph home" }));
  await screen.findByTestId("paths-switcher");
}

describe("Streaks — the optimistic cache patch (D10)", () => {
  it("[D10] cold cache: completing a lesson before ever visiting home fabricates nothing", async () => {
    useStreaksSession();
    seedStreakPath();
    window.history.pushState({}, "", `/lessons/${STREAK_LESSON_ID}`);
    render(<App />);

    await workTheLesson();

    // Home was never visited: there is no cache entry under `["progress", …]`
    // for the patch to touch, and no observer for `invalidateQueries` to wake
    // — both have to hold for this to stay at zero rather than becoming a
    // request nobody asked for.
    expect(progressRequestCount()).toBe(0);
  });

  it("[D10] second completion of the day: the optimistic patch leaves the cached streak untouched", async () => {
    useStreaksSession();
    seedStreakPath();
    configureProgress({
      summary: progressSummaryFixture({ current_streak: 5, best_streak: 5, completed_today: 1 }),
    });

    window.history.pushState({}, "", "/");
    render(<App />);
    await screen.findByTestId("streak-line");
    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 5-day streak · 1 lesson today");

    // Park the next GET: the streak line's value right after this can only be
    // read off the cache the mutation itself wrote.
    configureProgress({ hang: true });
    await completeStreakLesson();

    // `completed_today` was already 1, so the guard
    // (`old.completed_today > 0`) must have returned `old` untouched — not
    // "happens to still read the same number after a lucky fast refetch",
    // since that refetch is still parked.
    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 5-day streak · 1 lesson today");
    expect(todayActivityCell().getAttribute("data-intensity")).toBe("dim");

    // Release with the server's own count for a genuine second same-day
    // completion — a raw count (§5.3), so it really does move even though the
    // streak numbers do not. The eventual authoritative refetch is what
    // corrects it, not the optimistic patch pretending to know.
    configureProgress({
      summary: progressSummaryFixture({ current_streak: 5, best_streak: 5, completed_today: 2 }),
    });
    releaseProgress();

    await waitFor(() => {
      expect(screen.getByTestId("streak-line").textContent).toBe(
        "🔥 5-day streak · 2 lessons today",
      );
    });
  });

  it("[D10] first completion of the day: the line moves before any refetch, then the refetch confirms it", async () => {
    useStreaksSession();
    seedStreakPath();
    configureProgress({
      summary: progressSummaryFixture({ current_streak: 4, best_streak: 4, completed_today: 0 }),
    });

    window.history.pushState({}, "", "/");
    render(<App />);
    await screen.findByTestId("streak-line");
    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 4-day streak");

    configureProgress({ hang: true });
    await completeStreakLesson();

    // The "Day 6 🔥" beat (PRD §3) fires off this value, in this interaction —
    // the GET that would otherwise be needed to learn it is still parked, so
    // this can only be the optimistic patch, not a lucky fast round trip.
    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 5-day streak · 1 lesson today");
    // The activity strip's last cell (today) moves with the same patch, so the
    // grid and the number never disagree mid-flight.
    expect(todayActivityCell().getAttribute("data-intensity")).toBe("dim");

    // The authoritative payload disagrees slightly (TDD §15: two devices, or a
    // completion racing the server's own day boundary) — released once the
    // optimistic value has already been observed above.
    configureProgress({
      summary: progressSummaryFixture({ current_streak: 6, best_streak: 6, completed_today: 2 }),
    });
    releaseProgress();

    await waitFor(() => {
      expect(screen.getByTestId("streak-line").textContent).toBe(
        "🔥 6-day streak · 2 lessons today",
      );
    });
  });
});
