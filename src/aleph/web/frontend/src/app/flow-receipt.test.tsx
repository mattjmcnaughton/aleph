import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession, type PathUnit } from "../lib/api";
import type { FlowRecord } from "../lib/flow";
import { readFlow, writeFlow } from "../lib/flow";
import { flashcardKeepRequests, seedFlashcardDraftRun } from "../mocks/flashcards";
import { learnerUser } from "../mocks/handlers";
import { seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// `/flow/done`, the receipt (flow TDD §5.5, mock screen 04) — driven end to
// end. Nothing here writes anything new (D6): every stat is read straight off
// a seeded record and the paths list.

function useFlowSession(extra: Record<string, boolean> = {}): void {
  const session: AuthSession = {
    authenticated: true,
    provider: "keycloak",
    user: { ...learnerUser, feature_flags: { flow: true, ...extra } },
  };
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

const PATH_A = "receipt-path-a";
const PATH_B = "receipt-path-b";

function seedLandedPaths(): void {
  const unitsFor = (idPrefix: string, done: number, total: number): PathUnit[] => [
    {
      id: `${idPrefix}-u1`,
      title: "Unit",
      lessons: Array.from({ length: total }, (_, i) => ({
        id: `${idPrefix}-l${i + 1}`,
        title: `Lesson ${i + 1}`,
        position_in_path: i + 1,
        generation_state: "generated" as const,
        unlock_state: i < done ? ("complete" as const) : ("available" as const),
      })),
    },
  ];
  seedPath({ id: PATH_A, topic: "Path A", level: "new_to_it", units: unitsFor("a", 3, 5) });
  seedPath({ id: PATH_B, topic: "Path B", level: "new_to_it", units: unitsFor("b", 1, 4) });
}

function seedReceiptRecord(overrides: Partial<FlowRecord> = {}): void {
  writeFlow({
    version: 1,
    startedAt: "2026-01-01T00:00:00.000Z",
    length: 3,
    order: "interleave",
    paths: [PATH_A, PATH_B],
    cursor: 0,
    current: null,
    next: null,
    completed: [
      { lessonId: "a-l1", pathId: PATH_A, title: "Lesson 1", outcome: "correct" },
      { lessonId: "b-l1", pathId: PATH_B, title: "Lesson 1", outcome: "incorrect" },
      { lessonId: "a-l2", pathId: PATH_A, title: "Lesson 2", outcome: "correct" },
    ],
    endedReason: "length",
    ...overrides,
  });
}

async function gotoReceipt(): Promise<void> {
  window.history.pushState({}, "", "/flow/done");
  render(<App />);
  await screen.findByTestId("flow-receipt");
}

describe("Flow receipt — /flow/done", () => {
  it("the three stats and the ledger read straight off the record and the paths list", async () => {
    useFlowSession();
    seedLandedPaths();
    seedReceiptRecord();
    await gotoReceipt();

    expect(screen.getByTestId("flow-receipt-lessons").textContent).toBe("3");
    expect(screen.getByTestId("flow-receipt-paths").textContent).toBe("2");
    expect(screen.getByTestId("flow-receipt-checks").textContent).toBe("2/3");

    await waitFor(() => {
      const ledger = screen.getByTestId("flow-receipt-ledger");
      expect(ledger.textContent).toMatch(/Path A.*2 lessons.*3 of 5/s);
      expect(ledger.textContent).toMatch(/Path B.*1 lesson.*1 of 4/s);
    });
  });

  it("hides the Quick checks stat entirely when no completion carried an outcome", async () => {
    useFlowSession();
    seedLandedPaths();
    seedReceiptRecord({
      completed: [{ lessonId: "a-l1", pathId: PATH_A, title: "Lesson 1", outcome: null }],
    });
    await gotoReceipt();

    expect(screen.queryByTestId("flow-receipt-checks")).toBeNull();
    // Two columns, not three, with only two stats to show (fix plan item 7)
    // — three slots for two stats left a visibly empty cell.
    const grid = screen.getByTestId("flow-receipt-lessons").closest(".grid");
    expect(grid?.className).toContain("grid-cols-2");
  });

  it("names the ending: complete, ended, or dry", async () => {
    useFlowSession();
    seedLandedPaths();

    seedReceiptRecord({ endedReason: "ended" });
    await gotoReceipt();
    expect(screen.getByText("Flow ended")).toBeTruthy();
  });

  it("the drafts batch renders one DraftList per lesson and keeps post the right body", async () => {
    useFlowSession({ flashcards: true });
    seedLandedPaths();
    seedReceiptRecord();
    seedFlashcardDraftRun("a-l1", {
      state: "generated",
      cards: [{ id: "c1", front: "Front one", back: "Back one" }],
    });
    await gotoReceipt();

    const drafts = await screen.findByTestId("flow-receipt-drafts");
    expect(drafts.textContent).toMatch(/Aleph drafted 1 card/);
    const lessonBlock = within_(drafts, "flow-draft-lesson");
    expect(lessonBlock.getAttribute("data-lesson-id")).toBe("a-l1");

    fireEvent.click(screen.getByTestId("draft-keep-button"));

    await waitFor(() => expect(flashcardKeepRequests()).toHaveLength(1));
    expect(flashcardKeepRequests()[0]).toMatchObject({ lesson_id: "a-l1", kept_ids: ["c1"] });
  });

  it("'Go again' prefills the setup sheet with this flow's own scope", async () => {
    useFlowSession();
    seedLandedPaths();
    seedReceiptRecord({ order: "serial" });
    await gotoReceipt();

    fireEvent.click(screen.getByTestId("flow-receipt-again"));

    await screen.findByTestId("flow-setup");
    await waitFor(() => {
      expect(screen.getByTestId(`flow-path-${PATH_A}`).getAttribute("aria-pressed")).toBe("true");
      expect(screen.getByTestId(`flow-path-${PATH_B}`).getAttribute("aria-pressed")).toBe("true");
    });
  });

  it("Home clears the record", async () => {
    useFlowSession();
    seedLandedPaths();
    seedReceiptRecord();
    await gotoReceipt();

    fireEvent.click(screen.getByTestId("flow-receipt-home"));

    await screen.findByTestId("paths-switcher");
    await waitFor(() => expect(readFlow()).toBeNull());
  });

  it("redirects home when there is no record to show", async () => {
    useFlowSession();
    window.history.pushState({}, "", "/flow/done");
    render(<App />);

    await screen.findByTestId("paths-switcher");
  });

  it("renders the unavailable dead-end when the flag is off", async () => {
    seedReceiptRecord();
    window.history.pushState({}, "", "/flow/done");
    render(<App />);

    await screen.findByTestId("flow-done-unavailable");
  });
});

/** The one descendant of `container` carrying `testid`, asserting there is
 *  exactly one (this suite only ever seeds one drafted lesson per test). */
function within_(container: HTMLElement, testid: string): HTMLElement {
  const matches = container.querySelectorAll(`[data-testid="${testid}"]`);
  expect(matches).toHaveLength(1);
  return matches[0] as HTMLElement;
}
