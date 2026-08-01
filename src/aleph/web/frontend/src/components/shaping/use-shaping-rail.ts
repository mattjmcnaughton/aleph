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
import { PATHS_LIST_QUERY_KEY, pathQueryKey } from "../../lib/api";
import { sessionQueryOptions } from "../../lib/auth";
import { useFeatureFlag } from "../../lib/feature-flags";
import {
  type Change,
  type ProposalCardState,
  type ShapingConversation,
  type ShapingConflictReason,
  type ShapingMessage,
  applyProposal,
  changeHistoryQueryKey,
  changeHistoryQueryOptions,
  clearShapingConversation,
  conflictGroup,
  conflictReasonOf,
  proposalCardState,
  shapingConversationQueryKey,
  shapingConversationQueryOptions,
  undoChange,
  undoableChangeId,
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

/**
 * Everything the card for one message needs, already decided (AL-331, TDD §8).
 *
 * The card is handed a *state*, never the five facts behind it: `lib/shaping.ts`
 * owns that table as a pure function, so the component renders and the state
 * machine is asserted directly. `message` is the server's own learner-facing
 * wording for the last refusal (docs/api.md — the `409`'s `message` is written
 * for a learner, never provider text), and `reason` is what the affordance is
 * chosen from.
 */
export interface ProposalCardStatus {
  state: ProposalCardState;
  /** The last refusal's coded reason, or null. */
  reason: ShapingConflictReason | null;
  /** Learner-facing copy for the last refusal, or null. */
  message: string | null;
}

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

  // --- Apply / Not now (AL-331, TDD §8) --------------------------------------

  /** This message's card state, plus the wording for its last refusal. */
  proposalStatus: (messageId: string) => ProposalCardStatus;
  /** **Apply** — the explicit tap, and the only write path into the path. */
  applyProposal: (messageId: string) => void;
  /** **Not now** — pure UI dismissal. No request, nothing persisted. */
  dismissProposal: (messageId: string) => void;
  /**
   * "Ask again" on a stale (or undone) card: puts the learner's own question for
   * that turn back in the composer, and nothing else. The card keeps saying what
   * happened to the Proposal — that explanation is what the learner re-asks
   * *from*, and §8 has no state that retires it.
   */
  askAgain: (messageId: string) => void;
  /** "View in path": stand out of the way of the path this just changed. */
  viewInPath: () => void;

  /**
   * The Proposal the path rail previews as **ghost rows** — the newest one still
   * pending in the *open* thread, or null. Ghosts exist only while a proposal is
   * pending and the rail is open (TDD §8).
   */
  ghostProposal: Proposal | null;

  // --- Change history sheet --------------------------------------------------

  /** The read-only history sheet is open over the thread. */
  historyOpen: boolean;
  openHistory: () => void;
  closeHistory: () => void;
  /** The path's Changes, newest first. Empty until the sheet has been opened. */
  changes: Change[];
  changesLoading: boolean;
  /** `GET /changes` failed — the sheet says so rather than showing an empty record. */
  changesError: boolean;
  /**
   * The only Change this client offers **Undo** for: the newest live one (LIFO).
   * A convenience in front of the server's `409 not_latest`, never the rule.
   */
  undoableChangeId: string | null;
  /** The Change whose undo is in flight, or null. */
  undoingChangeId: string | null;
  undoChange: (changeId: string) => void;
  /** The last undo refusal: which Change, why, and the server's own wording. */
  undoError: { changeId: string; reason: ShapingConflictReason | null; message: string } | null;
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

/**
 * What one proposal card's **Apply** has done so far. Per message id, because a
 * thread can hold several cards and each one is applied on its own tap.
 *
 * `applied` is a boolean rather than the returned `ChangeDTO`: nothing on the
 * card needs the Change's id (Undo lives on the history sheet, which carries
 * every id by definition — `ProposalDTO`'s docstring), so holding it would be
 * a second copy of state the sheet already reads from the server.
 */
interface ApplyState {
  applying: boolean;
  applied: boolean;
  reason: ShapingConflictReason | null;
  /** The server's learner-facing message for the refusal, or the failure copy. */
  message: string | null;
}

const IDLE_APPLY: ApplyState = { applying: false, applied: false, reason: null, message: null };

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

  // AL-331's own state: per-card apply, per-card dismissal, and the sheet.
  const [applyStates, setApplyStates] = useState<Record<string, ApplyState>>({});
  const [dismissed, setDismissed] = useState<readonly string[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [undoingChangeId, setUndoingChangeId] = useState<string | null>(null);
  const [undoError, setUndoError] = useState<ShapingRailState["undoError"]>(null);

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

  // The Change history costs a request exactly when a learner asks to read it:
  // idle on `skipToken` until the sheet is open (and never at all when dark).
  const historyQuery = useQuery(changeHistoryQueryOptions(enabled && historyOpen ? pathId : null));
  const changes = historyQuery.data?.changes ?? [];

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

  // --- Apply, Not now, Undo (AL-331, TDD §5.6–§5.8) --------------------------
  //
  // Two guards live in refs rather than in state, for the reason `abortRef`
  // does: a double tap must not start a second write, and reading "is this one
  // already in flight" off state would read whatever the render that owned the
  // handler captured.
  const applyingRef = useRef<Set<string>>(new Set());
  const undoingRef = useRef<string | null>(null);

  const setApplyState = useCallback((messageId: string, next: ApplyState) => {
    setApplyStates((old) => ({ ...old, [messageId]: next }));
  }, []);

  const apply = useCallback(
    async (messageId: string) => {
      if (applyingRef.current.has(messageId)) return;
      applyingRef.current.add(messageId);
      setApplyState(messageId, { ...IDLE_APPLY, applying: true });
      try {
        const result = await applyProposal(messageId);
        // **Ghosts become real rows in one round trip.** The response's `path`
        // is byte-for-byte `GET /paths/{id}`, so it goes straight into the cache
        // the path rail already renders from: no second fetch, and no frame in
        // which the iris ghosts and the teal rows they became are both on
        // screen. (Requesting it is also what kicks the prefetch, §5.6.)
        //
        // Cancel first, for `appendTurn`'s reason and then some: the path route
        // polls `GET /paths/{id}` the whole time any lesson is generating, so a
        // poll started before this apply can resolve after it — and it carries
        // the *pre-apply* outline, which would land on top of the rows that were
        // just applied. The card would read "applied" over a rail the new rows
        // had vanished from, until the next tick put them back.
        await queryClient.cancelQueries({ queryKey: pathQueryKey(pathId) });
        queryClient.setQueryData(pathQueryKey(pathId), result.path);
        setApplyState(messageId, { ...IDLE_APPLY, applied: true });
        setDismissed((old) => old.filter((id) => id !== messageId));
        await Promise.all([
          // The history moved, and so did the switcher's lesson counts. The
          // detail was just written above, so it is deliberately *not* in the
          // invalidation — that would refetch what we already hold.
          queryClient.invalidateQueries({ queryKey: changeHistoryQueryKey(pathId) }),
          queryClient.invalidateQueries({ queryKey: PATHS_LIST_QUERY_KEY }),
          // Other cards in this thread may now be `superseded`; resolution is
          // derived server-side and this is the only way to learn it.
          queryClient.invalidateQueries({ queryKey: shapingConversationQueryKey(pathId) }),
        ]);
      } catch (error) {
        const reason = conflictReasonOf(error);
        setApplyState(messageId, {
          applying: false,
          applied: false,
          reason,
          // The server words its own refusals for a learner (docs/api.md), so
          // its message is used verbatim; only a transport failure gets ours.
          message: failureCopy(error),
        });
        // A nothing-to-do refusal says the server knows something this thread
        // read does not. Take its word for it rather than leaving the card's
        // state resting on the refusal alone.
        if (reason !== null && conflictGroup(reason) === "nothing_to_do") {
          await queryClient.invalidateQueries({
            queryKey: shapingConversationQueryKey(pathId),
          });
        }
      } finally {
        applyingRef.current.delete(messageId);
      }
    },
    [pathId, queryClient, setApplyState],
  );

  const undo = useCallback(
    async (changeId: string) => {
      if (undoingRef.current !== null) return;
      undoingRef.current = changeId;
      setUndoingChangeId(changeId);
      setUndoError(null);
      try {
        await undoChange(changeId);
        // Undo answers `204` and restores the path exactly, so everything it
        // touched is re-read rather than guessed at: the outline whose rows it
        // deleted or restored, the history whose row it moved, and the thread
        // whose proposal resolutions are derived from that row.
        setApplyStates({});
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: pathQueryKey(pathId) }),
          queryClient.invalidateQueries({ queryKey: changeHistoryQueryKey(pathId) }),
          queryClient.invalidateQueries({ queryKey: PATHS_LIST_QUERY_KEY }),
          queryClient.invalidateQueries({ queryKey: shapingConversationQueryKey(pathId) }),
        ]);
      } catch (error) {
        setUndoError({
          changeId,
          reason: conflictReasonOf(error),
          message: failureCopy(error),
        });
      } finally {
        undoingRef.current = null;
        setUndoingChangeId(null);
      }
    },
    [pathId, queryClient],
  );

  /** One card's state, decided by the pure table in `lib/shaping.ts`. */
  const proposalStatus = (messageId: string): ProposalCardStatus => {
    const message = messages.find((candidate) => candidate.id === messageId);
    const applyState = applyStates[messageId] ?? IDLE_APPLY;
    return {
      state: proposalCardState({
        resolution: message?.proposal?.resolution ?? "pending",
        applying: applyState.applying,
        applied: applyState.applied,
        dismissed: dismissed.includes(messageId),
        conflict: applyState.reason,
      }),
      reason: applyState.reason,
      message: applyState.message,
    };
  };

  /**
   * The Proposal the path rail previews (TDD §8: "ghosts exist only while a
   * proposal is pending in the open thread"). The **newest** such card wins:
   * two pending proposals are two competing futures, and drawing both would
   * preview a path neither of them describes.
   */
  const ghostProposal = ((): Proposal | null => {
    if (!enabled || !openState) return null;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.proposal === null) continue;
      const { state } = proposalStatus(message.id);
      if (state === "pending" || state === "applying") return message.proposal;
    }
    return null;
  })();

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
    // AL-331's state is per-message and per-path too: a card applied on path A
    // has nothing to say about B's thread, and B's history is not A's.
    applyingRef.current.clear();
    undoingRef.current = null;
    setApplyStates({});
    setDismissed([]);
    setHistoryOpen(false);
    setUndoingChangeId(null);
    setUndoError(null);
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
      // The thread is going, and every card in it with it. The **Change
      // history** is untouched by design (D3) — history belongs to the path.
      setApplyStates({});
      setDismissed([]);
      clearMutation.mutate(pathId);
    },
    clearError,

    model,
    setModel,
    isAdmin: session.data?.user?.is_admin ?? false,
    modelAllowlist: session.data?.user?.model_allowlist ?? [],

    proposalStatus,
    applyProposal: (messageId) => void apply(messageId),
    // Pure UI dismissal: no request, nothing persisted (PRD §5.4). It is not
    // even remembered across a reload, which is right — the Proposal is still
    // `pending` server-side, so declining must not quietly spend it.
    dismissProposal: (messageId) =>
      setDismissed((old) => (old.includes(messageId) ? old : [...old, messageId])),
    askAgain: (messageId) => {
      // The learner's own words for that turn, back in the composer: "ask again"
      // means ask the same thing of a path that has since moved, and retyping it
      // is the only part of that a client can save them.
      //
      // It deliberately does *not* dismiss the card. `dismissed` is **Not now**
      // and only Not now (PRD §5.4), and it ranks below stale/undone in
      // `proposalCardState` anyway — so marking it here would be a no-op that
      // read like a state change. The card goes on saying why this offer died,
      // which is what the learner is re-asking from.
      const index = messages.findIndex((message) => message.id === messageId);
      for (let before = index - 1; before >= 0; before -= 1) {
        if (messages[before].role === "learner") {
          setDraft(messages[before].content);
          break;
        }
      }
    },
    viewInPath: () => {
      // Stand out of the way of the thing that just changed. Below `lg` the rail
      // is a sheet over the path, so closing it *is* "view in path"; at `lg` the
      // path was never covered and closing costs a tap to reopen — one behavior
      // at both widths, because open/closed is shared state and never a branch
      // on viewport width (D12/D14).
      setOpenState(false);
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    ghostProposal,

    historyOpen,
    openHistory: () => setHistoryOpen(true),
    closeHistory: () => setHistoryOpen(false),
    changes,
    changesLoading: historyQuery.isPending && historyQuery.fetchStatus !== "idle",
    changesError: historyQuery.isError,
    undoableChangeId: undoableChangeId(changes),
    undoingChangeId,
    undoChange: (changeId) => void undo(changeId),
    undoError,
  };
}
