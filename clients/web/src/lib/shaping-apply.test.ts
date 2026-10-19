// The pure half of AL-331 (Phase 2B TDD §8, PRD §5.4–§5.5): everything the
// apply/undo surfaces decide *before* any DOM exists — the proposal card's state
// machine, the coded-`409` grouping, the client-side ghost merge, the cost line,
// and the LIFO undo derivation.
//
// They live in `lib/shaping.ts` as pure functions rather than inside the card and
// the sheet for the reason the rest of this codebase separates domain logic from
// I/O: a state machine with five inputs and six outputs deserves to be asserted
// directly, not through six renders. The integration tests
// (`app/shaping-apply.test.tsx`) then prove the wiring once each, instead of
// re-deriving the table through the UI.

import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import type { PathUnit } from "./api";
import {
  type Change,
  type ShapingConflictReason,
  conflictGroup,
  conflictReasonOf,
  mergeProposalIntoOutline,
  proposalCardState,
  proposalCostLine,
  undoableChangeId,
} from "./shaping";
import type { Proposal } from "./tutor-stream";

// --- Fixtures ----------------------------------------------------------------

/** Two units, four lessons, positions 1..4 — `MID_PATH_UNITS`' shape. */
const UNITS: PathUnit[] = [
  {
    id: "unit-1",
    title: "Foundations & types",
    lessons: [
      {
        id: "lesson-0",
        title: "What TypeScript adds",
        position_in_path: 1,
        generation_state: "generated",
        unlock_state: "complete",
      },
      {
        id: "lesson-1",
        title: "Primitive types",
        position_in_path: 2,
        generation_state: "generated",
        unlock_state: "complete",
      },
    ],
  },
  {
    id: "unit-2",
    title: "Functions & narrowing",
    lessons: [
      {
        id: "lesson-2",
        title: "Function types",
        position_in_path: 3,
        generation_state: "generated",
        unlock_state: "available",
      },
      {
        id: "lesson-3",
        title: "Narrowing",
        position_in_path: 4,
        generation_state: "ungenerated",
        unlock_state: "locked",
      },
    ],
  },
];

function addition(overrides: Partial<Proposal["operations"][number]> = {}): Proposal {
  return {
    summary: "Adds 2 lessons on `unknown` before Narrowing.",
    operations: [
      {
        insert_at_position: 4,
        new_unit: null,
        lessons: [{ title: "`unknown` vs `any`" }, { title: "Narrowing `unknown`" }],
        rationale: "You missed the narrowing check.",
        estimated_minutes: 10,
        ...overrides,
      },
    ],
  } as Proposal;
}

function revision(lessonId = "lesson-3"): Proposal {
  return {
    summary: "Revises Narrowing to assume closures are known.",
    operations: [
      {
        lesson_id: lessonId,
        instruction: "Re-teach assuming closures are known.",
        new_title: "Narrowing, with closures",
        rationale: "You already know closures.",
      },
    ],
  };
}

/** The shared error envelope a coded `409` arrives as (`errors.py`). */
function conflict(reason: string): ApiError {
  return new ApiError("This lesson has been started since.", 409, "conflict", "req-1", {
    reason,
  });
}

// --- The coded 409 (docs/api.md, `ShapingConflictReason`) ---------------------

