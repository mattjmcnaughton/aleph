import { describe, expect, it } from "vitest";
import {
  ApiError,
  type LessonGenerationState,
  type LessonUnlockState,
  type PathDetail,
  type PathStatus,
  isNotFound,
  isPathViewTerminal,
} from "./api";

// Terminal-state predicates for the path-view poll (TDD §5.4/§14). These decide
// when the shared polling helper stops, so they carry real reachable-state risk:
// stop too early and the learner strands on an available-but-empty lesson.

function detail(
  status: PathStatus,
  lessons: { unlock_state: LessonUnlockState; generation_state: LessonGenerationState }[],
): PathDetail {
  return {
    id: "p1",
    topic: "TypeScript",
    level: "new_to_it",
    status,
    refusal_message: null,
    progress: { total_lessons: lessons.length, generated_lessons: 0, completed_lessons: 0 },
    units: [
      {
        id: "u1",
        title: "Unit",
        lessons: lessons.map((l, i) => ({
          id: `l${i}`,
          title: `Lesson ${i}`,
          position_in_path: i,
          generation_state: l.generation_state,
          unlock_state: l.unlock_state,
        })),
      },
    ],
  };
}

describe("isPathViewTerminal", () => {
  it("is non-terminal while the outline itself is still generating", () => {
    expect(isPathViewTerminal(detail("generating", []))).toBe(false);
  });

  it("is non-terminal for undefined data (nothing fetched yet)", () => {
    expect(isPathViewTerminal(undefined)).toBe(false);
  });

  it("is terminal for refused / failed outlines (no rail to settle)", () => {
    expect(isPathViewTerminal(detail("refused", []))).toBe(true);
    expect(isPathViewTerminal(detail("failed", []))).toBe(true);
  });

  it("keeps polling a ready outline while a lesson is still generating (prefetch, §14)", () => {
    expect(
      isPathViewTerminal(
        detail("ready", [
          { unlock_state: "complete", generation_state: "generated" },
          { unlock_state: "locked", generation_state: "generating" },
        ]),
      ),
    ).toBe(false);
  });

  it("keeps polling the reachable available-but-ungenerated gap (ready precedes the claim)", () => {
    // poll_path spawns the resume THEN snapshots, so a `ready` payload can carry
    // an `available` lesson whose content is not yet `generating`. Stopping here
    // would strand the learner — this is the bug the predicate must not have.
    expect(
      isPathViewTerminal(
        detail("ready", [{ unlock_state: "available", generation_state: "ungenerated" }]),
      ),
    ).toBe(false);
  });

  it("is terminal once the available lesson's content is generated", () => {
    expect(
      isPathViewTerminal(
        detail("ready", [
          { unlock_state: "available", generation_state: "generated" },
          { unlock_state: "locked", generation_state: "ungenerated" },
        ]),
      ),
    ).toBe(true);
  });

  it("is terminal when an available lesson's generation has failed (no more polling helps)", () => {
    expect(
      isPathViewTerminal(
        detail("ready", [{ unlock_state: "available", generation_state: "failed" }]),
      ),
    ).toBe(true);
  });
});

describe("isNotFound", () => {
  it("is true only for an ApiError with status 404", () => {
    expect(isNotFound(new ApiError("gone", 404, "not_found"))).toBe(true);
  });

  it("is false for other ApiError statuses and non-ApiError values", () => {
    expect(isNotFound(new ApiError("boom", 500, "internal_error"))).toBe(false);
    expect(isNotFound(new Error("plain"))).toBe(false);
    expect(isNotFound(undefined)).toBe(false);
  });
});
