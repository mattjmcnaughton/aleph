import { describe, expect, it } from "vitest";
import type { PathSummary } from "./api";
import type { FlowRecord } from "./flow";
import { advanceRecord, eligiblePaths, lessonsLeft, pickNext } from "./flow-order";

// Pure-function coverage for flow TDD §5.1-§5.3 — no storage, no query
// client, no real `Math.random`: every rng here is a seeded stub, and every
// record is a plain literal built by `record()` below.

function summary(
  id: string,
  opts: {
    status?: PathSummary["status"];
    nextLessonId?: string | null;
    total?: number;
    completed?: number;
  } = {},
): PathSummary {
  const nextLessonId = opts.nextLessonId === undefined ? `${id}-next` : opts.nextLessonId;
  return {
    id,
    topic: `topic-${id}`,
    title: `Path ${id}`,
    level: "new_to_it",
    status: opts.status ?? "ready",
    progress: {
      total_lessons: opts.total ?? 10,
      generated_lessons: opts.total ?? 10,
      completed_lessons: opts.completed ?? 0,
    },
    last_activity_at: null,
    next_lesson:
      nextLessonId === null
        ? null
        : { id: nextLessonId, title: `Lesson for ${id}`, position_in_path: 1 },
  };
}

function record(overrides: Partial<FlowRecord> = {}): FlowRecord {
  return {
    version: 1,
    startedAt: "2026-01-01T00:00:00.000Z",
    length: 5,
    order: "interleave",
    paths: ["a", "b"],
    cursor: 0,
    current: null,
    next: null,
    completed: [],
    endedReason: null,
    ...overrides,
  };
}

/** A stub rng that returns a fixed, pre-seeded sequence of `[0, 1)` values. */
function seededRng(values: number[]): () => number {
  let i = 0;
  return () => {
    const value = values[i % values.length];
    i += 1;
    return value;
  };
}

describe("eligiblePaths", () => {
  it("keeps ready paths with a next lesson, in scope order — not summaries' order", () => {
    const summaries = [summary("b"), summary("a"), summary("c")];
    expect(eligiblePaths(summaries, ["a", "b"]).map((p) => p.id)).toEqual(["a", "b"]);
  });

  it("drops a path outside the scope entirely", () => {
    const summaries = [summary("a"), summary("z")];
    expect(eligiblePaths(summaries, ["a"]).map((p) => p.id)).toEqual(["a"]);
  });

  it("drops a finished path (next_lesson === null) even when it is in scope", () => {
    const summaries = [summary("a", { nextLessonId: null }), summary("b")];
    expect(eligiblePaths(summaries, ["a", "b"]).map((p) => p.id)).toEqual(["b"]);
  });

  it("drops a path that is not status === 'ready' (generating, failed, refused)", () => {
    const summaries = [summary("a", { status: "generating" }), summary("b")];
    expect(eligiblePaths(summaries, ["a", "b"]).map((p) => p.id)).toEqual(["b"]);
  });

  it("a scope id absent from summaries is silently skipped, not an error", () => {
    expect(eligiblePaths([summary("a")], ["missing", "a"]).map((p) => p.id)).toEqual(["a"]);
  });
});

describe("lessonsLeft", () => {
  it("is total minus completed", () => {
    expect(lessonsLeft(summary("a", { total: 20, completed: 12 }))).toBe(8);
  });
});

describe("pickNext — interleave", () => {
  it("takes the path at the cursor when it is eligible", () => {
    const flow = record({ order: "interleave", paths: ["a", "b"], cursor: 0 });
    const eligible = [summary("a"), summary("b")];
    expect(pickNext(flow, eligible, seededRng([0]))).toEqual({
      next: { lessonId: "a-next", pathId: "a" },
      cursor: 1,
    });
  });

  it("wraps around the scope back to index 0", () => {
    const flow = record({ order: "interleave", paths: ["a", "b"], cursor: 1 });
    const eligible = [summary("a"), summary("b")];
    expect(pickNext(flow, eligible, seededRng([0]))).toEqual({
      next: { lessonId: "b-next", pathId: "b" },
      cursor: 0,
    });
  });

  it("skips a dry path in the middle of the scope and keeps wrapping", () => {
    // Scope is a, b, c; b has run dry (excluded from `eligible`), cursor is
    // sitting on b — the draw must skip it and land on c.
    const flow = record({ order: "interleave", paths: ["a", "b", "c"], cursor: 1 });
    const eligible = [summary("a"), summary("c")];
    expect(pickNext(flow, eligible, seededRng([0]))).toEqual({
      next: { lessonId: "c-next", pathId: "c" },
      cursor: 0, // (scopeIndex 2 + 1) wraps mod 3
    });
  });

  it("returns null when the whole scope has run dry", () => {
    const flow = record({ order: "interleave", paths: ["a", "b"], cursor: 0 });
    expect(pickNext(flow, [], seededRng([0]))).toBeNull();
  });
});

