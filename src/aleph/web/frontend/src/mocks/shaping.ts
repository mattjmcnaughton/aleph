// Contract-shaped fakes for the Shaping API (AL-330; TDD §5.4/§6 fix the wire,
// AL-320 serves it live). Verified field by field against the merged backend —
// `dtos/shaping.py` for the thread read, `services/shaping.py` for the frames,
// `errors.py` for the pre-stream envelope — so these fakes are the real wire and
// not a memory of it. Deliberately a sibling of `mocks/tutor.ts` rather than an
// extension of it: the two threads are separate by design (PRD §5.8), so their
// fakes hold separate stores and a test can prove the in-lesson rail never sees
// a shaping turn simply because nothing connects them.
//
// The send endpoint answers a real `ReadableStream` `text/event-stream` body, so
// the rail's parser and its whole composer state machine run over the actual
// wire shape — the Lane-B pattern the epic names: build against streamed
// fixtures now, integrate when the backend lands.
//
// One shaping conversation **per path**, created lazily: a path nobody has
// shaped answers `200 {messages: []}`, never `404`.

import { HttpResponse, http } from "msw";
import { API_V1_BASE } from "../lib/api";
import type { ShapingMessage } from "../lib/shaping";
import type { Proposal, SendShapingMessageInput } from "../lib/tutor-stream";
import { frame } from "./tutor";

interface ShapingConfig {
  /** One `delta` event per item — the reply text, streamed in fragments. */
  replyDeltas: string[];
  /** Emitted as a `proposal` event after the deltas when set (TDD §5.4). */
  proposal: Proposal | null;
  /** When set, the stream ends in an `error` event instead of `done`. */
  failWith: { code: string; message: string } | null;
  /**
   * When true the stream stalls after its deltas instead of closing, so the only
   * ways out are the client aborting (stop / new conversation / unmount) or a
   * test calling `finishShapingStream()`.
   */
  hang: boolean;
  /**
   * When set, the POST answers a pre-stream JSON envelope and never streams —
   * the shape every admission failure takes (§5.5: "SSE starts only once the
   * turn is admitted"), most notably the `409 conflict` for a non-`ready` path
   * and for a reply already in flight on this conversation.
   */
  preStreamError: { status: number; code: string; message: string } | null;
}

const defaultConfig: ShapingConfig = {
  replyDeltas: ["Two short lessons ", "would close that gap."],
  proposal: null,
  failWith: null,
  hang: false,
  preStreamError: null,
};

let config: ShapingConfig = { ...defaultConfig };

/** path id -> the shaping thread, oldest first. Absent = no conversation row. */
const store = new Map<string, ShapingMessage[]>();

/** Every send body the fake received, in order — asserted on directly. */
let sentBodies: SendShapingMessageInput[] = [];

/** How many `DELETE /shaping/conversation` calls landed (new conversation). */
let clearCount = 0;

/** How many `GET /shaping/conversation` reads landed — 0 proves a dark surface. */
let readCount = 0;

/** How many sends the client hung up on (stop, new conversation, unmount). */
let abortedSendCount = 0;

/** Settle callbacks for streams parked by `hang`, released on demand. */
let heldStreams: Array<() => void> = [];

export function resetShaping(): void {
  store.clear();
  sentBodies = [];
  clearCount = 0;
  readCount = 0;
  abortedSendCount = 0;
  heldStreams = [];
  config = { ...defaultConfig };
}

export function configureShaping(overrides: Partial<ShapingConfig>): void {
  config = { ...config, ...overrides };
}

export function shapingSendBodies(): SendShapingMessageInput[] {
  return sentBodies;
}

export function shapingClearCount(): number {
  return clearCount;
}

export function shapingReadCount(): number {
  return readCount;
}

export function shapingAbortedSendCount(): number {
  return abortedSendCount;
}

/**
 * Let every stream parked by `hang` run to its terminal event. Calling it after
 * an abort proves the client is really disconnected: a stream the client hung up
 * on persists nothing (D2) and delivers nothing.
 */
