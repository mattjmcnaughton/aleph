// The shaping rail's state machine (AL-330, Phase 2B TDD §8/D14) — everything
// the shaping surface knows, in one hook the path route calls once.
//
// **The state machine is 2A's `use-tutor-rail.ts`, deliberately re-stated rather
// than shared.** The in-lesson rail must stay bit-identical (W21), and a hook
// generalized over "which conversation am I" would put the two surfaces' state
// machines in one file where a change for one silently reaches the other. What
// that argument does *not* cover is the pure stuff either machine feeds on —
// `isAbort`, `failureCopy` and the failure copy itself have no state in them, so
// they live once in `lib/tutor-stream.ts` and both rails import them. The three
// invariants below are 2A's, and they hold here for 2A's reasons:
//
//  1. **The client owns the question.** `pending` is the rail's own copy of what
//     the learner asked. Stop and failure both restore it to the composer; retry
//     re-sends it. Nothing here reads the question back off the server — a
//     failed or stopped turn persists nothing, so the server does not have it.
//  2. **A settled turn is appended, a failed one changes nothing.** Success
//     writes both messages into the cached thread (the `done` ids, the
//     accumulated deltas, and any `proposal` payload, which is how AL-331's card
//     finds it). Failure invalidates nothing.
//  3. **Partial text is not a reply.** Deltas accumulate in a ref for the live
//     bubble, and are dropped on stop or failure.
//
// And `endStream` is again the single owned entry point for *ending* a stream —
// stop, new conversation, a path switch and unmount are its only callers.
//
// What is genuinely new here is small and worth naming: a streamed **Proposal**
// is captured on the settling turn (invariant 2); the composer is empty until a
// `ready` path and the `shaping` flag both say the surface exists; and this
// surface's route is switched *between paths without remounting*, which the
// `pathId` reset below is the whole answer to.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { sessionQueryOptions } from "../../lib/auth";
import { useFeatureFlag } from "../../lib/feature-flags";
import {
  type ShapingConversation,
  type ShapingMessage,
  clearShapingConversation,
  shapingConversationQueryKey,
  shapingConversationQueryOptions,
} from "../../lib/shaping";
import {
  CLEAR_FAILURE_COPY,
  type Proposal,
  TUTOR_MESSAGE_MAX_LENGTH,
  type TutorMessageSource,
  failureCopy,
  isAbort,
  streamShapingReply,
} from "../../lib/tutor-stream";

/** The flag that gates the whole surface until AL-370 flips it (epic #114). */
const SHAPING_FLAG = "shaping";

/**
 * One of the four one-tap asks (PRD §5.3). Three of them are complete asks and
 * send as typed; **"Add practice on…"** is the start of a sentence, so it
 * prefills the composer instead — sending it verbatim would be an ask with no
 * object, and the ellipsis in the mock is the promise that it won't.
 */
export interface ShapingSuggestion {
  label: string;
  /** When set, tapping fills the composer with this instead of sending. */
  prefill?: string;
}

/**
 * The PRD §5.3 four, client-side and constant. They seed the vocabulary — what
 * shaping *is* — rather than constrain it: free text is always accepted, and
 * each of these sends its own label as ordinary content tagged
 * `source: "suggestion"` so the §7 entry-mix metric can tell them apart.
 */
export const SHAPING_SUGGESTIONS: readonly ShapingSuggestion[] = [
  { label: "Add practice on…", prefill: "Add practice on " },
  { label: "What's missing?" },
  { label: "Make my next lesson simpler" },
  { label: "Make my next lesson deeper" },
] as const;

/**
 * `TutorMessageStr` (docs/api.md), reused verbatim by `SendShapingMessageRequest`
 * — so the bound is the shared one, under this surface's name.
 */
export const SHAPING_MESSAGE_MAX_LENGTH = TUTOR_MESSAGE_MAX_LENGTH;

export type ShapingRailStatus = "idle" | "streaming" | "failed";

export interface ShapingRailState {
  /** The rail is mounted (docked column at `lg`, sheet below it). */
  open: boolean;
  /** The floating mark is the way back in — shown exactly when closed. */
  showMark: boolean;
  openRail: () => void;
  closeRail: () => void;

  /**
   * The path's display title — the context chip's subject (*Shaping ·
   * {title}*). Learner-facing copy, so it is the title, never the topic: the
   * shaper's own prompt reads the frozen topic server-side, independent of
   * whatever the learner has renamed the path to.
   */
  title: string;
  messages: ShapingMessage[];
  status: ShapingRailStatus;
  /** The live reply, mid-stream. Empty once the turn settles, stops, or fails. */
  streamingText: string;
  /** Learner-facing copy for the last failed reply, or null. */
  errorMessage: string | null;

