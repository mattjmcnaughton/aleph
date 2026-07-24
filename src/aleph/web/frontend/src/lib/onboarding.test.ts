import { describe, expect, it } from "vitest";
import type { PathStatus } from "./api";
import { LEVELS, canSubmitTopic, deriveOnboardingPhase } from "./onboarding";

describe("deriveOnboardingPhase", () => {
  it("[AL-061] shows the form before any path exists", () => {
    expect(deriveOnboardingPhase({ pathId: null, status: undefined })).toBe("editing");
    // A stale status is irrelevant while there is no path.
    expect(deriveOnboardingPhase({ pathId: null, status: "failed" })).toBe("editing");
  });

  it("[AL-061] shows generating while the outline is unresolved", () => {
    for (const status of [undefined, "pending", "generating"] as const) {
      expect(deriveOnboardingPhase({ pathId: "p1", status })).toBe("generating");
    }
  });

  it("[AL-061] stays on generating for ready (the route navigates away)", () => {
    expect(deriveOnboardingPhase({ pathId: "p1", status: "ready" })).toBe("generating");
  });

  it("[AL-061] surfaces refused distinctly from failed (W7 vs W8)", () => {
    expect(deriveOnboardingPhase({ pathId: "p1", status: "refused" })).toBe("refused");
    expect(deriveOnboardingPhase({ pathId: "p1", status: "failed" })).toBe("failed");
  });

  it("[AL-061] covers every PathStatus", () => {
    const statuses: PathStatus[] = ["pending", "generating", "ready", "failed", "refused"];
    for (const status of statuses) {
      expect(() => deriveOnboardingPhase({ pathId: "p1", status })).not.toThrow();
    }
  });
});

describe("canSubmitTopic", () => {
  it("[AL-061] requires a non-blank topic", () => {
    expect(canSubmitTopic("")).toBe(false);
    expect(canSubmitTopic("   ")).toBe(false);
    expect(canSubmitTopic("Rust ownership")).toBe(true);
  });
});

describe("LEVELS", () => {
  it("[AL-061] offers the three CONTEXT levels in order", () => {
    expect(LEVELS.map((l) => l.value)).toEqual(["new_to_it", "some_experience", "work_in_it"]);
    expect(LEVELS.map((l) => l.label)).toEqual(["New to it", "Some experience", "I work in it"]);
  });
});