export function finishShapingStream(): void {
  const held = heldStreams;
  heldStreams = [];
  for (const settle of held) settle();
}

export interface SeedShapingMessageInput {
  id?: string;
  role: "learner" | "tutor";
  content: string;
  proposal?: Proposal | null;
  /** Derived server-side (TDD §4); the fake takes it as a given. */
  resolution?: "pending" | "applied" | "undone" | "superseded";
}

/** Pre-populate a path's shaping thread (a returning learner, PRD §5.8). */
export function seedShapingConversation(pathId: string, messages: SeedShapingMessageInput[]): void {
  store.set(
    pathId,
    messages.map((message, index) => ({
      id: message.id ?? `shaping-msg-${index}`,
      role: message.role,
      content: message.content,
      proposal: message.proposal
        ? { ...message.proposal, resolution: message.resolution ?? "pending" }
        : null,
      created_at: "2026-07-30T12:00:00Z",
    })),
  );
}

const encoder = new TextEncoder();

function persistTurn(
  pathId: string,
  body: SendShapingMessageInput,
  ids: { learner: string; tutor: string },
): void {
  const thread = store.get(pathId) ?? [];
  const now = new Date().toISOString();
  thread.push(
    {
      id: ids.learner,
      role: "learner",
      content: body.content,
      proposal: null,
      created_at: now,
    },
    {
      id: ids.tutor,
      role: "tutor",
      content: config.replyDeltas.join(""),
      // A freshly persisted proposal is always pending — nothing has applied it.
      proposal: config.proposal ? { ...config.proposal, resolution: "pending" } : null,
      created_at: now,
    },
  );
  store.set(pathId, thread);
}

export const shapingHandlers = [
  http.get(`${API_V1_BASE}/paths/:pathId/shaping/conversation`, ({ params }) => {
    readCount += 1;
    return HttpResponse.json({ messages: store.get(params.pathId as string) ?? [] });
  }),

  http.delete(`${API_V1_BASE}/paths/:pathId/shaping/conversation`, ({ params }) => {
    clearCount += 1;
    store.delete(params.pathId as string);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post(
    `${API_V1_BASE}/paths/:pathId/shaping/conversation/messages`,
    async ({ params, request }) => {
      const pathId = params.pathId as string;
      const body = (await request.json()) as SendShapingMessageInput;
      sentBodies.push(body);

      if (config.preStreamError) {
        const { status, code, message } = config.preStreamError;
        // The shared envelope verbatim (`errors.py`): `request_id` is always
        // present on it, so the fake carries one too — a client that quietly
        // depended on its absence would only find out in production.
        return HttpResponse.json(
          { error: { code, message, request_id: "req-shaping-pre-stream" } },
          { status },
        );
      }

      const turnIds = {
        learner: `shaping-learner-${sentBodies.length}`,
        tutor: `shaping-tutor-${sentBodies.length}`,
      };

      let hungUp = false;
      const hangUp = () => {
        if (hungUp) return;
        hungUp = true;
        abortedSendCount += 1;
      };
      request.signal.addEventListener("abort", hangUp);

      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          const settle = () => {
            if (hungUp) return;
            if (config.failWith) {
              controller.enqueue(encoder.encode(frame("error", config.failWith)));
            } else {
              persistTurn(pathId, body, turnIds);
              controller.enqueue(
                encoder.encode(
                  frame("done", {
                    learner_message_id: turnIds.learner,
                    tutor_message_id: turnIds.tutor,
                  }),
                ),
              );
            }
            controller.close();
          };

          for (const text of config.replyDeltas) {
            controller.enqueue(encoder.encode(frame("delta", { text })));
          }
          // After the prose, as the service observes it: the tool call lands
          // mid-reply and the payload is emitted when it does (TDD §5.4).
          if (config.proposal) {
            controller.enqueue(encoder.encode(frame("proposal", config.proposal)));
          }
          if (config.hang) {
            heldStreams.push(settle);
            return;
          }
          settle();
        },
        cancel: hangUp,
      });

      return new HttpResponse(stream, {
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
      });
    },
  ),
];
