import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession, type PathUnit } from "../lib/api";
import type { Change } from "../lib/shaping";
import type { Proposal } from "../lib/tutor-stream";
import { learnerUser } from "../mocks/handlers";
import { MID_PATH_UNITS, pathDetailFor, seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import {
  configureShaping,
  seedChanges,
  seedShapingConversation,
  shapingApplyCount,
  shapingChangeReadCount,
  shapingChanges,
  shapingClearCount,
  shapingUndoCount,
} from "../mocks/shaping";
import { App } from "./app";

// Apply, Undo, ghost rows and the Change history (AL-331, TDD §8/§5.6–§5.8,
// PRD §5.4–§5.5) — the write half of the shaping surface, driven end to end
// through the real router, TanStack Query and MSW against the AL-321 wire.
//
// The state machine itself is asserted directly in `lib/shaping-apply.test.ts`;
// what these tests own is the wiring, and four claims in particular:
//
//  1. **Consent is a tap.** Rendering a Proposal costs no request; **Not now**
//     costs none either. Exactly one thing writes to the path, and a learner has
//     to press it.
//  2. **Ghosts swap for real rows in one round trip.** Apply's `path` is
//     byte-for-byte `GET /paths/{id}`, so the rail's iris ghosts become the
//     outline's real rows with no second fetch.
//  3. **A stale Proposal is a normal outcome.** Every coded `409` lands on the
//     card (or the sheet) as the state and the affordance it names — the
//     server's own learner-facing wording included.
//  4. **The history belongs to the path.** It survives "new conversation", and
//     it costs a request only when a learner opens it.

const PATH_ID = "p3100000-0000-4000-8000-000000000001";

/** A plain learner with the dark `shaping` flag flipped on for them. */
const shapingOnSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { shaping: true } },
};

/** Flag off — the surface does not exist, so nothing here may cost a request. */
const shapingOffSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { shaping: false } },
};

/**
 * An Addition at `position_in_path: 3` — `MID_PATH_UNITS`' "Narrowing", the
 * first locked lesson, so it is at-or-after the learner's first non-engaged
 * position exactly as the predicates require.
 */
const ADDITION: Proposal = {
  summary: "Adds 2 lessons on `unknown` before Narrowing.",
  operations: [
    {
      insert_at_position: 3,
      new_unit: null,
      lessons: [{ title: "`unknown` vs `any`" }, { title: "Narrowing `unknown`" }],
      rationale: "You missed the narrowing check, and Narrowing assumes it.",
      estimated_minutes: 10,
    },
  ],
};

/** A Revision of the same unengaged lesson (`MID_PATH_UNITS`' fourth row). */
const REVISION: Proposal = {
  summary: "Revises Narrowing to assume closures are known.",
  operations: [
    {
      lesson_id: "l2000000-0000-4000-8000-000000000004",
      instruction: "Re-teach assuming closures are known.",
      new_title: null,
      rationale: "You already know closures from the earlier unit.",
    },
  ],
};

/**
 * `MID_PATH_UNITS` with its locked lesson still generating, so `isPathViewTerminal`
 * stays false and the path route keeps polling `GET /paths/{id}` — the only
 * condition under which a poll can be in flight when Apply answers.
 */
const GENERATING_MID_PATH_UNITS: PathUnit[] = MID_PATH_UNITS.map((unit) => ({
  ...unit,
  lessons: unit.lessons.map((lesson) =>
    lesson.unlock_state === "locked"
      ? { ...lesson, generation_state: "generating" as const }
      : { ...lesson },
  ),
}));

