// The tutor's streaming client (AL-230, TDD §5.4 / D1): `fetch` +
// `ReadableStream` SSE parsing for `POST /paths/{id}/conversation/messages`.
//
// **Why this is the one module that calls `fetch` directly.** `apiFetch` exists
// so nothing else has to — but it awaits `res.json()`, which is exactly the
// blocking wait progressive rendering exists to avoid (PRD §5.6). So this seam
// keeps everything else `api.ts` owns (the `/api/v1` prefix through
// `apiV1Path`, the shared `ApiError` envelope) and departs on one point: it
// reads the body instead of buffering it. `EventSource` is not an option — it
// cannot POST or carry a body (docs/api.md).
//
// **Failures are one shape to the caller.** Three different things can go
// wrong, and the composer's state machine treats them identically ("the reply
// failed, the question is preserved"):
//   - a pre-stream JSON error envelope (401/403/404/409/422/429) -> `ApiError`,
//     because SSE has not started and the response *is* an ordinary API error;
//   - a terminal `error` event -> `TutorStreamError` with the server's `code`;
//   - the socket dropping with no terminal event, or a frame whose `data` is not
//     JSON -> `TutorStreamError` (`stream_dropped` / `stream_malformed`).
//     Exactly one terminal event ends a healthy stream, so "the reader ran out"
//     is a failure, never a quiet success — and a stream that started and then
//     went unreadable is a *stream* failure, not a transport one, so the rail
//     never blames the learner's connection for it.
// Stop is not a failure and not an endpoint: the caller aborts the request and
// gets the `AbortError` its signal raised, untouched (TDD §5.6).
//
// **Two conversations, one transport** (AL-330; Phase 2B TDD §5.4: "Phase 2 §5.4
// verbatim … plus one named event"). The shaping thread on the path route
// streams from a different endpoint, with a different body, and carries one
// extra event — and nothing else about it differs, so `streamShapingReply` below
// is a second entry point into the *same* reader rather than a second reader. A
// shaping-only copy of this loop would be a second place for chunk reassembly,
// heartbeats and the stop race to drift apart.

import { ApiError, apiErrorFrom, apiV1Path } from "./api";

/** How a learner message was entered — the §7 entry-mix datum (docs/api.md). */
export type TutorMessageSource = "typed" | "suggestion";

/**
 * `TutorMessageStr` (docs/api.md) — the server bound, enforced in the composer
 * too. **One constant, both threads**: the shaping body reuses the very same
 * server type, so a second copy of the number could only ever drift from it.
 */
export const TUTOR_MESSAGE_MAX_LENGTH = 2000;

/**
 * The tutor's own non-scoring question (TDD §6). Unlike `QuickCheck`, this
 * carries `correct_index` + `explanation` on delivery *by design* — feedback is
 * immediate and client-side, and nothing downstream grades it.
 */
export interface TutorCheck {
  stem: string;
  options: string[];
  correct_index: number;
  explanation: string;
  /** The learner's choice, or null until the check-answer route records one. */
  answered_index: number | null;
}

// --- The shaping wire (Phase 2B TDD §4/§5.4) --------------------------------
//
// The **Proposal** payload, exactly as `agents/shaper.py` validates it. Two
// operation shapes and no more (D1), and they are **untagged on purpose**: the
// server's union discriminates structurally, so the client does too
// (`isAddLessons` in `lib/shaping.ts`) rather than inventing a `kind` field the
// wire does not carry.

/** One lesson an Addition creates — a title, and Phase 1 generates the rest. */
export interface ProposedLesson {
  title: string;
}

/** The unit an Addition may group its new lessons into, or `null` for none. */
export interface ProposedUnit {
  title: string;
  summary: string;
}

/** `add_lessons`: new lessons at a position (CONTEXT.md: *Addition*). */
export interface AddLessonsOperation {
  /** A `position_in_path` in the payload's snapshot; apply re-resolves it (D5). */
  insert_at_position: number;
  lessons: ProposedLesson[];
  rationale: string;
  estimated_minutes: number;
  new_unit: ProposedUnit | null;
}

