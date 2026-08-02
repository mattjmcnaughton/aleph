import { skipToken } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { configurePaths, seedPath } from "../mocks/paths";
import { progressReceivedOffsets, progressRequestCount } from "../mocks/progress";
import {
  ApiError,
  PATHS_LIST_QUERY_KEY,
  PATHS_QUERY_PREFIX,
  PROGRESS_QUERY_PREFIX,
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
  isValidationError,
  pathQueryKey,
  progressSummaryQueryKey,
  progressSummaryQueryOptions,
  updatePathTitle,
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
    title: "TypeScript",
    guidance: null,
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
      title: "TypeScript",
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

describe("the paths query keys", () => {
  const pathId = "11111111-1111-4111-8111-111111111111";

  it("never collide with each other (ids are UUIDs, not 'list')", () => {
    expect(PATHS_LIST_QUERY_KEY).toEqual(["paths", "list"]);
    expect(pathQueryKey(pathId)).not.toEqual(PATHS_LIST_QUERY_KEY);
  });

  it("[AL-090] both sit under the prefix a completion invalidates", () => {
    // `invalidateQueries({ queryKey: PATHS_QUERY_PREFIX })` matches by prefix,
    // so completion reaches the switcher list and every cached path detail only
    // as long as both keys start with it (routes/lessons.$lessonId.tsx, W1).
    const depth = PATHS_QUERY_PREFIX.length;
    expect(PATHS_LIST_QUERY_KEY.slice(0, depth)).toEqual([...PATHS_QUERY_PREFIX]);
    expect(pathQueryKey(pathId).slice(0, depth)).toEqual([...PATHS_QUERY_PREFIX]);
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

describe("isValidationError", () => {
  it("[AL-065] is true for the 422 validation_error envelope", () => {
    expect(isValidationError(new ApiError("bad model", 422, "validation_error"))).toBe(true);
  });

  it("[AL-065] is false for the neighbouring create rejections and non-errors", () => {
    // 403 (non-admin override) and 429 (daily cap) have their own surfaces.
    expect(isValidationError(new ApiError("nope", 403, "forbidden"))).toBe(false);
    expect(isValidationError(new ApiError("capped", 429, "rate_limited"))).toBe(false);
    expect(isValidationError(new Error("plain"))).toBe(false);
    expect(isValidationError(undefined)).toBe(false);
  });
});

describe("updatePathTitle", () => {
  it("PATCHes the path and returns the full detail (the poll target's shape)", async () => {
    seedPath({ id: "p-rename", topic: "TypeScript", level: "new_to_it" });

    const updated = await updatePathTitle({ pathId: "p-rename", title: "TS from scratch" });

    expect(updated.id).toBe("p-rename");
    expect(updated.title).toBe("TS from scratch");
    // The topic never moves — it is frozen, and this endpoint cannot touch it.
    expect(updated.topic).toBe("TypeScript");
  });

  it("raises the shared ApiError on a server failure", async () => {
    seedPath({ id: "p-rename-fail", topic: "TypeScript", level: "new_to_it" });
    configurePaths({ renameFails: true });

    await expect(updatePathTitle({ pathId: "p-rename-fail", title: "New name" })).rejects.toThrow(
      ApiError,
    );
  });

  // F10: the fake must not silently 200/no-op a blank or over-long title (the
  // real server 422s, `PathTitleStr`, docs/api.md) — a client that ever stops
  // trimming/capping before sending needs the fake to catch it, not paper over
  // it with the OLD title.
  it("raises a validation_error ApiError for a blank title, and does not rename", async () => {
    seedPath({ id: "p-rename-blank", topic: "TypeScript", level: "new_to_it" });

    const error = await updatePathTitle({ pathId: "p-rename-blank", title: "   " }).catch(
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(isValidationError(error)).toBe(true);
    const detail = await updatePathTitle({ pathId: "p-rename-blank", title: "still there" });
    expect(detail.title).toBe("still there"); // untouched by the rejected attempt
  });

  it("raises a validation_error ApiError for an over-long title", async () => {
    seedPath({ id: "p-rename-long", topic: "TypeScript", level: "new_to_it" });

    const error = await updatePathTitle({
      pathId: "p-rename-long",
      title: "x".repeat(201),
    }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(isValidationError(error)).toBe(true);
  });
});

// Streaks TDD §7/§8: the summary's own query namespace, and the one call site
// for `getTimezoneOffset()` — §15's own words for why this matters: "the sign
// convention is the highest-consequence, lowest-visibility surface here."

describe("progressSummaryQueryKey", () => {
  it("sits under PROGRESS_QUERY_PREFIX, never PATHS_QUERY_PREFIX (D10)", () => {
    const key = progressSummaryQueryKey(-120);
    expect(key).toEqual(["progress", "summary", -120]);
    const depth = PROGRESS_QUERY_PREFIX.length;
    expect(key.slice(0, depth)).toEqual([...PROGRESS_QUERY_PREFIX]);
  });

  it("keys different offsets apart — a timezone/DST crossing is a cache miss (§7)", () => {
    expect(progressSummaryQueryKey(-120)).not.toEqual(progressSummaryQueryKey(300));
  });
});

describe("progressSummaryQueryOptions", () => {
  it("[TDD §8/§15] reads the offset from exactly one `getTimezoneOffset()` call", () => {
    const spy = vi.spyOn(Date.prototype, "getTimezoneOffset").mockReturnValue(-120);

    const options = progressSummaryQueryOptions(true);

    expect(spy).toHaveBeenCalledTimes(1);
    expect(options.queryKey).toEqual(["progress", "summary", -120]);
  });

  it("skipToken when disabled — no flag, no request (TDD §8)", () => {
    const options = progressSummaryQueryOptions(false);
    expect(options.queryFn).toBe(skipToken);
  });

  it("a real fetcher when enabled, sending that same offset on the wire", async () => {
    vi.spyOn(Date.prototype, "getTimezoneOffset").mockReturnValue(-120);
    const options = progressSummaryQueryOptions(true);
    expect(options.queryFn).not.toBe(skipToken);

    // @ts-expect-error queryOptions types its queryFn generically; this file
    // knows it is a concrete `() => Promise<ProgressSummary>` when enabled.
    await options.queryFn();

    expect(progressRequestCount()).toBe(1);
    expect(progressReceivedOffsets()).toEqual([-120]);
  });
});