function useSession(session: AuthSession) {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

/** A promise plus its resolver — how a handler holds a response open. */
function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve = (): void => {};
  const promise = new Promise<void>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

/** Flush timers/microtasks inside `act` so a resolved response can land. */
async function settle(ms = 50): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

/**
 * The preamble every case shares: the dark flag on, a `ready` mid-path to shape,
 * a thread that already holds one tutor turn carrying `proposal`, and the rail
 * opened through its mark. Seeding the thread rather than streaming it keeps
 * these tests about the card instead of about the transport (AL-330's subject).
 */
async function openRailWithProposal(
  proposal: Proposal = ADDITION,
  options: {
    session?: AuthSession;
    resolution?: "pending" | "applied" | "undone" | "superseded";
    units?: PathUnit[];
  } = {},
): Promise<void> {
  useSession(options.session ?? shapingOnSession);
  seedPath({
    id: PATH_ID,
    topic: "TypeScript",
    level: "some_experience",
    units: options.units ?? MID_PATH_UNITS,
  });
  seedShapingConversation(PATH_ID, [
    { role: "learner", content: "Add practice on narrowing" },
    {
      role: "tutor",
      content: "Here's what I'd add.",
      proposal,
      resolution: options.resolution ?? "pending",
    },
  ]);
  window.history.pushState({}, "", `/paths/${PATH_ID}`);
  render(<App />);
  await screen.findByTestId("path-view");
  fireEvent.click(await screen.findByTestId("shaping-rail-mark"));
  await screen.findByTestId("shaping-rail");
  await screen.findByTestId("shaping-rail-proposal");
}

const card = () => screen.getByTestId("shaping-rail-proposal");
const ghosts = () => screen.queryAllByTestId("path-rail-ghost");
const pathRail = () => screen.getByTestId("path-rail");

// --- The pending card (PRD §5.4) ---------------------------------------------

describe("Proposal card — pending", () => {
  it("[AL-331] groups the operations with rationale and a cost line", async () => {
    await openRailWithProposal();

    expect(card().dataset.state).toBe("pending");
    expect(screen.getByTestId("shaping-rail-proposal-cost").textContent).toBe(
      "Adds 2 lessons ≈ 10 min",
    );
    const operation = screen.getByTestId("shaping-rail-proposal-operation");
    expect(operation.dataset.kind).toBe("add_lessons");
    expect(
      screen.getAllByTestId("shaping-rail-proposal-lesson").map((row) => row.textContent),
    ).toEqual(["`unknown` vs `any`", "Narrowing `unknown`"]);
    expect(operation.textContent).toContain("You missed the narrowing check");
  });

  it("[AL-331] shows a Revision's instruction — consent needs to see what it does", async () => {
    await openRailWithProposal(REVISION);

    const operation = screen.getByTestId("shaping-rail-proposal-operation");
    expect(operation.dataset.kind).toBe("revise_lesson");
    expect(operation.textContent).toContain("Re-teach assuming closures are known");
    expect(screen.getByTestId("shaping-rail-proposal-cost").textContent).toBe("Revises 1 lesson");
  });

  it("[AL-331] rendering a Proposal writes nothing — Apply is the only write path", async () => {
    await openRailWithProposal();

    expect(shapingApplyCount()).toBe(0);
    expect(screen.getByTestId("shaping-rail-proposal-apply")).toBeTruthy();
    expect(screen.getByTestId("shaping-rail-proposal-dismiss")).toBeTruthy();
  });

  it("[AL-331] Not now dismisses the offer with no request at all", async () => {
    await openRailWithProposal();
    expect(ghosts()).toHaveLength(2);

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-dismiss"));

    await waitFor(() => expect(card().dataset.state).toBe("dismissed"));
    expect(shapingApplyCount()).toBe(0);
    expect(screen.getByTestId("shaping-rail-proposal-dismissed").textContent).toBe("Not applied.");
    // Declining is never destructive — and it takes the preview with it.
    expect(ghosts()).toHaveLength(0);
  });
});

// --- Ghost rows (TDD D14, PRD §5.4) ------------------------------------------

describe("Ghost rows", () => {
  it("[AL-331] previews an Addition in place, iris against the path's teal rows", async () => {
    await openRailWithProposal();

    const rows = ghosts();
    expect(rows).toHaveLength(2);
    // "Proposed" twice per row on purpose: an `sr-only` prefix that carries the
    // state to a screen reader (the marker itself is decorative), and the
    // visible kicker. The titles are the payload's, in payload order.
    expect(rows[0].textContent).toContain("`unknown` vs `any`");
    expect(rows[1].textContent).toContain("Narrowing `unknown`");
    for (const row of rows) expect(row.textContent).toContain("Proposed");
    // A ghost is a drawing of an offer, not a lesson: nothing to open.
    for (const row of rows) expect(row.tagName).not.toBe("BUTTON");
    expect(rows[0].className).toContain("iris");
  });

  it("[AL-331] marks a Revision's target rather than adding a row", async () => {
    await openRailWithProposal(REVISION);

    expect(ghosts()).toHaveLength(0);
    const target = screen.getByTestId("lesson-l2000000-0000-4000-8000-000000000004");
    expect(target.dataset.revising).toBe("true");
    expect(within(target).getByTestId("path-rail-revising").textContent).toBe("Will be revised");
  });

  it("[AL-331] a new unit previews as a ghost unit in the rail", async () => {
    await openRailWithProposal({
      summary: "Adds a unit on `unknown`.",
      operations: [
        {
          insert_at_position: 2,
          new_unit: { title: "Unknown & narrowing", summary: "The gap." },
          lessons: [{ title: "`unknown` vs `any`" }],
          rationale: "It is a unit's worth of work.",
          estimated_minutes: 5,
        },
      ],
    });

    const unit = screen.getByTestId("path-rail-ghost-unit");
    expect(unit.textContent).toContain("Proposed unit");
    expect(unit.textContent).toContain("Unknown & narrowing");
    expect(within(unit).getAllByTestId("path-rail-ghost")).toHaveLength(1);
  });

  it("[AL-331] a ghost unit does not renumber the real units below it", async () => {
    // A ghost unit has no place in the path's ordering until Apply gives it one
    // — so it takes no number, and the real units keep the ones they already
    // had. Renumbering "Functions & narrowing" from 02 to 03 would be the rail
    // restating the path for an edit nobody has consented to yet.
    await openRailWithProposal({
      summary: "Adds a unit on `unknown`.",
      operations: [
        {
          insert_at_position: 2,
          new_unit: { title: "Unknown & narrowing", summary: "The gap." },
          lessons: [{ title: "`unknown` vs `any`" }],
          rationale: "It is a unit's worth of work.",
          estimated_minutes: 5,
        },
      ],
    });

    const kickers = within(pathRail()).getAllByText(/^(Unit \d\d|Proposed unit)$/);
    expect(kickers.map((kicker) => kicker.textContent)).toEqual([
      "Unit 01",
      "Proposed unit",
      "Unit 02",
    ]);
  });

  it("[AL-331] ghosts exist only while the proposal is pending in the open thread", async () => {
    await openRailWithProposal();
    expect(ghosts()).toHaveLength(2);

    fireEvent.click(screen.getByTestId("shaping-rail-collapse"));

    await waitFor(() => expect(ghosts()).toHaveLength(0));
  });

  it("[AL-331] an already-resolved proposal previews nothing", async () => {
    await openRailWithProposal(ADDITION, { resolution: "applied" });

    expect(card().dataset.state).toBe("applied");
    expect(ghosts()).toHaveLength(0);
  });
});

// --- Apply (TDD §5.6) --------------------------------------------------------

describe("Apply", () => {
  it("[AL-331] swaps ghosts for real rows in one round trip, then offers view in path", async () => {
    await openRailWithProposal();
    expect(ghosts()).toHaveLength(2);

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));

    await waitFor(() => expect(card().dataset.state).toBe("applied"));
    expect(shapingApplyCount()).toBe(1);
    expect(screen.getByTestId("shaping-rail-proposal-applied").textContent).toBe(
      "Applied to your path.",
    );
    // The response's `path` went straight into the outline cache: no ghost is
    // left, and both titles are now ordinary rows the learner can see.
    await waitFor(() => expect(ghosts()).toHaveLength(0));
    expect(pathRail().textContent).toContain("`unknown` vs `any`");
    expect(pathRail().textContent).toContain("Narrowing `unknown`");
    // Applied rows are real path structure: 4 lessons became 6.
    expect(screen.getByTestId("path-progress").textContent).toBe("2 of 6 lessons complete");
  });

  it("[AL-331] shows an applying state while the write is in flight", async () => {
    configureShaping({ applyDelayMs: 40 });
    await openRailWithProposal();

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));

    await waitFor(() => expect(card().dataset.state).toBe("applying"));
    const button = screen.getByTestId("shaping-rail-proposal-apply") as HTMLButtonElement;
    expect(button.textContent).toBe("Applying…");
    expect(button.disabled).toBe(true);
    // A second tap mid-flight must not start a second write.
    fireEvent.click(button);
    await waitFor(() => expect(card().dataset.state).toBe("applied"));
    expect(shapingApplyCount()).toBe(1);
  });

  it("[AL-331] view in path stands out of the way of what just changed", async () => {
    await openRailWithProposal();
    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));
    await waitFor(() => expect(card().dataset.state).toBe("applied"));

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-view"));

    await waitFor(() => expect(screen.queryByTestId("shaping-rail")).toBeNull());
    // The way back is the mark, exactly as it was the way in.
    expect(screen.getByTestId("shaping-rail-mark")).toBeTruthy();
  });

  it("[AL-331] an outline poll already in flight cannot overwrite what Apply landed", async () => {
    // The path route polls `GET /paths/{id}` while any lesson is still
    // generating, so a poll can be in flight when Apply answers. That poll's
    // body is the path as it stood *before* the apply; resolving after Apply's
    // cache write, it would put the pre-apply snapshot back on top — the card
    // saying "applied" over a rail whose new rows had vanished. Apply cancels
    // the outline query before writing, which is what makes that impossible.
    const pollStarted = deferred();
    const pollHeld = deferred();
    let polls = 0;
    server.use(
      http.get(`${API_V1_BASE}/paths/:id`, async ({ params }) => {
        const detail = pathDetailFor(params.id as string);
        if (detail === undefined) {
          return HttpResponse.json(
            { error: { code: "not_found", message: "Path not found." } },
            { status: 404 },
          );
        }
        polls += 1;
        if (polls === 1) return HttpResponse.json(detail);
        // The poll: its body is the outline as it stands *now* — snapshotted,
        // because the fake's units are live objects — and held open until the
        // test has applied on top of it.
        const snapshot = structuredClone(detail);
        pollStarted.resolve();
        await pollHeld.promise;
        return HttpResponse.json(snapshot);
      }),
    );

    await openRailWithProposal(ADDITION, { units: GENERATING_MID_PATH_UNITS });
    expect(screen.getByTestId("path-progress").textContent).toBe("2 of 4 lessons complete");
    await act(async () => {
      await pollStarted.promise;
    });

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));
    await waitFor(() => expect(card().dataset.state).toBe("applied"));
    expect(screen.getByTestId("path-progress").textContent).toBe("2 of 6 lessons complete");

    pollHeld.resolve();
    await settle();

    // The stale snapshot lost: the applied rows are still on the rail.
    expect(screen.getByTestId("path-progress").textContent).toBe("2 of 6 lessons complete");
    expect(pathRail().textContent).toContain("`unknown` vs `any`");
    expect(ghosts()).toHaveLength(0);
  });

  it("[AL-331] a row applied ahead of the learner takes the available slot", async () => {
    // Unlock state is *derived* from the single total order (`domains/progression`
    // — "available iff it is the first incomplete lesson in position_in_path
    // order"), so a row that lands ahead of where the learner is becomes the
    // available one and the row it displaced locks behind it.
    await openRailWithProposal({
      summary: "Adds 1 lesson before Function types.",
      operations: [
        {
          insert_at_position: 2,
          new_unit: null,
          lessons: [{ title: "Type predicates" }],
          rationale: "It has to come first.",
          estimated_minutes: 5,
        },
      ],
    });

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));
    await waitFor(() => expect(card().dataset.state).toBe("applied"));

    await waitFor(() =>
      expect(
        screen.getByTestId("lesson-l2000000-0000-4000-8000-000000000003").dataset.unlockState,
      ).toBe("locked"),
    );
    const available = within(pathRail())
      .getAllByRole("button")
      .filter((row) => row.dataset.unlockState === "available");
    expect(available).toHaveLength(1);
    expect(available[0].textContent).toContain("Type predicates");
  });

  it("[AL-331] a Revision's applied lesson goes back to ungenerated, keeping its slot", async () => {
    await openRailWithProposal(REVISION);

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));

    await waitFor(() => expect(card().dataset.state).toBe("applied"));
    const target = await screen.findByTestId("lesson-l2000000-0000-4000-8000-000000000004");
    await waitFor(() => expect(target.dataset.generationState).toBe("ungenerated"));
    // The marker goes with the offer it belonged to.
    expect(target.dataset.revising).toBeUndefined();
  });
});