describe("conflictReasonOf / conflictGroup", () => {
  it("[AL-331] reads `details.reason` off a coded 409", () => {
    expect(conflictReasonOf(conflict("positions_shifted"))).toBe("positions_shifted");
  });

  it("[AL-331] ignores a 409 whose reason it does not know", () => {
    // Forwards-compatible: a reason added server-side later must degrade to the
    // generic failure copy, never to a card state derived from a guess.
    expect(conflictReasonOf(conflict("reason_from_the_future"))).toBeNull();
    expect(conflictReasonOf(new ApiError("Boom.", 500, "internal_error"))).toBeNull();
    expect(conflictReasonOf(new Error("offline"))).toBeNull();
    // A 409 with no `details` at all is still just an unrecognised refusal.
    expect(conflictReasonOf(new ApiError("Nope.", 409, "conflict"))).toBeNull();
  });

  it("[AL-331] groups every reason the way the card and the sheet treat them", () => {
    // The five groups of `ShapingConflictReason`'s docstring, verbatim — read
    // off a reason, which is what every caller has by the time it asks.
    expect(conflictGroup("already_applied")).toBe("nothing_to_do");
    expect(conflictGroup("already_undone")).toBe("nothing_to_do");
    expect(conflictGroup("not_applied")).toBe("nothing_to_do");

    const askAgain: ShapingConflictReason[] = [
      "path_cap_reached",
      "insert_position_taken",
      "revision_target_engaged",
      "title_conflict",
      "positions_shifted",
      "invalid_proposal",
    ];
    for (const reason of askAgain) {
      expect(conflictGroup(reason)).toBe("ask_again");
    }

    expect(conflictGroup("target_generating")).toBe("retry");
    expect(conflictGroup("not_latest")).toBe("not_latest");
    expect(conflictGroup("engaged")).toBe("closed");
  });

  it("[AL-331] the two compose: a coded 409 goes reason-first, then group", () => {
    // How the hook and the card actually use them (`use-shaping-rail.ts`): the
    // reason is extracted once and stored, and the group is read off it.
    const reason = conflictReasonOf(conflict("engaged"));
    expect(reason).not.toBeNull();
    expect(reason && conflictGroup(reason)).toBe("closed");
  });
});

// --- The card state machine (TDD §8) -----------------------------------------

describe("proposalCardState", () => {
  const base = {
    resolution: "pending",
    applying: false,
    applied: false,
    dismissed: false,
    conflict: null,
  } as const;

  it("[AL-331] a fresh proposal is pending — Apply and Not now", () => {
    expect(proposalCardState(base)).toBe("pending");
  });

  it("[AL-331] an apply in flight is `applying`, whatever else is true", () => {
    expect(proposalCardState({ ...base, applying: true, dismissed: true })).toBe("applying");
  });

  it("[AL-331] the card that just applied is applied, before any refetch", () => {
    // Apply answers with the whole `ChangeDTO`, so the card knows it landed
    // without waiting for the thread read to re-derive `resolution` (TDD §4).
    expect(proposalCardState({ ...base, applied: true })).toBe("applied");
  });

  it("[AL-331] a server-derived `applied` resolution is applied on a revisit", () => {
    expect(proposalCardState({ ...base, resolution: "applied" })).toBe("applied");
  });

  it("[AL-331] `undone` resolution renders the undone state", () => {
    expect(proposalCardState({ ...base, resolution: "undone" })).toBe("undone");
  });

  it("[AL-331] `superseded` is a stale card — ask again, never Apply", () => {
    expect(proposalCardState({ ...base, resolution: "superseded" })).toBe("stale");
  });

  it("[AL-331] every ask-again reason renders stale", () => {
    expect(proposalCardState({ ...base, conflict: "positions_shifted" })).toBe("stale");
    expect(proposalCardState({ ...base, conflict: "revision_target_engaged" })).toBe("stale");
    expect(proposalCardState({ ...base, conflict: "path_cap_reached" })).toBe("stale");
  });

  it("[AL-331] `already_applied` and `already_undone` settle the card, not error it", () => {
    // Nothing to do: the double tap asked for a state the path is already in.
    expect(proposalCardState({ ...base, conflict: "already_applied" })).toBe("applied");
    expect(proposalCardState({ ...base, conflict: "already_undone" })).toBe("undone");
  });

  it("[AL-331] `target_generating` leaves the card pending — the tap works in a moment", () => {
    expect(proposalCardState({ ...base, conflict: "target_generating" })).toBe("pending");
  });

  it("[AL-331] Not now dismisses the offer and nothing else", () => {
    expect(proposalCardState({ ...base, dismissed: true })).toBe("dismissed");
    // A dismissal never hides what actually happened to the path.
    expect(proposalCardState({ ...base, dismissed: true, resolution: "applied" })).toBe("applied");
  });
});

// --- The cost line (PRD §5.4: "adds 2 lessons ≈ 10 min") ---------------------

