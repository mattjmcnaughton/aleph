import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type PathUnit } from "../lib/api";
import { seedLesson } from "../mocks/lessons";
import { pathsListRequestCount, seedPath } from "../mocks/paths";
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