// --- The coded 409s on apply (docs/api.md) -----------------------------------

describe("Apply — coded conflicts", () => {
  it("[AL-331] a stale reason renders the server's words and offers ask again", async () => {
    configureShaping({
      applyConflict: {
        reason: "positions_shifted",
        message: "Something else changed this path since I suggested that.",
      },
    });
    await openRailWithProposal();

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));

    await waitFor(() => expect(card().dataset.state).toBe("stale"));
    // The `409`'s message is written for a learner, so it is used verbatim.
    expect(screen.getByTestId("shaping-rail-proposal-stale").textContent).toBe(
      "Something else changed this path since I suggested that.",
    );
    expect(screen.queryByTestId("shaping-rail-proposal-apply")).toBeNull();
    // A stale preview is a claim about a path that has moved — it goes with the
    // offer, in the same breath.
    expect(ghosts()).toHaveLength(0);

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-ask-again"));

    // "Ask again" hands the learner's own question back to the composer.
    await waitFor(() =>
      expect((screen.getByTestId("shaping-rail-input") as HTMLTextAreaElement).value).toBe(
        "Add practice on narrowing",
      ),
    );
    // And nothing else: the card still says *why* this offer died, which is the
    // one thing the learner needs before re-asking. §8 has no retired state.
    expect(card().dataset.state).toBe("stale");
    expect(screen.getByTestId("shaping-rail-proposal-stale").textContent).toBe(
      "Something else changed this path since I suggested that.",
    );
  });

  it("[AL-331] `revision_target_engaged` is stale too — the boundary held", async () => {
    configureShaping({
      applyConflict: {
        reason: "revision_target_engaged",
        message: "You've started that lesson since, so I'll leave it as it is.",
      },
    });
    await openRailWithProposal(REVISION);

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));

    await waitFor(() => expect(card().dataset.state).toBe("stale"));
    expect(screen.getByTestId("shaping-rail-proposal-stale").textContent).toContain(
      "You've started that lesson since",
    );
  });

  it("[AL-331] `target_generating` keeps the card pending — the same tap works shortly", async () => {
    configureShaping({
      applyConflict: {
        reason: "target_generating",
        message: "That lesson is being written right now. Try again in a moment.",
      },
    });
    await openRailWithProposal();

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));

    await waitFor(() =>
      expect(screen.getByTestId("shaping-rail-proposal-notice").textContent).toContain(
        "Try again in a moment",
      ),
    );
    expect(card().dataset.state).toBe("pending");
    // Retryable means retryable: the button is still there, and it works.
    configureShaping({ applyConflict: null });
    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));
    await waitFor(() => expect(card().dataset.state).toBe("applied"));
    expect(shapingApplyCount()).toBe(2);
  });

  it("[AL-331] `already_applied` settles the card instead of erroring it", async () => {
    configureShaping({
      applyConflict: { reason: "already_applied", message: "That change is already in your path." },
    });
    await openRailWithProposal();

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));

    await waitFor(() => expect(card().dataset.state).toBe("applied"));
  });

  it("[AL-331] a plain 500 leaves the Proposal on the card to retry", async () => {
    configureShaping({ applyFails: true });
    await openRailWithProposal();

    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));

    await waitFor(() => expect(screen.getByTestId("shaping-rail-proposal-notice")).toBeTruthy());
    // Nothing was applied and nothing was lost: PRD §5.7's "whole or not at all".
    expect(card().dataset.state).toBe("pending");
    expect(screen.getByTestId("shaping-rail-proposal-apply")).toBeTruthy();
    expect(ghosts()).toHaveLength(2);
  });
});

