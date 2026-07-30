// The tutor's non-streaming wire seam (AL-230, docs/api.md ## Tutor): the
// conversation read + clear routes, their types, and the one query identity the
// rail reads. Everything here goes through `apiFetch`/`apiV1Path` exactly like
// `lib/api.ts`'s Phase 1 calls — only the *streamed* send lives apart, in
// `lib/tutor-stream.ts`, and only because it must read the body progressively.
//
// **The thread is ordinary query state, not a poll target** (TDD §8). Phase 1's
// generation surfaces poll because work outlives the request; a tutor reply is
// request-scoped, arrives on its own stream, and is appended to this cache when
// it settles. Nothing here carries a `refetchInterval`, and a *failed* reply
// invalidates nothing — a turn exists whole or not at all (D2), so there is
// never server state the client is out of step with.

import { queryOptions, skipToken } from "@tanstack/react-query";
import { apiFetch, apiV1Path } from "./api";
import type { TutorCheck } from "./tutor-stream";

/** Who spoke (docs/api.md). Learner and tutor messages pair into a **turn**. */
export type TutorRole = "learner" | "tutor";

/**
 * One message in the thread. There is **one conversation per path**, so
 * `lesson_id`/`lesson_title` — the lesson the message was asked in — vary down a
 * single thread (PRD §5.8; Phase 2B renders them as dividers).
 *
 * Note the payload carries no `position`: the array order **is** the order.
 */
export interface ConversationMessage {
  id: string;
  role: TutorRole;
  content: string;
  lesson_id: string;
  lesson_title: string;
  /** Set only on a tutor message that posed a Tutor check (AL-231 renders it). */
  tutor_check: TutorCheck | null;
  created_at: string;
}

/** `GET /api/v1/paths/{id}/conversation` — object-wrapped, oldest first. */
export interface Conversation {
  messages: ConversationMessage[];
}

/** The whole thread for a path. `200 {messages: []}` when none exists yet. */
export function getConversation(pathId: string): Promise<Conversation> {
  return apiFetch<Conversation>(apiV1Path(`/paths/${pathId}/conversation`));
}

/**
 * **New conversation** (PRD §5.8): drop the thread for this path. `204` and
 * idempotent; cascades to its messages; touches no Phase 1 state. Destructive
 * and not undoable — the caller MUST confirm first, like `deletePath`.
 */
export function clearConversation(pathId: string): Promise<void> {
  return apiFetch<void>(apiV1Path(`/paths/${pathId}/conversation`), { method: "DELETE" });
}

/**
 * Record the learner's Tutor-check choice (AL-231, route from AL-221) → `204`.
 *
 * Addressed by **message** id: a check rides the tutor message that posed it.
 * `selected_index` indexes that check's `options` — out of range is a `422`,
 * because nothing grades a Tutor check and the index is only ever used to index
 * `options` when re-rendering the revealed card.
 *
 * **This call grades nothing.** The delivered payload already carries
 * `correct_index` + `explanation` (TDD §6), so the card reveals from what it is
 * already holding and calls this *afterwards*, only so a revisited thread
 * renders revealed. Re-answering overwrites (last-wins) — deliberately unlike
 * the Quick check's first-wins Attempt, because an Attempt is graded and feeds
 * the §7 metrics, and neither is true here.
 */
export function answerTutorCheck(messageId: string, selectedIndex: number): Promise<void> {
  return apiFetch<void>(apiV1Path(`/messages/${messageId}/tutor-check-answer`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_index: selectedIndex }),
  });
}

/**
 * TanStack query key for one path's thread. The `"tutor"` head is its own
 * namespace rather than a branch of `["paths", …]`: lesson completion
 * invalidates that whole prefix (`PATHS_QUERY_PREFIX`), and a conversation has
 * no business being refetched because a lesson was marked complete.
 */
export function conversationQueryKey(pathId: string): readonly ["tutor", "conversation", string] {
  return ["tutor", "conversation", pathId] as const;
}

/**
 * THE conversation query — key + fetcher paired in one place (the house rule
 * from `sessionQueryOptions`). Pass `null` when the rail's entry point is not
 * rendered (flag off, or a lesson with no generated content): the query idles on
 * `skipToken` and the gated surface costs no request at all.
 */
export function conversationQueryOptions(pathId: string | null) {
  return queryOptions({
    queryKey: conversationQueryKey(pathId ?? "idle"),
    queryFn: pathId === null ? skipToken : () => getConversation(pathId),
  });
}
