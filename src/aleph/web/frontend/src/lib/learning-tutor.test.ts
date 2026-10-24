import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { learningError, learningStateOptions } from "./learning-tutor";

describe("Learning Tutor state boundaries", () => {
  it("keys state by account and revalidates on window focus", () => {
    expect(learningStateOptions("a").queryKey).not.toEqual(learningStateOptions("b").queryKey);
    expect(learningStateOptions("a").refetchOnWindowFocus).toBe("always");
  });
  it("explains stale state, limits, and deleted targets rather than generic empty", () => {
    expect(
      learningError(new ApiError("", 409, "conflict", undefined, { reason: "stale_state" })),
    ).toMatch(/changed/i);
    expect(learningError(new ApiError("", 429, "rate_limited"))).toMatch(/limit/i);
    expect(learningError(new ApiError("", 410, "gone"))).toMatch(/deleted/i);
  });
});