  draft: string;
  setDraft: (value: string) => void;
  send: (content: string, source: TutorMessageSource) => void;
  /** A one-tap ask: sends it, or prefills the composer when it is a stem. */
  useSuggestion: (suggestion: ShapingSuggestion) => void;
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

export interface UseShapingRailOptions {
  pathId: string;
  /** The path's display title; "" until the detail query lands. */
  title: string;
  /**
   * Whether the outline is `ready`. There is no structure to shape before it is
   * (PRD §5.1), so the entry point is absent — not disabled. The server says the
   * same thing with a `409` (TDD §5.5); this is the convenience in front of it.
   */
  pathReady: boolean;
}

interface PendingQuestion {
  content: string;
  source: TutorMessageSource;
}

/**
 * What an ended stream should leave behind in the composer. `restore` is stop —
 * the learner wants to edit the question, so it goes back where they left it.
 * `discard` is new conversation and unmount: they asked for the thread to go
 * away, and putting the abandoned question back into an emptied rail would be
 * the opposite of what they said.
 */
type EndStreamMode = "restore" | "discard";

export function useShapingRail({
  pathId,
  title,
  pathReady,
}: UseShapingRailOptions): ShapingRailState {
  const queryClient = useQueryClient();
  const flagOn = useFeatureFlag(SHAPING_FLAG);
  // The same cached session the flag came from — one read, two answers.
  const session = useQuery(sessionQueryOptions);
  const enabled = flagOn && pathReady;

  const [openState, setOpenState] = useState(false);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<ShapingRailStatus>("idle");
  const [streamingText, setStreamingText] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [confirmingNew, setConfirmingNew] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  const [model, setModel] = useState("");

  // Refs, not state, for everything the stream callbacks touch: deltas land
  // faster than React re-renders, and the settle path needs the *final* text and
  // proposal payload, not whatever a closure captured when the send started.
  const pendingRef = useRef<PendingQuestion | null>(null);
  const textRef = useRef("");
  const proposalRef = useRef<Proposal | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const endModeRef = useRef<EndStreamMode>("restore");

  // `skipToken` when the entry point is not rendered: a gated-off surface costs
  // no request at all, which is what makes shipping dark actually dark.
  const conversationQuery = useQuery(shapingConversationQueryOptions(enabled ? pathId : null));
  const messages = conversationQuery.data?.messages ?? [];

  const appendTurn = useCallback(
    async (
      ids: { learner_message_id: string; tutor_message_id: string },
      question: PendingQuestion,
      reply: string,
      proposal: Proposal | null,
    ) => {
      const key = shapingConversationQueryKey(pathId);
      // Cancel first: a GET that started before this turn would resolve without
      // it and briefly un-append the reply the learner just watched arrive.
      await queryClient.cancelQueries({ queryKey: key });
      const now = new Date().toISOString();
      queryClient.setQueryData<ShapingConversation>(key, (old) => ({
        messages: [
          ...(old?.messages ?? []),
          {
            id: ids.learner_message_id,
            role: "learner",
            content: question.content,
            proposal: null,
            created_at: now,
          },
          {
            id: ids.tutor_message_id,
            role: "tutor",
            content: reply,
            // The whole reason the stream's `proposal` event is captured: the
            // card (AL-331) reads it off the cached message, not off transient
            // streaming state. A proposal that just arrived is `pending` by
            // construction — nothing could have applied it yet — so the client
            // states the resolution it *knows*, and every later read takes the
            // server's derived one (TDD §4).
            proposal: proposal === null ? null : { ...proposal, resolution: "pending" },
            created_at: now,
          },
        ],
      }));
    },
    [pathId, queryClient],
  );

  /**
   * **The one way an in-flight stream ends from the outside.** Stop, new
   * conversation and unmount all come through here, and nothing else in this
   * hook touches `abortRef` — with one entry point, "the stream is over" and
   * "the stream's settle path is cancelled" cannot come apart.
   *
   * Idempotent, and a no-op once the stream has stopped being interruptible:
   * `run` releases `abortRef` the moment the `done` frame lands.
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
      const controller = new AbortController();
      abortRef.current = controller;
      endModeRef.current = "restore";
      pendingRef.current = question;
      textRef.current = "";
      proposalRef.current = null;
      // The composer is emptied here rather than in `send`, so every path into a
      // stream clears it and every path out of one restores it.
      setDraft("");
      setStreamingText("");
      setErrorMessage(null);
      setStatus("streaming");

      try {
        const done = await streamShapingReply({
          pathId,
          input: {
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
          onProposal: (proposal) => {
            proposalRef.current = proposal;
          },
        });
        // Released *before* the cache write, not in a `finally` after it: from
        // the `done` frame on there is nothing left to abort.
        abortRef.current = null;
        await appendTurn(done, question, textRef.current, proposalRef.current);
        pendingRef.current = null;
        setStreamingText("");
        setStatus("idle");
      } catch (error) {
        // Whatever happened, the partial reply is gone (invariant 3) — and so is
        // any proposal it carried: a Proposal the learner cannot see cannot be
        // consented to, and consent is the whole contract here.
        abortRef.current = null;
        setStreamingText("");
        textRef.current = "";
        proposalRef.current = null;
        if (isAbort(error)) {
          if (endModeRef.current === "restore") setDraft(question.content);
          pendingRef.current = null;
          setStatus("idle");
          return;
        }
        // Failure keeps the question twice over: in `pendingRef`, which is what
        // "Try again" re-sends, and in the composer, which is what the card's
        // "Your question is still here" claims.
        setDraft(question.content);
        setErrorMessage(failureCopy(error));
        setStatus("failed");
      }
    },
    [pathId, model, appendTurn],
  );

  const send = useCallback(
    (content: string, source: TutorMessageSource) => {
      const trimmed = content.trim();
      if (trimmed === "" || status === "streaming") return;
      void run({ content: trimmed, source });
    },
    [run, status],
  );

  // Unmount discards the turn, matching what the server does with it: the
  // learner navigated away, the socket drops, and a reply nobody is watching is
  // persisted by neither side.
  useEffect(() => () => endStream("discard"), [endStream]);

  /**
   * **A different path is a different conversation.**
   *
   * The sidebar switcher goes `/paths/A` -> `/paths/B` on the *same* route, so
   * only the `pathId` param changes: TanStack Router re-renders `PathView`
   * rather than remounting it, nothing unmounts, and the effect above never
   * fires. Every piece of state below would otherwise follow the learner onto a
   * path it has nothing to do with — A's live reply streaming into B's rail, A's
   * failure card and its question sitting over B's empty thread, A's per-message
   * model override riding B's next send.
   *
   * So this does by hand what a remount would have done for free. It is the
   * *whole* of the surface's state on purpose, including `open`: arriving on a
   * new path with the previous path's rail already open would be the rail
   * claiming continuity it does not have, and reopening is one tap. The cached
   * threads need no help — they are keyed per path already.
   *
   * `endStream("discard")` first, and for the unmount's reason: the learner
   * walked away from that turn, so it is hung up on and its question is not put
   * back into a composer that now belongs to somewhere else.
   */
  const currentPathRef = useRef(pathId);
  useEffect(() => {
    if (currentPathRef.current === pathId) return;
    currentPathRef.current = pathId;
    endStream("discard");
    pendingRef.current = null;
    textRef.current = "";
    proposalRef.current = null;
    setOpenState(false);
    setDraft("");
    setStatus("idle");
    setStreamingText("");
    setErrorMessage(null);
    setConfirmingNew(false);
    setClearError(null);
    setModel("");
  }, [pathId, endStream]);

  const clearMutation = useMutation({
    mutationFn: (id: string) => clearShapingConversation(id),
    // Only the cache. Every piece of local state this clear touches is reset
    // synchronously in `confirmNewConversation`, before the request goes out.
    onSuccess: async (_result, id) => {
      const key = shapingConversationQueryKey(id);
      await queryClient.cancelQueries({ queryKey: key });
      // The thread is gone server-side and the next turn creates a fresh row, so
      // the empty list is certain — write it rather than refetch it. The change
      // history is untouched by design (TDD D3): history belongs to the path.
      queryClient.setQueryData<ShapingConversation>(key, { messages: [] });
    },
    onError: () => setClearError(CLEAR_FAILURE_COPY),
  });

  return {
    open: enabled && openState,
    showMark: enabled && !openState,
    openRail: () => setOpenState(true),
    closeRail: () => setOpenState(false),

    title,
    messages,
    status,
    streamingText,
    errorMessage,

    draft,
    setDraft,
    send,
    useSuggestion: (suggestion) => {
      if (suggestion.prefill !== undefined) {
        // A stem, not an ask: fill the composer and leave the learner in it.
        setDraft(suggestion.prefill);
        return;
      }
      send(suggestion.label, "suggestion");
    },
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
      // Stop first, always. The `DELETE` empties the thread, and a stream still
      // running would append its turn onto the emptied thread when it settled.
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