/** `revise_lesson`: re-teach an unengaged lesson (CONTEXT.md: *Revision*). */
export interface ReviseLessonOperation {
  lesson_id: string;
  instruction: string;
  rationale: string;
  new_title: string | null;
}

export type ProposalOperation = AddLessonsOperation | ReviseLessonOperation;

/**
 * The `proposal` event's data: the full validated payload (TDD §5.4). Note what
 * is *not* here — a resolution. A proposal arriving on the stream is pending by
 * construction (nothing has applied it yet); resolution is derived server-side
 * and reaches the client only on a thread read (`MessageProposal`, `shaping.ts`).
 */
export interface Proposal {
  operations: ProposalOperation[];
  summary: string;
}

/** `POST /paths/{id}/conversation/messages` body (docs/api.md). */
export interface SendTutorMessageInput {
  lesson_id: string;
  /** ≤ 2000 chars (`TutorMessageStr`) — the composer enforces it too. */
  content: string;
  source: TutorMessageSource;
  /** Admin-only per-message override; omitted entirely when unset (403 else). */
  model?: string;
}

/**
 * `POST /paths/{id}/shaping/conversation/messages` body (Phase 2B TDD §6).
 *
 * The 2A body minus `lesson_id`: the shaping thread is one per *path* and is
 * never asked from inside a lesson (PRD §5.8), so there is no lesson to record.
 */
export interface SendShapingMessageInput {
  /** ≤ 2000 chars (`TutorMessageStr`, reused) — the composer enforces it too. */
  content: string;
  source: TutorMessageSource;
  /** Admin-only per-message override; omitted entirely when unset (403 else). */
  model?: string;
}

/** The `done` event: terminal success, the turn is persisted (TDD D2). */
export interface TutorStreamDone {
  learner_message_id: string;
  tutor_message_id: string;
}

/** A stream that started and then failed — an `error` event or a dropped socket. */
export class TutorStreamError extends Error {
  constructor(
    message: string,
    /** `timeout` / `upstream_error` / `internal_error`, or `stream_dropped`. */
    public readonly code: string,
  ) {
    super(message);
    this.name = "TutorStreamError";
  }
}

/**
 * Everything a reply stream can deliver on its way to a terminal event. Both
 * conversations pass the same object; each simply leaves the handlers it has no
 * event for undefined, which is also what makes an unknown event harmless.
 */
interface StreamHandlers {
  /** Fires for every `delta`; concatenated in order, the deltas are the reply. */
  onDelta?: (text: string) => void;
  /** Fires when the tutor posed a Tutor check (mid-stream; may land between deltas). */
  onTutorCheck?: (check: TutorCheck) => void;
  /** Fires when the shaper called `propose_path_edit` (Phase 2B TDD §5.4). */
  onProposal?: (proposal: Proposal) => void;
}

export interface StreamTutorReplyOptions extends StreamHandlers {
  pathId: string;
  input: SendTutorMessageInput;
  /** The stop affordance: aborting rejects with the signal's `AbortError`. */
  signal?: AbortSignal;
}

export interface StreamShapingReplyOptions extends StreamHandlers {
  pathId: string;
  input: SendShapingMessageInput;
  /** The stop affordance: aborting rejects with the signal's `AbortError`. */
  signal?: AbortSignal;
}

/** Copy for a stream that ended without a terminal event (never provider text). */
const DROPPED_MESSAGE = "The tutor didn't finish answering.";

/** Copy for a frame whose `data` is not the JSON this wire promises. */
const MALFORMED_MESSAGE = "The tutor's reply didn't come through.";

/** The in-lesson tutor's reply stream (Phase 2 §5.4) — unchanged by 2B. */
export function streamTutorReply({
  pathId,
  input,
  signal,
  ...handlers
}: StreamTutorReplyOptions): Promise<TutorStreamDone> {
  return streamReply(apiV1Path(`/paths/${pathId}/conversation/messages`), input, handlers, signal);
}

