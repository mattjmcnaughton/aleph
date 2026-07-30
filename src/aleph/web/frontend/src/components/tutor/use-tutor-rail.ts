// The rail's state machine (AL-230, TDD §8, PRD §5.6–§5.8) — everything the
// tutor surface knows, in one hook the lesson route calls once.
//
// It lives here, above both presentations, for the D12 reason: the docked
// column and the bottom sheet are **one tree**, so open/closed is plain shared
// JS state and never a function of viewport width. Nothing in this file (or any
// file under `components/tutor/`) reads `matchMedia` or a breakpoint.
//
// Three invariants worth stating together, because they only make sense together:
//
//  1. **The client owns the question.** `pending` is the rail's own copy of what
//     the learner asked. Stop and failure both restore it to the composer; retry
//     re-sends it. Nothing here ever reads the question back off the server — a
//     failed or stopped turn persists nothing (TDD D2), so the server does not
//     have it.
//  2. **A settled turn is appended, a failed one changes nothing.** Success
//     writes both messages into the cached thread (the `done` ids, the
//     accumulated deltas, and any `tutor_check` payload, which is how AL-231's
//     card finds it). Failure invalidates nothing: there is no server state the
//     client could be out of step with.
//  3. **Partial text is not a reply.** Deltas accumulate in a ref for the live
//     bubble, and are dropped on stop or failure. A turn exists whole or not at
//     all, on the client as much as in the database.
//
// And one rule about *ending* a stream, which the three invariants above all
// depend on: `endStream` is the single owned entry point. Stop, new conversation
// and unmount are its only callers, and nothing else in this file may touch
// `abortRef` or move `status` out of `streaming`. A second way to end a stream is
// how a cleared thread gets a resurrected turn appended back onto it.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import { sessionQueryOptions } from "../../lib/auth";
import { useFeatureFlag } from "../../lib/feature-flags";
import {
  type Conversation,
  type ConversationMessage,
  clearConversation,
  conversationQueryKey,
  conversationQueryOptions,
} from "../../lib/tutor";
import {
  type TutorCheck,
  type TutorMessageSource,
  TutorStreamError,
  streamTutorReply,
} from "../../lib/tutor-stream";

/** The flag that gates the whole surface until AL-270 flips it (epic #82). */
const TUTOR_FLAG = "tutor";

/**
 * The one-tap asks, client-side and constant (PRD §5.3). They are a starting
 * vocabulary, not a menu: each sends its own label as ordinary content, tagged
 * `source: "suggestion"` so the §7 entry-mix metric can tell them apart.
 */
export const TUTOR_SUGGESTIONS: readonly string[] = [
  "Explain this simpler",
  "Go deeper",
  "Quiz me on this",
  "Show me a real example",
] as const;

/** `TutorMessageStr` (docs/api.md) — the server bound, enforced here too. */
export const TUTOR_MESSAGE_MAX_LENGTH = 2000;

/** Transport-level failure copy. The only case where blaming the connection is honest. */
const NETWORK_FAILURE_COPY = "The tutor didn't answer. Check your connection and send it again.";

/** New conversation failed. Nothing was cleared, so the offer is to try clearing again. */
const CLEAR_FAILURE_COPY =
  "That didn't go through — this conversation is still here. Try clearing it again.";

export type TutorRailStatus = "idle" | "streaming" | "failed";

export interface TutorRailState {
  /** The rail is mounted (docked column at `lg`, sheet below it). */
  open: boolean;
  /** The floating mark is the way back in — shown exactly when closed. */
  showMark: boolean;
  openRail: () => void;
  closeRail: () => void;

  lessonTitle: string;
  messages: ConversationMessage[];
  status: TutorRailStatus;
  /** The live reply, mid-stream. Empty once the turn settles, stops, or fails. */
  streamingText: string;
  /** Learner-facing copy for the last failed reply, or null. */
  errorMessage: string | null;

  draft: string;
  setDraft: (value: string) => void;
  send: (content: string, source: TutorMessageSource) => void;
  stop: () => void;
  retry: () => void;

  /** New conversation (PRD §5.8) — destructive, so it confirms first. */
  confirmingNew: boolean;
  askNewConversation: () => void;
  cancelNewConversation: () => void;
  confirmNewConversation: () => void;
  /** Copy for a `DELETE` that failed, or null. Distinct from `errorMessage`. */
  clearError: string | null;

  /** Admin per-message model override; "" = the server's configured slot. */
  model: string;
  setModel: (value: string) => void;
  /** `session.user.is_admin` — the picker's whole visibility rule. */
  isAdmin: boolean;
  /** `session.user.model_allowlist` — the picker's only source of options. */
  modelAllowlist: readonly string[];
}

export interface UseTutorRailOptions {
  /** The lesson's parent path — one conversation per path, not per lesson. */
  pathId: string | null;
  lessonId: string;
  lessonTitle: string;
  /**
   * Whether this lesson has a Read passage to ground on. Lesson scope is empty
   * without one, so the entry point is absent (not disabled) — the server says
   * the same thing with a `409` (TDD §8).
   */
  lessonReady: boolean;
}

