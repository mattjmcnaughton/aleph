import { fireEvent, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_V1_BASE, type AuthSession, type PathUnit } from "../lib/api";
import { localDaySpan } from "../components/path-complete";
import { learnerUser } from "../mocks/handlers";
import { seedLesson } from "../mocks/lessons";
import { seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// The end of a path (AL-420, mock: docs/mocks/aleph-path-complete.html).
//
// `CompletedState` used to be one component for every lesson, so the last Mark
// complete on a path rendered the same "Head back to your path to keep going"
// as the first. What separates the two now is `path_completion` on the
// completion response — the payload the server sends precisely so the
// celebration can render in the same round trip as the tap — and these tests
// drive that through the real router, TanStack Query and MSW rather than
// rendering the card in isolation, because the interesting part is *which* card
// a completion produces, not how either one looks.

const PATH_ID = "pc000000-0000-4000-8000-000000000001";
const FIRST_LESSON = "lc000000-0000-4000-8000-000000000001";
const LAST_LESSON = "lc000000-0000-4000-8000-000000000002";

const analystSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { analyst: true } },
};

function useAnalystSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(analystSession)));
}

function units(first: "available" | "complete", last: "locked" | "available" | "complete") {
  return [
    {
      id: "uc000000-0000-4000-8000-000000000001",
      title: "Foundations",
      lessons: [
        {
          id: FIRST_LESSON,
          title: "Ownership and moves",
          position_in_path: 1,
          generation_state: "generated",
          unlock_state: first,
        },
        {
          id: LAST_LESSON,
          title: "Lifetimes in function signatures",
          position_in_path: 2,
          generation_state: "generated",
          unlock_state: last,
        },
      ],
    },
  ] satisfies PathUnit[];
}

/**
 * A two-lesson path with the first already done — the learner is standing on
 * the last lesson, one Mark complete from finishing.
 *
 * Both lessons go into the lesson store because that store is the fake's whole
 * universe of lessons: it is what `pathCompletionFor` counts, exactly as the
 * real route counts rows on the path.
 */
function seedNearlyDone(): void {
  seedPath({ id: PATH_ID, topic: "Rust ownership", level: "some_experience" });
  seedLesson({
    id: FIRST_LESSON,
    path_id: PATH_ID,
    title: "Ownership and moves",
    position_in_path: 1,
    unlock_state: "complete",
  });
  seedLesson({
    id: LAST_LESSON,
    path_id: PATH_ID,
    title: "Lifetimes in function signatures",
    position_in_path: 2,
    correctIndex: 0,
  });
}

async function gotoLesson(id: string): Promise<HTMLElement> {
  window.history.pushState({}, "", `/lessons/${id}`);
  render(<App />);
  return screen.findByTestId("lesson-view");
}

/** Answer the Quick check (non-gating) and mark the open lesson complete. */
async function markComplete(): Promise<void> {
  fireEvent.click((await screen.findAllByTestId("quick-check-option"))[0]);
  fireEvent.click(screen.getByTestId("quick-check-submit"));
  fireEvent.click(await screen.findByTestId("lesson-complete-button"));
  await screen.findByTestId("lesson-completed");
}