// --- The Change history sheet (PRD §5.5) -------------------------------------

function change(id: string, overrides: Partial<Change> = {}): Change {
  return {
    id,
    summary: `Added 2 lessons on \`unknown\` (${id})`,
    kinds: ["add_lessons"],
    status: "applied",
    applied_at: "2026-07-30T12:00:00Z",
    undone_at: null,
    ...overrides,
  };
}

describe("Change history sheet", () => {
  it("[AL-331] costs no request until a learner opens it", async () => {
    seedChanges(PATH_ID, [change("c1")]);
    await openRailWithProposal();

    expect(shapingChangeReadCount()).toBe(0);

    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));

    await screen.findByTestId("shaping-rail-history");
    await waitFor(() => expect(shapingChangeReadCount()).toBe(1));
    expect(screen.getAllByTestId("shaping-rail-history-change")).toHaveLength(1);
  });

  it("[AL-331] lists undone Changes too — undo is a status, never a delete", async () => {
    seedChanges(PATH_ID, [
      change("c2", { status: "undone", undone_at: "2026-07-30T13:00:00Z" }),
      change("c1"),
    ]);
    await openRailWithProposal();
    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));

    const rows = await screen.findAllByTestId("shaping-rail-history-change");
    expect(rows.map((row) => row.dataset.status)).toEqual(["undone", "applied"]);
    expect(within(rows[0]).getByTestId("shaping-rail-history-status").textContent).toBe("Undone");
  });

  it("[AL-331] dates a Change from an earlier year with its year", async () => {
    // A bare "Jan 15" on a path shaped over two years is a date the learner
    // cannot place. Within this year the year is noise, so it stays off.
    const thisYear = String(new Date().getFullYear());
    seedChanges(PATH_ID, [
      change("c2", { applied_at: new Date().toISOString() }),
      change("c1", { applied_at: "2020-01-15T12:00:00Z" }),
    ]);
    await openRailWithProposal();
    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));

    const stamps = (await screen.findAllByTestId("shaping-rail-history-when")).map(
      (each) => each.textContent,
    );
    expect(stamps[0]).not.toContain(thisYear);
    expect(stamps[1]).toContain("2020");
  });

  it("[AL-331] says so plainly when nothing has shaped the path", async () => {
    await openRailWithProposal();
    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));

    const empty = await screen.findByTestId("shaping-rail-history-empty");
    expect(empty.textContent).toContain("Nothing has shaped this path yet");
  });

  it("[AL-331] closing the sheet returns to the conversation it covered", async () => {
    await openRailWithProposal();
    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));
    await screen.findByTestId("shaping-rail-history");
    expect(screen.queryByTestId("shaping-rail-proposal")).toBeNull();

    fireEvent.click(screen.getByTestId("shaping-rail-history-close"));

    await screen.findByTestId("shaping-rail-proposal");
    expect(screen.queryByTestId("shaping-rail-history")).toBeNull();
  });

  it("[AL-331] survives new conversation — history belongs to the path", async () => {
    seedChanges(PATH_ID, [change("c1")]);
    await openRailWithProposal();

    fireEvent.click(screen.getByTestId("shaping-rail-new-conversation"));
    fireEvent.click(screen.getByTestId("shaping-rail-new-conversation-confirm"));
    await waitFor(() => expect(shapingClearCount()).toBe(1));
    await waitFor(() => expect(screen.queryByTestId("shaping-rail-proposal")).toBeNull());

    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));

    expect(await screen.findAllByTestId("shaping-rail-history-change")).toHaveLength(1);
  });

  it("[AL-331] an unreadable history says so rather than showing an empty record", async () => {
    configureShaping({ changesFail: true });
    await openRailWithProposal();

    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));

    // `retry: 1` on the app's query client, so the error state costs a retry
    // and its backoff before it lands.
    expect(
      await screen.findByTestId("shaping-rail-history-error", {}, { timeout: 5000 }),
    ).toBeTruthy();
    expect(screen.queryByTestId("shaping-rail-history-empty")).toBeNull();
  });
});

