import { describe, expect, it } from "vitest";
import { BACK_WORDS_MAX, FRONT_WORDS_MAX, canSaveCardEdit, countWords } from "./flashcard-caps";

// The frontend mirror of `agents/flashcard.py`'s `is_non_empty` /
// `within_word_cap` / `sides_differ` (AL-410 plan §4/§6) — the gate
// `card-row.tsx` disables Save behind, so an edit that would 422 server-side
// never leaves the browser in the first place.

describe("countWords", () => {
  it("matches Python's str.split(): collapses runs of whitespace, ignores padding", () => {
    expect(countWords("a  b   c")).toBe(3);
    expect(countWords("  padded  ")).toBe(1);
    expect(countWords("one\ttwo\nthree")).toBe(3);
  });

  it("is 0 for empty or whitespace-only text", () => {
    expect(countWords("")).toBe(0);
    expect(countWords("   ")).toBe(0);
  });
});

describe("canSaveCardEdit", () => {
  const front = "What does `extends` mean?";
  const back = "It constrains T — T must be assignable to X.";

  it("passes a well-formed, distinct pair", () => {
    expect(canSaveCardEdit(front, back)).toBe(true);
  });

  it("rejects an empty or whitespace-only side", () => {
    expect(canSaveCardEdit("", back)).toBe(false);
    expect(canSaveCardEdit(front, "   ")).toBe(false);
  });

  it(`rejects a front over ${FRONT_WORDS_MAX} words`, () => {
    const tooLong = Array.from({ length: FRONT_WORDS_MAX + 1 }, () => "word").join(" ");
    expect(canSaveCardEdit(tooLong, back)).toBe(false);
    // Exactly at the cap is still fine — the cap is inclusive.
    const atCap = Array.from({ length: FRONT_WORDS_MAX }, () => "word").join(" ");
    expect(canSaveCardEdit(atCap, back)).toBe(true);
  });

  it(`rejects a back over ${BACK_WORDS_MAX} words`, () => {
    const tooLong = Array.from({ length: BACK_WORDS_MAX + 1 }, () => "word").join(" ");
    expect(canSaveCardEdit(front, tooLong)).toBe(false);
  });

  it("rejects identical sides, case- and whitespace-insensitively", () => {
    expect(canSaveCardEdit("Same text", "Same text")).toBe(false);
    expect(canSaveCardEdit("Same Text", "  same text  ")).toBe(false);
  });
});