/**
 * The **shaping** reply stream (Phase 2B TDD §5.4/§6): same transport, the
 * shaping conversation's endpoint, and the `proposal` event on top.
 *
 * A non-`ready` path answers `409` *before* streaming starts (TDD §5.5), so it
 * arrives as an ordinary `ApiError` — the same shape the rail already words for
 * every other pre-stream failure. The entry point is hidden on those paths
 * anyway; this is the server backstop behind it.
 */
export function streamShapingReply({
  pathId,
  input,
  signal,
  ...handlers
}: StreamShapingReplyOptions): Promise<TutorStreamDone> {
  return streamReply(
    apiV1Path(`/paths/${pathId}/shaping/conversation/messages`),
    input,
    handlers,
    signal,
  );
}

/**
 * The reader both conversations share. Everything below this line was AL-230's
 * and is untouched by AL-330 except for where the endpoint and the body come
 * from — the two things that actually differ between the threads.
 */
async function streamReply(
  url: string,
  body: SendTutorMessageInput | SendShapingMessageInput,
  handlers: StreamHandlers,
  signal: AbortSignal | undefined,
): Promise<TutorStreamDone> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  // Pre-stream failure: SSE starts only once the turn is admitted, so a non-2xx
  // here is an ordinary error envelope and raises the same `ApiError` every
  // other call in the app does.
  if (!response.ok || response.body === null) {
    throw await apiErrorFrom(response);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const aborted = whenAborted(signal);
  let buffer = "";

  // Cancelling a body stream that has already errored rejects; the caller's
  // failure is the one worth reporting, so every cancel here is fire-and-forget
  // rather than `void`, which would leave that rejection unhandled.
  const cancelRead = () => {
    reader.cancel().catch(() => {});
  };

  for (;;) {
    // Raced against the signal rather than trusting the body stream to error:
    // stop must be immediate, and a buffering proxy (or a test double) can hold
    // a socket open long after the fetch was abandoned.
    const { done, value } = await Promise.race([reader.read(), aborted]).catch((error: unknown) => {
      cancelRead();
      throw error;
    });
    if (done) break;
    // `stream: true` is what makes a multi-byte character split across two
    // network chunks survive; the buffer is what makes a split *frame* survive.
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      let terminal: Terminal | null;
      try {
        terminal = handleFrame(buffer.slice(0, boundary), handlers);
      } catch (error) {
        // An unparseable frame ends the stream like any other failure — and the
        // reader is released on the way out, exactly as a terminal event does.
        cancelRead();
        throw error;
      }
      if (terminal) {
        // Terminal event: stop reading and let the server close its end. The
        // fetch is not aborted — that would look like a stop to the server.
        cancelRead();
        if (terminal.kind === "done") return terminal.data;
        throw new TutorStreamError(terminal.message, terminal.code);
      }
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
  }

  throw new TutorStreamError(DROPPED_MESSAGE, "stream_dropped");
}

/**
 * A promise that rejects with the abort reason and otherwise never settles —
 * the stop affordance's half of the read race. `AbortError` is what a caller
 * distinguishes stop from failure by, so the reason is passed through as the
 * platform raised it.
 *
 * The no-op `.catch` is not decoration: the loop only *holds* this promise while
 * it is racing a read. A stop landing in the window between the `done` frame and
 * the caller's own settle would reject a promise nobody is awaiting, which is an
 * unhandled rejection. Attaching a handler at creation makes the rejection
 * always handled; the loop still races the original, so the reason it sees is
 * unchanged.
 */
function whenAborted(signal: AbortSignal | undefined): Promise<never> {
  const promise = new Promise<never>((_resolve, reject) => {
    if (signal === undefined) return;
    const fail = () =>
      reject(
        signal.reason instanceof Error
          ? signal.reason
          : new DOMException("The reply was stopped.", "AbortError"),
      );
    if (signal.aborted) fail();
    else signal.addEventListener("abort", fail, { once: true });
  });
  promise.catch(() => {});
  return promise;
}

type Terminal =
  | { kind: "done"; data: TutorStreamDone }
  | { kind: "error"; code: string; message: string };

