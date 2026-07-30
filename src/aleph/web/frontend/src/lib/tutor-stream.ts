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

import { apiErrorFrom, apiV1Path } from "./api";

/** How a learner message was entered — the §7 entry-mix datum (docs/api.md). */
export type TutorMessageSource = "typed" | "suggestion";

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

/** `POST /paths/{id}/conversation/messages` body (docs/api.md). */
export interface SendTutorMessageInput {
  lesson_id: string;
  /** ≤ 2000 chars (`TutorMessageStr`) — the composer enforces it too. */
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

export interface StreamTutorReplyOptions {
  pathId: string;
  input: SendTutorMessageInput;
  /** Fires for every `delta`; concatenated in order, the deltas are the reply. */
  onDelta?: (text: string) => void;
  /** Fires when the tutor posed a Tutor check (arrives before its reply text). */
  onTutorCheck?: (check: TutorCheck) => void;
  /** The stop affordance: aborting rejects with the signal's `AbortError`. */
  signal?: AbortSignal;
}

/** Copy for a stream that ended without a terminal event (never provider text). */
const DROPPED_MESSAGE = "The tutor didn't finish answering.";

/** Copy for a frame whose `data` is not the JSON this wire promises. */
const MALFORMED_MESSAGE = "The tutor's reply didn't come through.";

export async function streamTutorReply({
  pathId,
  input,
  onDelta,
  onTutorCheck,
  signal,
}: StreamTutorReplyOptions): Promise<TutorStreamDone> {
  const response = await fetch(apiV1Path(`/paths/${pathId}/conversation/messages`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
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
        terminal = handleFrame(buffer.slice(0, boundary), onDelta, onTutorCheck);
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
function handleFrame(
  frame: string,
  onDelta?: (text: string) => void,
  onTutorCheck?: (check: TutorCheck) => void,
): Terminal | null {
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
      onDelta?.(String(payload.text ?? ""));
      return null;
    case "tutor_check":
      onTutorCheck?.(payload as unknown as TutorCheck);
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
