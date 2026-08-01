import { describe, expect, it } from "vitest";
import type { PathStatus } from "./api";
import {
  LEVELS,
  MODEL_SLOT_DEFAULT,
  buildCreatePathInput,
  canSubmitTopic,
  deriveOnboardingPhase,
  levelLabel,
} from "./onboarding";

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

describe("levelLabel", () => {
  it("[AL-064] renders each level with its CONTEXT display label", () => {
    expect(levelLabel("new_to_it")).toBe("New to it");
    expect(levelLabel("some_experience")).toBe("Some experience");
    expect(levelLabel("work_in_it")).toBe("I work in it");
  });
});

describe("buildCreatePathInput", () => {
  const base = { topic: "  Rust ownership  ", level: "some_experience" } as const;

  it("[AL-065] trims the topic and omits both model slots when unset", () => {
    const input = buildCreatePathInput({
      ...base,
      modelOutline: MODEL_SLOT_DEFAULT,
      modelLesson: MODEL_SLOT_DEFAULT,
    });

    // Absent keys, never `null`/`undefined` values: a non-admin sending an
    // override at all is `403 forbidden` (docs/api.md), so the payload must not
    // carry the key when no model was chosen.
    expect(input).toEqual({ topic: "Rust ownership", level: "some_experience" });
    expect("model_outline" in input).toBe(false);
    expect("model_lesson" in input).toBe(false);
  });

  it("[AL-065] carries the chosen models under the documented field names", () => {
    const input = buildCreatePathInput({
      ...base,
      modelOutline: "anthropic/claude-opus-4-8",
      modelLesson: "anthropic/claude-haiku-4-5",
    });

    expect(input).toEqual({
      topic: "Rust ownership",
      level: "some_experience",
      model_outline: "anthropic/claude-opus-4-8",
      model_lesson: "anthropic/claude-haiku-4-5",
    });
  });

  it("[AL-065] omits only the slot left at the default", () => {
    const outlineOnly = buildCreatePathInput({
      ...base,
      modelOutline: "openai/gpt-5.6-terra",
      modelLesson: MODEL_SLOT_DEFAULT,
    });
    expect("model_lesson" in outlineOnly).toBe(false);
    expect(outlineOnly.model_outline).toBe("openai/gpt-5.6-terra");

    const lessonOnly = buildCreatePathInput({
      ...base,
      modelOutline: MODEL_SLOT_DEFAULT,
      modelLesson: "minimax/minimax-m3",
    });
    expect("model_outline" in lessonOnly).toBe(false);
    expect(lessonOnly.model_lesson).toBe("minimax/minimax-m3");
  });

  it("[AL-065] omits both slots when the picker never rendered at all", () => {
    // The non-admin path: no slot arguments reach the builder, and the payload
    // must look exactly like an admin's untouched picker.
    const input = buildCreatePathInput(base);

    expect(Object.keys(input).sort()).toEqual(["level", "topic"]);
  });

  it("includes trimmed guidance when the textarea carries text", () => {
    const input = buildCreatePathInput({
      ...base,
      guidance: "  Cover generics before decorators  ",
    });

    expect(input.guidance).toBe("Cover generics before decorators");
  });

  it("omits guidance entirely when blank or whitespace-only", () => {
    expect("guidance" in buildCreatePathInput(base)).toBe(false);
    expect("guidance" in buildCreatePathInput({ ...base, guidance: "" })).toBe(false);
    expect("guidance" in buildCreatePathInput({ ...base, guidance: "   " })).toBe(false);
  });
});
