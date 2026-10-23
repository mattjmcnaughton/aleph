import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { learningError, learningStateOptions, recommendationState } from "./learning-tutor";
import type { LearningTutorState } from "./learning-tutor-types";

const empty: LearningTutorState = {
  focus: null,
  revision: 0,
  focus_version: 0,
  epoch: 0,
  recommendations: [],
  latest_refresh: null,
  current_operation_id: null,
  history: { items: [], before: null },
};

describe("Learning Tutor state boundaries", () => {
  it("keys state by account and revalidates on window focus", () => {
    expect(learningStateOptions("a").queryKey).not.toEqual(learningStateOptions("b").queryKey);
    expect(learningStateOptions("a").refetchOnWindowFocus).toBe("always");
  });
  it("does not call initial, dismissed and missing prior cards successful empty", () => {
    expect(recommendationState(empty, 0)).toBe("initial");
    expect(recommendationState(empty, 1)).toBe("dismissed");
    const refresh = {
      id: "r",
      epoch: 0,
      kind: "refresh" as const,
      status: "succeeded" as const,
      error_code: null,
      input: null,
      output: {
        outcome: "completed" as const,
        omitted: { turns: 0, paths: 0, beats: 0, briefs: 0, recommendations: 0 },
        recommendation_ids: ["old"],
      },
      started_at: "2026-10-23",
      finished_at: "2026-10-23",
      cleared: false,
    };
    expect(recommendationState({ ...empty, latest_refresh: refresh }, 0)).toBe("unavailable");
    expect(
      recommendationState(
        {
          ...empty,
          latest_refresh: { ...refresh, output: { ...refresh.output, recommendation_ids: [] } },
        },
        0,
      ),
    ).toBe("empty");
    expect(
      recommendationState(
        {
          ...empty,
          latest_refresh: { ...refresh, output: { ...refresh.output, outcome: "needs_context" } },
        },
        0,
      ),
    ).toBe("needs_context");
  });
  it("explains stale state, limits, and deleted targets rather than generic empty", () => {
    expect(
      learningError(new ApiError("", 409, "conflict", undefined, { reason: "stale_state" })),
    ).toMatch(/changed/i);
    expect(learningError(new ApiError("", 429, "rate_limited"))).toMatch(/limit/i);
    expect(learningError(new ApiError("", 410, "gone"))).toMatch(/deleted/i);
  });
});