describe("proposalCostLine", () => {
  it("[AL-331] states the scale of an Addition in lessons and minutes", () => {
    expect(proposalCostLine(addition())).toBe("Adds 2 lessons ≈ 10 min");
  });

  it("[AL-331] says one lesson, singular", () => {
    expect(proposalCostLine(addition({ lessons: [{ title: "Only one" }] }))).toBe(
      "Adds 1 lesson ≈ 10 min",
    );
  });

  it("[AL-331] a Revision changes no lesson count — it re-teaches a slot", () => {
    expect(proposalCostLine(revision())).toBe("Revises 1 lesson");
  });

  it("[AL-331] one Apply may carry both shapes", () => {
    const mixed: Proposal = {
      summary: "Adds one and revises one.",
      operations: [...addition().operations, ...revision().operations],
    };
    expect(proposalCostLine(mixed)).toBe("Adds 2 lessons ≈ 10 min · Revises 1 lesson");
  });
});

// --- Ghost rows (TDD D14: merged client-side, no preview endpoint) -----------

/** The flattened rail: one string per row, ghosts marked, so order is visible. */
function railShape(units: ReturnType<typeof mergeProposalIntoOutline>): string[] {
  return units.flatMap((unit) => [
    `${unit.ghost ? "ghost-unit" : "unit"}: ${unit.title}`,
    ...unit.lessons.map((row) =>
      row.kind === "ghost"
        ? `  ghost: ${row.title}`
        : `  ${row.revising ? "revising" : "real"}: ${row.lesson.title}`,
    ),
  ]);
}