describe("pickNext — serial", () => {
  it("sticks with the first eligible path in scope order, ignoring the cursor", () => {
    const flow = record({ order: "serial", paths: ["a", "b"], cursor: 1 });
    const eligible = [summary("a"), summary("b")];
    // Cursor points at "b", but serial always re-derives from scope order —
    // "a" is still eligible, so "a" is still the pick.
    expect(pickNext(flow, eligible, seededRng([0]))).toEqual({
      next: { lessonId: "a-next", pathId: "a" },
      cursor: 0,
    });
  });

  it("moves to the next path in scope order once the first runs dry", () => {
    const flow = record({ order: "serial", paths: ["a", "b"], cursor: 0 });
    const eligible = [summary("b")]; // "a" has run dry
    expect(pickNext(flow, eligible, seededRng([0]))).toEqual({
      next: { lessonId: "b-next", pathId: "b" },
      cursor: 1,
    });
  });

  it("returns null once every scoped path has run dry", () => {
    const flow = record({ order: "serial", paths: ["a", "b"], cursor: 1 });
    expect(pickNext(flow, [], seededRng([0]))).toBeNull();
  });
});

describe("pickNext — random", () => {
  it("never repeats the current path when two or more are eligible", () => {
    const flow = record({
      order: "random",
      paths: ["a", "b"],
      current: { lessonId: "a-prev", pathId: "a" },
    });
    const eligible = [summary("a"), summary("b")];
    // rng of 0 would draw index 0 of the *full* pool ("a") — proving the
    // exclusion actually narrows the pool rather than merely filtering the
    // draw after the fact.
    expect(pickNext(flow, eligible, seededRng([0]))).toEqual({
      next: { lessonId: "b-next", pathId: "b" },
      cursor: 0,
    });
  });

  it("does repeat the current path when it is the only one eligible", () => {
    const flow = record({
      order: "random",
      paths: ["a", "b"],
      current: { lessonId: "a-prev", pathId: "a" },
    });
    const eligible = [summary("a")]; // "b" has run dry
    expect(pickNext(flow, eligible, seededRng([0]))).toEqual({
      next: { lessonId: "a-next", pathId: "a" },
      cursor: 0,
    });
  });

  it("draws uniformly over the pool per the rng's fraction", () => {
    const flow = record({ order: "random", paths: ["a", "b", "c"], current: null });
    const eligible = [summary("a"), summary("b"), summary("c")];
    // 0.9 * 3 = 2.7 -> floor 2 -> the third entry, "c".
    expect(pickNext(flow, eligible, seededRng([0.9]))?.next.pathId).toBe("c");
  });

  it("returns null when the scope is dry", () => {
    const flow = record({ order: "random", paths: ["a"], current: null });
    expect(pickNext(flow, [], seededRng([0]))).toBeNull();
  });
});

describe("advanceRecord", () => {
  const completion = {
    lessonId: "l1",
    pathId: "a",
    title: "Lesson one",
    outcome: "correct" as const,
  };

  it("ends on reaching length, even though a path is still eligible", () => {
    const flow = record({ length: 1, current: { lessonId: "l1", pathId: "a" } });
    const result = advanceRecord(flow, completion, [summary("a"), summary("b")], seededRng([0]));
    expect(result.endedReason).toBe("length");
    expect(result.completed).toEqual([completion]);
    expect(result.current).toBeNull();
    expect(result.next).toBeNull();
  });

  it("never ends on length for an open-ended flow (length null)", () => {
    const flow = record({
      length: null,
      current: { lessonId: "l1", pathId: "a" },
      paths: ["a"],
    });
    const result = advanceRecord(flow, completion, [summary("a")], seededRng([0]));
    expect(result.endedReason).toBeNull();
    expect(result.current).toEqual({ lessonId: "a-next", pathId: "a" });
  });

  it("ends with 'dry' once the scope has nothing left, before reaching length", () => {
    const flow = record({
      length: 5,
      current: { lessonId: "l1", pathId: "a" },
      paths: ["a"],
    });
    // "a" itself is now finished (next_lesson null) — the only scoped path.
    const result = advanceRecord(
      flow,
      completion,
      [summary("a", { nextLessonId: null })],
      seededRng([0]),
    );
    expect(result.endedReason).toBe("dry");
    expect(result.current).toBeNull();
  });

  it("prefers the pre-drawn look-ahead when it is still eligible (D8)", () => {
    const flow = record({
      length: 5,
      paths: ["a", "b"],
      current: { lessonId: "l1", pathId: "a" },
      next: { lessonId: "b-next", pathId: "b" },
      cursor: 7, // deliberately not what a fresh pickNext would return
    });
    const result = advanceRecord(flow, completion, [summary("a"), summary("b")], seededRng([0]));
    expect(result.current).toEqual({ lessonId: "b-next", pathId: "b" });
    expect(result.next).toBeNull();
    // Reusing the look-ahead does not re-run pickNext, so the cursor it
    // already carried survives untouched.
    expect(result.cursor).toBe(7);
  });

  it("falls back to a fresh pickNext when the look-ahead went stale (§9)", () => {
    const flow = record({
      length: 5,
      order: "serial",
      paths: ["a", "b"],
      current: { lessonId: "l1", pathId: "a" },
      // Pre-drawn against "b", but the fresh list below shows "b" ran dry in
      // the meantime (completed from another tab) — must not be reused.
      next: { lessonId: "b-next", pathId: "b" },
    });
    const result = advanceRecord(
      flow,
      completion,
      [summary("a"), summary("b", { nextLessonId: null })],
      seededRng([0]),
    );
    // Serial re-derives from scope order: "a" is still eligible and first.
    expect(result.current).toEqual({ lessonId: "a-next", pathId: "a" });
  });
});
