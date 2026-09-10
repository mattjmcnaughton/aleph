import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PathSummary } from "./api";
import {
  FLOW_STORAGE_KEY,
  NoEligiblePathError,
  clearFlow,
  readFlow,
  startFlow,
  useFlow,
  writeFlow,
} from "./flow";
import type { FlowRecord } from "./flow";

// Storage + store coverage for flow TDD §4 — `lib/flow-order.ts` owns the
// pure transitions and is tested on its own (`flow-order.test.ts`); this file
// is `sessionStorage` read/write/validate, the `useSyncExternalStore` wiring,
// and `startFlow`'s first-draw + look-ahead.

afterEach(() => {
  window.sessionStorage.clear();
});

function summary(
  id: string,
  opts: { nextLessonId?: string | null; total?: number; completed?: number } = {},
): PathSummary {
  const nextLessonId = opts.nextLessonId === undefined ? `${id}-next` : opts.nextLessonId;
  return {
    id,
    topic: `topic-${id}`,
    title: `Path ${id}`,
    level: "new_to_it",
    status: "ready",
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

function bareRecord(overrides: Partial<FlowRecord> = {}): FlowRecord {
  return {
    version: 1,
    startedAt: "2026-01-01T00:00:00.000Z",
    length: 5,
    order: "interleave",
    paths: ["a"],
    cursor: 0,
    current: { lessonId: "l1", pathId: "a" },
    next: null,
    completed: [],
    endedReason: null,
    ...overrides,
  };
}

describe("readFlow / writeFlow / clearFlow", () => {
  it("round-trips a written record", () => {
    const record = bareRecord();
    writeFlow(record);
    expect(readFlow()).toEqual(record);
  });

  it("reads null when nothing is stored", () => {
    expect(readFlow()).toBeNull();
  });

  it("reads null after clearFlow, and the key is actually gone", () => {
    writeFlow(bareRecord());
    clearFlow();
    expect(readFlow()).toBeNull();
    expect(window.sessionStorage.getItem(FLOW_STORAGE_KEY)).toBeNull();
  });

  it("rejects and removes a record with the wrong version", () => {
    window.sessionStorage.setItem(
      FLOW_STORAGE_KEY,
      JSON.stringify({ ...bareRecord(), version: 2 }),
    );
    expect(readFlow()).toBeNull();
    expect(window.sessionStorage.getItem(FLOW_STORAGE_KEY)).toBeNull();
  });

  it("rejects and removes a malformed record (wrong shape, not just wrong version)", () => {
    window.sessionStorage.setItem(FLOW_STORAGE_KEY, JSON.stringify({ version: 1, paths: "nope" }));
    expect(readFlow()).toBeNull();
    expect(window.sessionStorage.getItem(FLOW_STORAGE_KEY)).toBeNull();
  });

  it("rejects and removes unparseable JSON", () => {
    window.sessionStorage.setItem(FLOW_STORAGE_KEY, "{not json");
    expect(readFlow()).toBeNull();
    expect(window.sessionStorage.getItem(FLOW_STORAGE_KEY)).toBeNull();
  });

  it("survives a throwing storage — reads as no flow rather than throwing", () => {
    const getItem = vi.spyOn(window.sessionStorage.__proto__, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    try {
      expect(readFlow()).toBeNull();
    } finally {
      getItem.mockRestore();
    }
  });

  it("writeFlow survives a throwing storage without raising", () => {
    const setItem = vi.spyOn(window.sessionStorage.__proto__, "setItem").mockImplementation(() => {
      throw new Error("storage full");
    });
    try {
      expect(() => writeFlow(bareRecord())).not.toThrow();
    } finally {
      setItem.mockRestore();
    }
  });
});

describe("useFlow", () => {
  it("reads the live record and re-renders after writeFlow/clearFlow", () => {
    const { result } = renderHook(() => useFlow());
    expect(result.current).toBeNull();

    act(() => writeFlow(bareRecord()));
    expect(result.current).toEqual(bareRecord());

    act(() => clearFlow());
    expect(result.current).toBeNull();
  });
});

describe("startFlow", () => {
  it("draws the first lesson, pre-draws the look-ahead, and writes the record", () => {
    const summaries = [summary("a"), summary("b")];
    const firstId = startFlow({ length: 5, order: "interleave", paths: ["a", "b"] }, summaries);

    expect(firstId).toBe("a-next");
    const record = readFlow();
    expect(record?.current).toEqual({ lessonId: "a-next", pathId: "a" });
    expect(record?.next).toEqual({ lessonId: "b-next", pathId: "b" });
    expect(record?.completed).toEqual([]);
    expect(record?.endedReason).toBeNull();
  });

  it("drops the look-ahead when a single-path scope's second draw lands on the same path", () => {
    // With only one path scoped, the look-ahead's own draw always lands back
    // on that same path — not a different one, so there is nothing to look
    // ahead to (flow-fix-plan item 2: D8's reason for a look-ahead is a
    // *different* path; same-path is covered by backend Prefetch (+N), not
    // this GET). `next` is dropped to `null` rather than stored as the lesson
    // already on screen; `advanceRecord` falls through to a fresh `pickNext`
    // at the next advance regardless (flow-order.test.ts's own coverage).
    const firstId = startFlow({ length: 3, order: "interleave", paths: ["a"] }, [summary("a")]);
    expect(firstId).toBe("a-next");
    expect(readFlow()?.next).toBeNull();
  });

  it("drops the look-ahead for a serial two-path scope too, while the first path still has lessons left", () => {
    // Serial sticks with the first eligible path in scope order until it runs
    // dry — its own look-ahead draw lands on that same path as `current` for
    // as long as "a" still has a `next_lesson`, exactly like the single-path
    // case above.
    const firstId = startFlow({ length: 5, order: "serial", paths: ["a", "b"] }, [
      summary("a"),
      summary("b"),
    ]);
    expect(firstId).toBe("a-next");
    expect(readFlow()?.next).toBeNull();
  });

  it("keeps an interleave look-ahead that lands on a different path", () => {
    // Interleave's second draw moves scope-index on from the first, so with
    // two eligible paths it lands on "b" — a genuine look-ahead, unlike the
    // same-path cases above.
    const firstId = startFlow({ length: 5, order: "interleave", paths: ["a", "b"] }, [
      summary("a"),
      summary("b"),
    ]);
    expect(firstId).toBe("a-next");
    expect(readFlow()?.next).toEqual({ lessonId: "b-next", pathId: "b" });
  });

  it("throws NoEligiblePathError and writes nothing when the scope is dry", () => {
    const summaries = [summary("a", { nextLessonId: null })];
    expect(() => startFlow({ length: 5, order: "interleave", paths: ["a"] }, summaries)).toThrow(
      NoEligiblePathError,
    );
    expect(readFlow()).toBeNull();
  });

  it("persists the look-ahead draw's own cursor, not just its lesson (interleave)", () => {
    // Regression pin: the second (look-ahead) draw must advance the cursor
    // too, or the *third* pick (the one after the look-ahead is consumed)
    // would re-run from a stale cursor and repeat a path interleave already
    // passed. Scope order [a, b]: draw 1 -> a (cursor 0 -> 1), draw 2 -> b
    // (cursor 1 -> 0) — the record must land on cursor 0, not 1.
    startFlow({ length: 5, order: "interleave", paths: ["a", "b"] }, [summary("a"), summary("b")]);
    expect(readFlow()?.cursor).toBe(0);
  });

  it("records the exact length and order the learner picked", () => {
    startFlow({ length: null, order: "random", paths: ["a"] }, [summary("a")]);
    const record = readFlow();
    expect(record?.length).toBeNull();
    expect(record?.order).toBe("random");
  });
});