// --- Undo (TDD §5.7) ---------------------------------------------------------

describe("Undo", () => {
  async function openHistory(changes: Change[]): Promise<void> {
    seedChanges(PATH_ID, changes);
    await openRailWithProposal();
    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));
    await screen.findAllByTestId("shaping-rail-history-change");
  }

  it("[AL-331] offers undo on the newest live Change only", async () => {
    await openHistory([change("c2"), change("c1")]);

    const rows = screen.getAllByTestId("shaping-rail-history-change");
    expect(within(rows[0]).getByTestId("shaping-rail-history-undo")).toBeTruthy();
    // Not an error and not a refusal: it is simply not on top of the stack.
    expect(within(rows[1]).getByTestId("shaping-rail-history-not-latest").textContent).toBe(
      "Undo the newest change first.",
    );
    expect(within(rows[1]).queryByTestId("shaping-rail-history-undo")).toBeNull();
  });

  it("[AL-331] an undone Change on top does not block the one below it", async () => {
    await openHistory([change("c2", { status: "undone", undone_at: "x" }), change("c1")]);

    const rows = screen.getAllByTestId("shaping-rail-history-change");
    expect(within(rows[0]).queryByTestId("shaping-rail-history-undo")).toBeNull();
    expect(within(rows[1]).getByTestId("shaping-rail-history-undo")).toBeTruthy();
  });

  it("[AL-331] undoing restores the path and marks the row undone", async () => {
    await openRailWithProposal();
    // Apply for real, so undo has something of its own to take back.
    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));
    await waitFor(() =>
      expect(screen.getByTestId("path-progress").textContent).toBe("2 of 6 lessons complete"),
    );

    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));
    const row = (await screen.findAllByTestId("shaping-rail-history-change"))[0];
    fireEvent.click(within(row).getByTestId("shaping-rail-history-undo"));

    await waitFor(() => expect(shapingUndoCount()).toBe(1));
    await waitFor(() =>
      expect(screen.getAllByTestId("shaping-rail-history-change")[0].dataset.status).toBe("undone"),
    );
    // Restored exactly: the rows the Change added are gone.
    await waitFor(() =>
      expect(screen.getByTestId("path-progress").textContent).toBe("2 of 4 lessons complete"),
    );
    expect(pathRail().textContent).not.toContain("`unknown` vs `any`");
  });

  it("[AL-331] `409 engaged` closes the window plainly instead of hiding the button", async () => {
    configureShaping({
      undoConflict: {
        reason: "engaged",
        message: "You've started one of these lessons, so this change is part of your path now.",
      },
    });
    await openHistory([change("c1")]);

    const row = screen.getAllByTestId("shaping-rail-history-change")[0];
    fireEvent.click(within(row).getByTestId("shaping-rail-history-undo"));

    await waitFor(() =>
      expect(screen.getByTestId("shaping-rail-history-undo-error").textContent).toContain(
        "part of your path now",
      ),
    );
    // Closed for good — the Change stays applied and stops offering undo.
    expect(screen.getAllByTestId("shaping-rail-history-change")[0].dataset.status).toBe("applied");
    expect(screen.queryByTestId("shaping-rail-history-undo")).toBeNull();
  });

  it("[AL-331] `409 not_latest` says which one to undo first, and keeps the affordance", async () => {
    configureShaping({
      undoConflict: { reason: "not_latest", message: "Undo the later change first." },
    });
    await openHistory([change("c1")]);

    const row = screen.getAllByTestId("shaping-rail-history-change")[0];
    fireEvent.click(within(row).getByTestId("shaping-rail-history-undo"));

    await waitFor(() =>
      expect(screen.getByTestId("shaping-rail-history-undo-error").textContent).toBe(
        "Undo the later change first.",
      ),
    );
    expect(screen.getByTestId("shaping-rail-history-undo")).toBeTruthy();
  });

  it("[AL-331] undoing one Change leaves the other applied Change's card alone", async () => {
    // Resolution is per *message*: a live `path_changes` row references exactly
    // the message whose Proposal made it (TDD §4), so undoing the newest Change
    // says nothing about the one below it — its card is still applied.
    useSession(shapingOnSession);
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    seedShapingConversation(PATH_ID, [
      { id: "m1", role: "learner", content: "Add practice on narrowing" },
      { id: "m2", role: "tutor", content: "Here's what I'd add.", proposal: ADDITION },
      { id: "m3", role: "learner", content: "And re-teach the last one" },
      { id: "m4", role: "tutor", content: "Here's that too.", proposal: REVISION },
    ]);
    window.history.pushState({}, "", `/paths/${PATH_ID}`);
    render(<App />);
    await screen.findByTestId("path-view");
    fireEvent.click(await screen.findByTestId("shaping-rail-mark"));
    await waitFor(() => expect(screen.getAllByTestId("shaping-rail-proposal")).toHaveLength(2));

    // Oldest card first, so its Change is the older of the two.
    fireEvent.click(screen.getAllByTestId("shaping-rail-proposal-apply")[0]);
    await waitFor(() =>
      expect(screen.getAllByTestId("shaping-rail-proposal-apply")).toHaveLength(1),
    );
    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));
    await waitFor(() =>
      expect(
        screen.getAllByTestId("shaping-rail-proposal").map((each) => each.dataset.state),
      ).toEqual(["applied", "applied"]),
    );

    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));
    const newest = (await screen.findAllByTestId("shaping-rail-history-change"))[0];
    fireEvent.click(within(newest).getByTestId("shaping-rail-history-undo"));
    await waitFor(() => expect(shapingUndoCount()).toBe(1));
    fireEvent.click(screen.getByTestId("shaping-rail-history-close"));

    await waitFor(() =>
      expect(
        screen.getAllByTestId("shaping-rail-proposal").map((each) => each.dataset.state),
      ).toEqual(["applied", "undone"]),
    );
  });

  it("[AL-331] an undone Change's card reads undone on the next thread read", async () => {
    await openRailWithProposal();
    fireEvent.click(screen.getByTestId("shaping-rail-proposal-apply"));
    await waitFor(() => expect(card().dataset.state).toBe("applied"));

    fireEvent.click(screen.getByTestId("shaping-rail-change-history"));
    const row = (await screen.findAllByTestId("shaping-rail-history-change"))[0];
    fireEvent.click(within(row).getByTestId("shaping-rail-history-undo"));
    await waitFor(() => expect(shapingUndoCount()).toBe(1));
    fireEvent.click(screen.getByTestId("shaping-rail-history-close"));

    await waitFor(() => expect(card().dataset.state).toBe("undone"));
    expect(screen.getByTestId("shaping-rail-proposal-ask-again")).toBeTruthy();
  });
});

// --- Dark by default (AL-370 flips it) ---------------------------------------

describe("The flag still gates all of it", () => {
  it("[AL-331] a learner without the flag costs no apply, undo or history request", async () => {
    useSession(shapingOffSession);
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "some_experience", units: MID_PATH_UNITS });
    seedChanges(PATH_ID, [change("c1")]);
    seedShapingConversation(PATH_ID, [
      { role: "tutor", content: "Here's what I'd add.", proposal: ADDITION },
    ]);
    window.history.pushState({}, "", `/paths/${PATH_ID}`);
    render(<App />);
    await screen.findByTestId("path-rail");

    expect(screen.queryByTestId("shaping-rail-mark")).toBeNull();
    expect(shapingChangeReadCount()).toBe(0);
    expect(shapingApplyCount()).toBe(0);
    expect(shapingUndoCount()).toBe(0);
    // And no ghost rows: a dark surface previews nothing either.
    expect(ghosts()).toHaveLength(0);
    expect(shapingChanges(PATH_ID)).toHaveLength(1);
  });
});
