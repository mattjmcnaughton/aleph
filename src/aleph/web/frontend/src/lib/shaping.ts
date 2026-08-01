// The shaping conversation's non-streaming wire seam (AL-330; Phase 2B TDD §6):
// the thread read + clear routes, their types, and the one query identity the
// shaping rail reads. The streamed send lives in `lib/tutor-stream.ts` with its
// 2A sibling, for that module's reason — it must read the body progressively,
// and there is one reader for both threads.
//
// **Why this is a separate module from `lib/tutor.ts`.** The two threads are
// separate by design (PRD §5.8: "the in-lesson rail never shows shaping turns,
// and vice versa"), and separate files are the cheapest way to make that
// structural rather than a rule someone has to remember: nothing here reaches
// into the 2A conversation's key, and nothing there can reach into this one.
//
// The thread is ordinary TanStack Query state, not a poll target (TDD §8) — the
// same posture 2A takes, for the same reason: a reply is request-scoped and
// arrives on its own stream. Applying a proposal is what invalidates the *path*
// outline query (AL-331), and that is a different query entirely.

import { queryOptions, skipToken } from "@tanstack/react-query";
import { apiFetch, apiV1Path } from "./api";
import type { TutorRole } from "./tutor";
import type { AddLessonsOperation, Proposal, ProposalOperation } from "./tutor-stream";

/**
 * A proposal's standing in the thread (TDD §4). **Derived server-side, never
 * stored** — *applied* when a live `path_changes` row references the message,
 * *undone* when that row is undone, *superseded* when a later proposal was
 * applied first and re-validation now fails, else *pending*. The client renders
 * it and never computes it: the same rule that keeps apply's staleness check on
 * the server keeps this off the client.
 */
export type ProposalResolution = "pending" | "applied" | "undone" | "superseded";

/** A proposal as a *read* thread carries it: the payload plus its resolution. */
export interface MessageProposal extends Proposal {
  resolution: ProposalResolution;
}

/**
 * One message in the shaping thread. Note what is absent next to
 * `ConversationMessage`: no `lesson_id`/`lesson_title`. A shaping turn is about
 * the path, so there is no lesson to record — the shape of the DTO is the shape
 * of the scope.
 */
export interface ShapingMessage {
  id: string;
  role: TutorRole;
  content: string;
  /** Set only on a tutor message that produced a Proposal (AL-331 renders it). */
  proposal: MessageProposal | null;
  created_at: string;
}

/** `GET /api/v1/paths/{id}/shaping/conversation` — object-wrapped, oldest first. */
export interface ShapingConversation {
  messages: ShapingMessage[];
}

/**
 * Which of the two operation shapes this is (D1). The wire union is **untagged**
 * — `agents/shaper.py` discriminates it structurally and so does this — so the
 * test is a field that only one shape has, not a `kind` string nobody sends.
 */
export function isAddLessons(operation: ProposalOperation): operation is AddLessonsOperation {
  return "lessons" in operation;
}

/** The whole shaping thread for a path. `200 {messages: []}` when none exists. */
export function getShapingConversation(pathId: string): Promise<ShapingConversation> {
  return apiFetch<ShapingConversation>(apiV1Path(`/paths/${pathId}/shaping/conversation`));
}

/**
 * **New conversation** (PRD §5.8): drop the shaping thread for this path. `204`
 * and idempotent. It does **not** touch the change history — a Change belongs to
 * the path, not to the conversation that proposed it (TDD D3, `SET NULL` on the
 * message FK), which is exactly why clearing a thread is safe to offer. Still
 * destructive and not undoable, so the caller MUST confirm first.
 */
export function clearShapingConversation(pathId: string): Promise<void> {
  return apiFetch<void>(apiV1Path(`/paths/${pathId}/shaping/conversation`), { method: "DELETE" });
}

/**
 * TanStack query key for one path's shaping thread. Its own namespace under
 * `"shaping"`, deliberately *not* a branch of `["tutor", …]` or of
 * `["paths", …]`: clearing one thread must never invalidate the other, and
 * marking a lesson complete (which invalidates the whole `paths` prefix) has no
 * business refetching a conversation.
 */
export function shapingConversationQueryKey(
  pathId: string,
): readonly ["shaping", "conversation", string] {
  return ["shaping", "conversation", pathId] as const;
}

/**
 * THE shaping conversation query — key + fetcher paired in one place (the house
 * rule from `sessionQueryOptions`). Pass `null` when the rail's entry point is
 * not rendered (flag off, or a path that is not `ready`): the query idles on
 * `skipToken`, so the gated surface costs no request at all. That is what makes
 * shipping dark actually dark.
 */
export function shapingConversationQueryOptions(pathId: string | null) {
  return queryOptions({
    queryKey: shapingConversationQueryKey(pathId ?? "idle"),
    queryFn: pathId === null ? skipToken : () => getShapingConversation(pathId),
  });
}