describe("Finishing a path — the completion card (AL-420)", () => {
  it("[AL-420] the last Mark complete on a path renders the path-complete card", async () => {
    seedNearlyDone();
    await gotoLesson(LAST_LESSON);
    await markComplete();

    const card = screen.getByTestId("lesson-completed");
    expect(card.getAttribute("data-variant")).toBe("path-complete");
    expect(card.textContent).toContain("Path complete.");
    // Names the path, and never tells a learner with nothing left to keep going.
    expect(card.textContent).toContain("Rust ownership");
    expect(card.textContent).not.toContain("keep going");
    // The way back survives under copy that now means something.
    expect(screen.getByTestId("lesson-completed-back").getAttribute("href")).toContain(PATH_ID);
  });

  it("[AL-420] a mid-path completion still gets the ordinary lesson card", async () => {
    // Three lessons, two of them done: finishing the second leaves one
    // outstanding, so `path_completion` is null and nothing is celebrated.
    seedNearlyDone();
    seedLesson({
      id: "lc000000-0000-4000-8000-000000000003",
      path_id: PATH_ID,
      title: "Borrowing",
      position_in_path: 3,
    });
    await gotoLesson(LAST_LESSON);
    await markComplete();

    const card = screen.getByTestId("lesson-completed");
    expect(card.getAttribute("data-variant")).toBeNull();
    expect(card.textContent).toContain("Lesson complete.");
    expect(screen.queryByTestId("path-complete-stats")).toBeNull();
  });

  it("[AL-420] the receipt counts the path's lessons and the days it spanned", async () => {
    seedNearlyDone();
    // The two ends of the span the server reports, four local days apart.
    server.use(
      http.post(`${API_V1_BASE}/lessons/${LAST_LESSON}/complete`, () =>
        HttpResponse.json({
          id: LAST_LESSON,
          unlock_state: "complete",
          path_completion: {
            lesson_count: 24,
            first_completed_at: "2026-08-01T09:00:00Z",
            completed_at: "2026-08-04T21:30:00Z",
          },
        }),
      ),
    );
    await gotoLesson(LAST_LESSON);
    await markComplete();

    const stats = screen.getByTestId("path-complete-stats");
    expect(stats.textContent).toContain("24");
    expect(stats.textContent).toContain("Lessons");
    expect(stats.textContent).toContain("4");
    expect(stats.textContent).toContain("Days");
  });

  it("[AL-420] the doors carry the path's Topic, and the Beat door follows its flag", async () => {
    seedNearlyDone();
    await gotoLesson(LAST_LESSON);
    await markComplete();

    // Topic, not the display title — the Topic is what a new path or Beat is
    // generated from (CONTEXT.md).
    const deeper = screen.getByTestId("path-complete-deeper");
    expect(deeper.getAttribute("href")).toContain("topic=Rust");
    // The default fake learner ships `analyst: false`, so there is no Beat to
    // deploy and no door offering one — absent, not disabled.
    expect(screen.queryByTestId("path-complete-beat")).toBeNull();
  });

  it("[AL-420] with the analyst flag on, the Beat door appears beside it", async () => {
    useAnalystSession();
    seedNearlyDone();
    await gotoLesson(LAST_LESSON);
    await markComplete();

    const beat = screen.getByTestId("path-complete-beat");
    expect(beat.getAttribute("href")).toContain("/beats/new");
    expect(beat.getAttribute("href")).toContain("topic=Rust");
  });

  it("[AL-420] the celebration is thrown on the tap that earned it", async () => {
    seedNearlyDone();
    await gotoLesson(LAST_LESSON);
    await markComplete();

    expect(screen.getByTestId("path-complete-confetti")).toBeTruthy();
  });

  it("[AL-420][a11y] prefers-reduced-motion spawns no confetti at all", async () => {
    // Not merely un-animated: 22 nodes parked at their start position would sit
    // on top of the card forever, so the branch has to create none.
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query: string) =>
        ({
          matches: query.includes("prefers-reduced-motion"),
          media: query,
          onchange: null,
          addListener: vi.fn(),
          removeListener: vi.fn(),
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
          dispatchEvent: vi.fn(),
        }) as unknown as MediaQueryList,
    );
    seedNearlyDone();
    await gotoLesson(LAST_LESSON);
    await markComplete();

    expect(screen.getByTestId("lesson-completed").getAttribute("data-variant")).toBe(
      "path-complete",
    );
    expect(screen.queryByTestId("path-complete-confetti")).toBeNull();
  });

  it("[AL-420] revisiting a finished path's last lesson: the card, no Days, no confetti", async () => {
    // No mutation ran this session, so there is no completion payload and no
    // span — the path detail knows how many lessons there were, not when they
    // were done, and a fabricated number is worse than one fewer stat.
    seedPath({
      id: PATH_ID,
      topic: "Rust ownership",
      level: "some_experience",
      units: units("complete", "complete"),
    });
    seedLesson({
      id: LAST_LESSON,
      path_id: PATH_ID,
      title: "Lifetimes in function signatures",
      position_in_path: 2,
      unlock_state: "complete",
    });
    await gotoLesson(LAST_LESSON);

    // Awaited on the stats rather than on `lesson-completed`: the ordinary card
    // renders first and resolves into this one when the path detail lands a
    // beat later, which is deliberate — with no path payload yet there is
    // nothing that could tell the two apart, and guessing would be worse.
    const stats = await screen.findByTestId("path-complete-stats");
    expect(screen.getByTestId("lesson-completed").getAttribute("data-variant")).toBe(
      "path-complete",
    );
    expect(stats.textContent).toContain("Lessons");
    expect(stats.textContent).not.toContain("Days");
    expect(screen.queryByTestId("path-complete-confetti")).toBeNull();
  });
});

describe("localDaySpan — whole local Days, inclusive", () => {
  it("[AL-420] a path started and finished the same day is one day", () => {
    expect(localDaySpan("2026-08-13T08:00:00Z", "2026-08-13T09:30:00Z")).toBe(1);
  });

  it("[AL-420] counts calendar days, not elapsed 24-hour periods", () => {
    // Ten hours apart, but either side of a local midnight: two days.
    const evening = new Date(2026, 7, 12, 20, 0).toISOString();
    const morning = new Date(2026, 7, 13, 6, 0).toISOString();
    expect(localDaySpan(evening, morning)).toBe(2);
  });

  it("[AL-420] spans a run of days inclusively", () => {
    const start = new Date(2026, 7, 1, 9, 0).toISOString();
    const end = new Date(2026, 7, 11, 9, 0).toISOString();
    expect(localDaySpan(start, end)).toBe(11);
  });
});
