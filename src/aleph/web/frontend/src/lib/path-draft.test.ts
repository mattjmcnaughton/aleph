import { describe, expect, it } from "vitest";
import type { PathDraft } from "./api";
import { answerText, draftChanges, guidanceLines } from "./path-draft";

const FIRST: PathDraft = {
  topic: "Postgres internals",
  level: "some_experience",
  guidance: "Start with the planner.\nThen storage.\nSkip basic SQL.",
  teaching_style: "",
};

describe("guidanceLines", () => {
  it("splits on newlines and drops blank lines", () => {
    expect(guidanceLines("  One.\n\n Two. \n")).toEqual(["One.", "Two."]);
    expect(guidanceLines("")).toEqual([]);
  });
});

describe("draftChanges", () => {
  it("marks nothing on a first draft", () => {
    expect(draftChanges(null, FIRST)).toEqual({ topicChanged: false, changedLines: new Set() });
  });

  it("marks guidance lines the previous draft did not have, wherever they moved", () => {
    const next: PathDraft = {
      ...FIRST,
      guidance: "Read EXPLAIN first.\nStart with the planner.\n Then storage. \nKeep it short.",
    };
    expect(draftChanges(FIRST, next)).toEqual({
      topicChanged: false,
      changedLines: new Set([0, 3]),
    });
  });

  it("flags a changed topic, ignoring surrounding whitespace", () => {
    expect(draftChanges(FIRST, { ...FIRST, topic: " Postgres internals " }).topicChanged).toBe(
      false,
    );
    expect(draftChanges(FIRST, { ...FIRST, topic: "Postgres for production" }).topicChanged).toBe(
      true,
    );
  });
});

describe("answerText", () => {
  const options = ["Replication", "Extensions", "Partitioning"];

  it("joins picked options in option order, then the learner's own words", () => {
    expect(answerText(options, [2, 0], "  and sharding ")).toBe(
      "Replication; Partitioning; and sharding",
    );
  });

  it("is null when nothing was picked or typed", () => {
    expect(answerText(options, [], "   ")).toBeNull();
  });

  it("caps the answer at the server's 500 characters", () => {
    expect(answerText(options, [], "x".repeat(600))?.length).toBe(500);
  });
});