/**
 * Parse one SSE frame and dispatch it. Returns the terminal event when the
 * frame ends the stream, so the read loop stays a loop and this stays parsing.
 *
 * Comment lines (`: ping`, the 15s heartbeat) and unknown event names are
 * dropped: a healthy stream must survive both, and a client that treats an
 * unrecognised event as fatal cannot be extended additively.
 *
 * A `data` line that is not JSON is the one parse failure that *is* fatal, and
 * it throws a `TutorStreamError`: the stream started, so this is a stream
 * failure the rail should word as one — a raw `SyntaxError` escaping here would
 * read to the learner as "check your connection", which is never true by then.
 */
function handleFrame(frame: string, handlers: StreamHandlers): Terminal | null {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    // `data` lines are always single-line JSON on this wire (docs/api.md), so
    // there is no multi-line accumulation rule to get wrong.
    else if (line.startsWith("data:")) data = line.slice("data:".length).trim();
  }
  if (data === "") return null;

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data) as Record<string, unknown>;
  } catch {
    throw new TutorStreamError(MALFORMED_MESSAGE, "stream_malformed");
  }
  switch (event) {
    case "delta":
      handlers.onDelta?.(String(payload.text ?? ""));
      return null;
    case "tutor_check":
      handlers.onTutorCheck?.(payload as unknown as TutorCheck);
      return null;
    // Delivered whole, mid-stream: the payload *is* the edit (CONTEXT.md —
    // a Proposal is data, not prose), and the reply's text keeps streaming
    // after it. One proposal per reply (D4), so a second would simply
    // overwrite the first on the caller's side — the server does not send one.
    //
    // The cast is 2A's `tutor_check` posture — `agents/shaper.py` validates the
    // payload before the service emits it, so the client does not re-derive the
    // schema — with one guard in front of it. The rail's card reads
    // `operations` structurally, so a payload without it would throw *out of
    // render*, taking the route down and wording nothing. Failing here instead
    // makes it the ordinary stream failure the rail already knows how to say.
    case "proposal":
      if (!Array.isArray(payload.operations)) {
        throw new TutorStreamError(MALFORMED_MESSAGE, "stream_malformed");
      }
      handlers.onProposal?.(payload as unknown as Proposal);
      return null;
    case "done":
      return { kind: "done", data: payload as unknown as TutorStreamDone };
    case "error":
      return {
        kind: "error",
        code: String(payload.code ?? "internal_error"),
        message: String(payload.message ?? DROPPED_MESSAGE),
      };
    default:
      return null;
  }
}

// --- What a rail does with a failure -----------------------------------------
//
// Both rails run their own state machine — that separation is deliberate and
// stays (W21) — but "was this a stop or a failure?" and "what do we say about
// it?" are **pure functions of the error**, with no state machine in them at
// all. They live here, next to the errors they discriminate, so the two
// surfaces cannot come to word the same failure two different ways.

/**
 * Stop, not failure. Matched on `name` rather than `instanceof DOMException`:
 * the rejection crosses realms (the fetch's abort reason is minted outside the
 * document's global), and an `instanceof` that is true in a browser can be false
 * under jsdom — which would turn every stop into a spurious error state.
 */
export function isAbort(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: string }).name === "AbortError"
  );
}

/** Transport-level failure copy. The only case where blaming the connection is honest. */
export const NETWORK_FAILURE_COPY =
  "The tutor didn't answer. Check your connection and send it again.";

/** New conversation failed. Nothing was cleared, so the offer is to try clearing again. */
export const CLEAR_FAILURE_COPY =
  "That didn't go through — this conversation is still here. Try clearing it again.";

/**
 * Learner-facing copy for a failed reply. The server already words its own
 * failures for a learner (never provider text) and carries a `code`, so its
 * message is used verbatim — that is what lets an upstream budget failure avoid
 * "check your connection", the wording gap PRD §5.7 names. Only a genuine
 * transport failure gets the connection copy.
 */
export function failureCopy(error: unknown): string {
  if (error instanceof TutorStreamError || error instanceof ApiError) {
    return error.message;
  }
  return NETWORK_FAILURE_COPY;
}
