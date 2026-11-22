// Pure helpers for the New path draft flow (CONTEXT.md: *Path draft*,
// *Interview*). Display only: which parts of a sharpened draft to mark, and how
// one Interview answer is spelled when it is sent back for a redraft.

import type { PathDraft } from "./api";

/** Mirrors `AnswerStr` in `dtos/path_drafts.py` (`max_length=500`). */
export const ANSWER_MAX_LENGTH = 500;
/** Mirrors `MAX_ANSWERS`: the server takes at most this many answers per call. */
export const MAX_ANSWERS = 20;

/** A draft's guidance as its non-blank, trimmed lines. */
export function guidanceLines(guidance: string): string[] {
  return guidance
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

export interface DraftChanges {
  /** True when the Topic differs from the previous draft's. */
  topicChanged: boolean;
  /** Indexes (into `guidanceLines(next.guidance)`) of lines `previous` lacked. */
  changedLines: Set<number>;
}

/**
 * What a redraft changed: lines are compared as a set, so a line the redraft
 * only moved is not marked. A first draft (`previous` null) marks nothing.
 */
export function draftChanges(previous: PathDraft | null, next: PathDraft): DraftChanges {
  if (previous === null) return { topicChanged: false, changedLines: new Set() };
  const before = new Set(guidanceLines(previous.guidance));
  const changedLines = new Set<number>();
  guidanceLines(next.guidance).forEach((line, index) => {
    if (!before.has(line)) changedLines.add(index);
  });
  return { topicChanged: previous.topic.trim() !== next.topic.trim(), changedLines };
}

/**
 * One Interview answer as text: the picked options in the order offered, then
 * the learner's own words. `null` when they neither picked nor typed anything,
 * so a skipped question is not sent at all.
 */
export function answerText(options: string[], picked: number[], ownWords: string): string | null {
  const parts = options.filter((_, index) => picked.includes(index));
  const typed = ownWords.trim();
  if (typed) parts.push(typed);
  return parts.length > 0 ? parts.join("; ").slice(0, ANSWER_MAX_LENGTH) : null;
}
