import { describe, expect, it } from "vitest";
import {
  ApiError,
  PATHS_LIST_QUERY_KEY,
  type LessonDetail,
  type LessonGenerationState,
  type LessonUnlockState,
  type PathDetail,
  type PathStatus,
  type PathSummary,
  isLessonViewTerminal,
  isNotFound,
  isPathListTerminal,
  isPathViewTerminal,
  pathQueryKey,
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

describe("isLessonViewTerminal", () => {
  function lesson(
    unlock_state: LessonUnlockState,
    generation_state: LessonGenerationState,
  ): LessonDetail {
    return {
      id: "l1",
      path_id: "p1",
      title: "Lesson",
      position_in_path: 0,
      position_in_unit: 0,
      generation_state,
      unlock_state,
      read_passage: null,
      quick_check: null,
      attempt: null,
      generation_error: null,
    };
  }

  it("is non-terminal for undefined data (nothing fetched yet)", () => {
    expect(isLessonViewTerminal(undefined)).toBe(false);
  });

  it("is terminal for a locked lesson (no content to watch, regardless of state)", () => {
    expect(isLessonViewTerminal(lesson("locked", "generating"))).toBe(true);
    expect(isLessonViewTerminal(lesson("locked", "ungenerated"))).toBe(true);
  });

  it("keeps polling an available lesson until its generation resolves", () => {
    expect(isLessonViewTerminal(lesson("available", "ungenerated"))).toBe(false);
    expect(isLessonViewTerminal(lesson("available", "generating"))).toBe(false);
  });

  it("is terminal once generation is generated or failed", () => {
    expect(isLessonViewTerminal(lesson("available", "generated"))).toBe(true);
    expect(isLessonViewTerminal(lesson("available", "failed"))).toBe(true);
  });
});

describe("isPathListTerminal", () => {
  function summary(status: PathStatus): PathSummary {
    return {
      id: `p-${status}`,
      topic: "TypeScript",
      level: "new_to_it",
      status,
      progress: { total_lessons: 0, generated_lessons: 0, completed_lessons: 0 },
    };
  }

  it("is non-terminal for undefined data (nothing fetched yet)", () => {
    expect(isPathListTerminal(undefined)).toBe(false);
  });

  it("is terminal for an empty list (nothing left to resolve)", () => {
    expect(isPathListTerminal({ paths: [] })).toBe(true);
  });

  it("keeps refetching while any row is still pending or generating", () => {
    expect(isPathListTerminal({ paths: [summary("ready"), summary("generating")] })).toBe(false);
    expect(isPathListTerminal({ paths: [summary("pending")] })).toBe(false);
  });

  it("is terminal once every row is ready, failed, or refused", () => {
    expect(
      isPathListTerminal({ paths: [summary("ready"), summary("failed"), summary("refused")] }),
    ).toBe(true);
  });
});

describe("PATHS_LIST_QUERY_KEY", () => {
  it("never collides with a path-detail key (ids are UUIDs, not 'list')", () => {
    expect(PATHS_LIST_QUERY_KEY).toEqual(["paths", "list"]);
    expect(pathQueryKey("11111111-1111-4111-8111-111111111111")).not.toEqual(PATHS_LIST_QUERY_KEY);
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