interface PendingQuestion {
  content: string;
  source: TutorMessageSource;
}

/**
 * What an ended stream should leave behind in the composer. `restore` is stop —
 * the learner wants to edit the question, so it goes back where they left it.
 * `discard` is new conversation and unmount: they asked for the thread to go
 * away, and putting the question they abandoned back into the composer of an
 * emptied rail would be the opposite of what they said.
 */
type EndStreamMode = "restore" | "discard";

export function useTutorRail({
  pathId,
  lessonId,
  lessonTitle,
  lessonReady,
}: UseTutorRailOptions): TutorRailState {
  const queryClient = useQueryClient();
  const flagOn = useFeatureFlag(TUTOR_FLAG);
  // The same cached session the flag came from — one read, two answers (the
  // house rule in `lib/auth.ts`: nobody respells the session query).
  const session = useQuery(sessionQueryOptions);
  const enabled = flagOn && lessonReady && pathId !== null;

  const [openState, setOpenState] = useState(false);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<TutorRailStatus>("idle");
  const [streamingText, setStreamingText] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [confirmingNew, setConfirmingNew] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  const [model, setModel] = useState("");

  // Refs, not state, for everything the stream callbacks touch: deltas land
  // faster than React re-renders, and the settle path needs the *final* text and
  // check payload, not whatever a closure captured when the send started.
  const pendingRef = useRef<PendingQuestion | null>(null);
  const textRef = useRef("");
  const checkRef = useRef<TutorCheck | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const endModeRef = useRef<EndStreamMode>("restore");

  // `skipToken` when the entry point is not rendered: a gated-off surface costs
  // no request at all, which is what makes shipping dark actually dark.
  const conversationQuery = useQuery(conversationQueryOptions(enabled ? pathId : null));
  const messages = conversationQuery.data?.messages ?? [];

  const appendTurn = useCallback(
    async (
      ids: { learner_message_id: string; tutor_message_id: string },
      question: PendingQuestion,
      reply: string,
      check: TutorCheck | null,
    ) => {
      if (pathId === null) return;
      const key = conversationQueryKey(pathId);
      // Cancel first for the same reason the lesson view does before folding in
      // an Attempt: a GET that started before this turn would resolve without it
      // and briefly un-append the reply the learner just watched arrive.
      await queryClient.cancelQueries({ queryKey: key });
      const now = new Date().toISOString();
      const shared = { lesson_id: lessonId, lesson_title: lessonTitle, created_at: now };
      queryClient.setQueryData<Conversation>(key, (old) => ({
        messages: [
          ...(old?.messages ?? []),
          {
            ...shared,
            id: ids.learner_message_id,
            role: "learner",
            content: question.content,
            tutor_check: null,
          },
          {
            ...shared,
            id: ids.tutor_message_id,
            role: "tutor",
            content: reply,
            // The whole reason the stream's `tutor_check` event is captured:
            // the card (AL-231) reads it off the cached message, not off any
            // transient streaming state.
            tutor_check: check,
          },
        ],
      }));
    },
    [pathId, lessonId, lessonTitle, queryClient],
  );

  /**
   * **The one way an in-flight stream ends from the outside.** Stop, new
   * conversation and unmount all come through here, and nothing else in this
   * hook touches `abortRef`. That is the whole fix for a cleared thread getting
   * the old turn appended back onto it: with one entry point, "the stream is
   * over" and "the stream's settle path is cancelled" cannot come apart.
   *
   * Idempotent, and a no-op once the stream has stopped being interruptible —
   * `run` releases `abortRef` the moment the `done` frame lands, so a stop that
   * arrives while the turn is being written to the cache is correctly ignored
   * rather than aborting a fetch that already succeeded.
   */
  const endStream = useCallback((mode: EndStreamMode) => {
    const controller = abortRef.current;
    if (controller === null) return;
    endModeRef.current = mode;
    abortRef.current = null;
    controller.abort();
  }, []);

  const run = useCallback(
    async (question: PendingQuestion) => {
      if (pathId === null) return;
      const controller = new AbortController();
      abortRef.current = controller;
      endModeRef.current = "restore";
      pendingRef.current = question;
      textRef.current = "";
      checkRef.current = null;
      // The composer is emptied here rather than in `send`, so every path into a
      // stream clears it and every path out of one restores it: send and retry
      // clear, stop and failure put the question back (below).
      setDraft("");
      setStreamingText("");
      setErrorMessage(null);
      setStatus("streaming");

      try {
        const done = await streamTutorReply({
          pathId,
          input: {
            lesson_id: lessonId,
            content: question.content,
            source: question.source,
            // Absent, not null: sending the key at all is `403` for a non-admin.
            ...(model ? { model } : {}),
          },
          signal: controller.signal,
          onDelta: (text) => {
            textRef.current += text;
            setStreamingText(textRef.current);
          },
          onTutorCheck: (check) => {
            checkRef.current = check;
          },
        });
        // Released *before* the cache write, not in a `finally` after it: from
        // the `done` frame on there is nothing left to abort, and leaving the
        // controller reachable would let a stop landing in that window reject a
        // promise the read loop is no longer holding.
        abortRef.current = null;
        await appendTurn(done, question, textRef.current, checkRef.current);
        pendingRef.current = null;
        setStreamingText("");
        setStatus("idle");
      } catch (error) {
        // Whatever happened, the partial reply is gone (invariant 3).
        abortRef.current = null;
        setStreamingText("");
        textRef.current = "";
        if (isAbort(error)) {
          // Stop restores the question to the composer for editing; a clear or
          // an unmount discards it. Not an error state either way, and nothing
          // was persisted.
          if (endModeRef.current === "restore") setDraft(question.content);
          pendingRef.current = null;
          setStatus("idle");
          return;
        }
        // Failure keeps the question twice over: in `pendingRef`, which is what
        // "Try again" re-sends, and in the composer, which is what the card's
        // "Your question is still here" claims. Symmetric with stop — the two
        // read the same to a learner, so they should leave the same thing behind.
        setDraft(question.content);
        setErrorMessage(failureCopy(error));
        setStatus("failed");
      }
    },
    [pathId, lessonId, model, appendTurn],
  );

  const send = useCallback(
    (content: string, source: TutorMessageSource) => {
      const trimmed = content.trim();
      if (trimmed === "" || status === "streaming") return;
      void run({ content: trimmed, source });
    },
    [run, status],
  );

  // Unmount discards the turn, matching what the server does with it (D2): the
  // learner navigated away, the socket drops, and a reply nobody is watching is
  // persisted by neither side. Without this the stream would run to completion
  // and write a turn into the cache of a route that is gone.
  useEffect(() => () => endStream("discard"), [endStream]);

  const clearMutation = useMutation({
    // The id is the mutation's variable, never `pathId ?? ""` — that spelling
    // can issue `DELETE /paths//conversation`, which is a different route.
    mutationFn: (id: string) => clearConversation(id),
    // Only the cache. Every piece of local state this clear touches is reset
    // synchronously in `confirmNewConversation`, before the request goes out —
    // a late callback writing `status` is how a *later* stream would get forced
    // out of `streaming` by a `DELETE` that has nothing to do with it.
    onSuccess: async (_result, id) => {
      const key = conversationQueryKey(id);
      await queryClient.cancelQueries({ queryKey: key });
      // The thread is gone server-side and the next turn creates a fresh row, so
      // the empty list is certain — write it rather than refetch it.
      queryClient.setQueryData<Conversation>(key, { messages: [] });
    },
    onError: () => setClearError(CLEAR_FAILURE_COPY),
  });

  return {
    open: enabled && openState,
    showMark: enabled && !openState,
    openRail: () => setOpenState(true),
    closeRail: () => setOpenState(false),

    lessonTitle,
    messages,
    status,
    streamingText,
    errorMessage,

    draft,
    setDraft,
    send,
    stop: () => endStream("restore"),
    retry: () => {
      const question = pendingRef.current;
      if (question) void run(question);
    },

    confirmingNew,
    askNewConversation: () => setConfirmingNew(true),
    cancelNewConversation: () => setConfirmingNew(false),
    confirmNewConversation: () => {
      setConfirmingNew(false);
      // Never `pathId ?? ""`: that spelling issues `DELETE /paths//conversation`.
      if (pathId === null) return;
      // Stop first, always. The `DELETE` empties the thread, and a stream still
      // running would append its turn onto the emptied thread when it settled —
      // so the stream is ended, and the rail put back to rest, before the
      // request is issued rather than alongside it.
      endStream("discard");
      setStatus("idle");
      setErrorMessage(null);
      setClearError(null);
      pendingRef.current = null;
      clearMutation.mutate(pathId);
    },
    clearError,

    model,
    setModel,
    isAdmin: session.data?.user?.is_admin ?? false,
    modelAllowlist: session.data?.user?.model_allowlist ?? [],
  };
}

/**
 * Stop, not failure. Matched on `name` rather than `instanceof DOMException`:
 * the rejection crosses realms (the fetch's abort reason is minted outside the
 * document's global), and an `instanceof` that is true in a browser can be false
 * under jsdom — which would turn every stop into a spurious error state.
 */
function isAbort(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: string }).name === "AbortError"
  );
}

/**
 * Learner-facing copy for a failed reply. The server already words its own
 * failures for a learner (never provider text) and carries a `code`, so its
 * message is used verbatim — that is what lets an upstream budget failure avoid
 * "check your connection", the wording gap PRD §5.7 names. Only a genuine
 * transport failure gets the connection copy.
 */
function failureCopy(error: unknown): string {
  if (error instanceof TutorStreamError || error instanceof ApiError) {
    return error.message;
  }
  return NETWORK_FAILURE_COPY;
}