describe("mergeProposalIntoOutline", () => {
  it("[AL-331] no pending proposal leaves the outline exactly as it came", () => {
    const merged = mergeProposalIntoOutline(UNITS, null);
    expect(railShape(merged)).toEqual([
      "unit: Foundations & types",
      "  real: What TypeScript adds",
      "  real: Primitive types",
      "unit: Functions & narrowing",
      "  real: Function types",
      "  real: Narrowing",
    ]);
  });

  it("[AL-331] an Addition previews in place, before the slot it names", () => {
    // `insert_at_position: 4` is Narrowing's `position_in_path`, so the ghosts
    // land where the applied lessons will: pushing Narrowing down.
    expect(railShape(mergeProposalIntoOutline(UNITS, addition()))).toEqual([
      "unit: Foundations & types",
      "  real: What TypeScript adds",
      "  real: Primitive types",
      "unit: Functions & narrowing",
      "  real: Function types",
      "  ghost: `unknown` vs `any`",
      "  ghost: Narrowing `unknown`",
      "  real: Narrowing",
    ]);
  });

  it("[AL-331] an Addition past the end of the path previews at the end", () => {
    const merged = mergeProposalIntoOutline(UNITS, addition({ insert_at_position: 5 }));
    expect(railShape(merged).slice(-3)).toEqual([
      "  real: Narrowing",
      "  ghost: `unknown` vs `any`",
      "  ghost: Narrowing `unknown`",
    ]);
  });

  it("[AL-331] a new unit previews as a ghost unit before the slot's unit", () => {
    const merged = mergeProposalIntoOutline(
      UNITS,
      addition({
        insert_at_position: 3,
        new_unit: { title: "Unknown & narrowing", summary: "The gap." },
      }),
    );
    expect(railShape(merged)).toEqual([
      "unit: Foundations & types",
      "  real: What TypeScript adds",
      "  real: Primitive types",
      "ghost-unit: Unknown & narrowing",
      "  ghost: `unknown` vs `any`",
      "  ghost: Narrowing `unknown`",
      "unit: Functions & narrowing",
      "  real: Function types",
      "  real: Narrowing",
    ]);
  });

  it("[AL-331] a new unit naming a mid-unit slot previews after the unit it splits", () => {
    // `insert_at_position: 4` is Narrowing — unit-2's *second* row, so there is
    // no unit boundary to land on. The backend re-derives unit order from lesson
    // order afterwards (`services/shaping.py::_apply_additions`), which puts the
    // new unit *after* the one it split; the preview says the same thing rather
    // than showing the learner an order Apply will not produce.
    const merged = mergeProposalIntoOutline(
      UNITS,
      addition({
        insert_at_position: 4,
        new_unit: { title: "Unknown & narrowing", summary: "The gap." },
      }),
    );
    expect(railShape(merged)).toEqual([
      "unit: Foundations & types",
      "  real: What TypeScript adds",
      "  real: Primitive types",
      "unit: Functions & narrowing",
      "  real: Function types",
      "  real: Narrowing",
      "ghost-unit: Unknown & narrowing",
      "  ghost: `unknown` vs `any`",
      "  ghost: Narrowing `unknown`",
    ]);
  });

  it("[AL-331] a new unit past the end of the path previews last", () => {
    const merged = mergeProposalIntoOutline(
      UNITS,
      addition({
        insert_at_position: 10,
        new_unit: { title: "Unknown & narrowing", summary: "The gap." },
      }),
    );
    expect(railShape(merged).slice(-3)).toEqual([
      "ghost-unit: Unknown & narrowing",
      "  ghost: `unknown` vs `any`",
      "  ghost: Narrowing `unknown`",
    ]);
  });

  it("[AL-331] a Revision marks the target row rather than adding one", () => {
    const merged = mergeProposalIntoOutline(UNITS, revision());
    expect(railShape(merged)).toEqual([
      "unit: Foundations & types",
      "  real: What TypeScript adds",
      "  real: Primitive types",
      "unit: Functions & narrowing",
      "  real: Function types",
      "  revising: Narrowing",
    ]);
  });

  it("[AL-331] a Revision naming a lesson that is not here marks nothing", () => {
    // The payload's snapshot can be older than the outline (a Change landed in
    // between). The merge is a preview, so it degrades to showing what it can.
    expect(railShape(mergeProposalIntoOutline(UNITS, revision("gone")))).toEqual(
      railShape(mergeProposalIntoOutline(UNITS, null)),
    );
  });

  it("[AL-331] several operations resolve against the *payload's* positions", () => {
    // Both Additions name position 3 in the snapshot they were drafted against,
    // so the second lands after the first — payload order, not a shifted frame.
    const both: Proposal = {
      summary: "Two additions at the same slot.",
      operations: [
        ...addition().operations,
        ...addition({ lessons: [{ title: "Third" }] }).operations,
      ],
    };
    expect(railShape(mergeProposalIntoOutline(UNITS, both)).slice(-4)).toEqual([
      "  ghost: `unknown` vs `any`",
      "  ghost: Narrowing `unknown`",
      "  ghost: Third",
      "  real: Narrowing",
    ]);
  });

  it("[AL-331] every ghost row carries a stable key of its own", () => {
    const merged = mergeProposalIntoOutline(UNITS, addition());
    const keys = merged
      .flatMap((unit) => unit.lessons)
      .filter((row) => row.kind === "ghost")
      .map((row) => (row.kind === "ghost" ? row.key : ""));
    expect(new Set(keys).size).toBe(keys.length);
  });
});

// --- Undo is last-in-first-out (docs/api.md, "Why undo is LIFO") -------------

describe("undoableChangeId", () => {
  function change(id: string, status: Change["status"]): Change {
    return {
      id,
      summary: `Change ${id}`,
      kinds: ["add_lessons"],
      status,
      applied_at: "2026-07-30T12:00:00Z",
      undone_at: status === "undone" ? "2026-07-30T12:05:00Z" : null,
    };
  }

  it("[AL-331] nothing is undoable on an unshaped path", () => {
    expect(undoableChangeId([])).toBeNull();
  });

  it("[AL-331] only the newest live Change may be undone", () => {
    // Newest first, as `GET /changes` serves it.
    const history = [change("c3", "applied"), change("c2", "applied"), change("c1", "applied")];
    expect(undoableChangeId(history)).toBe("c3");
  });

  it("[AL-331] an already-undone Change on top does not block the one below it", () => {
    const history = [change("c3", "undone"), change("c2", "applied"), change("c1", "applied")];
    expect(undoableChangeId(history)).toBe("c2");
  });

  it("[AL-331] a fully undone history offers no undo at all", () => {
    expect(undoableChangeId([change("c2", "undone"), change("c1", "undone")])).toBeNull();
  });
});
