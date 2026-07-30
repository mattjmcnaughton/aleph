// Contract-shaped fakes for the Tutor API (docs/api.md ## Tutor, AL-220/AL-221):
// the conversation read/clear routes and — the reason this file exists — the
// send endpoint's `text/event-stream` reply, served as a real `ReadableStream`
// body. MSW can stream, so the rail's parser and its whole state machine are
// exercised over the actual wire shape rather than against a stubbed module.
//
// Mirrors `mocks/lessons.ts`: an in-memory store, a `seedConversation` helper, a
// `configureTutor({...})` knob for the reply/failure shapes, and a `resetTutor`
// wired into tests/setup.ts.
//
// One conversation **per path** (not per lesson), created lazily: a path with no
// completed turn still answers `200 {messages: []}`, never `404`.

import { HttpResponse, http } from "msw";
import { API_V1_BASE } from "../lib/api";
import type { ConversationMessage } from "../lib/tutor";
import type { SendTutorMessageInput, TutorCheck } from "../lib/tutor-stream";

interface TutorConfig {
  /** One `delta` event per item — the reply text, streamed in fragments. */
  replyDeltas: string[];
  /** Emitted as a `tutor_check` event before the deltas when set. */
  check: TutorCheck | null;
  /** When set, the stream ends in an `error` event instead of `done`. */
  failWith: { code: string; message: string } | null;
  /**
   * When true the stream stalls after its deltas instead of closing, so the only
   * ways out are the client aborting (stop / new conversation / unmount) or a
   * test calling `finishTutorStream()`.
   */
  hang: boolean;
  /** When set, the POST answers a pre-stream JSON envelope and never streams. */
  preStreamError: { status: number; code: string; message: string } | null;
  /**
   * When set, `POST /messages/{id}/tutor-check-answer` fails with this envelope
   * (AL-231). The card's reveal is local and must survive it — the persist is
   * fire-after, so a failure changes nothing the learner can see.
   */
  answerError: { status: number; code: string; message: string } | null;
}

const defaultConfig: TutorConfig = {
  replyDeltas: ["Think of ", "a constraint as a promise."],
  check: null,
  failWith: null,
  hang: false,
  preStreamError: null,
  answerError: null,
};

let config: TutorConfig = { ...defaultConfig };

/** path id -> the thread, oldest first. Absent = no conversation row yet. */
const store = new Map<string, ConversationMessage[]>();

/** Every send body the fake received, in order — asserted on directly. */
let sentBodies: SendTutorMessageInput[] = [];

/** Every Tutor-check answer the fake received, in order (AL-231). */
let answerRequests: TutorCheckAnswerRecord[] = [];

/** How many `DELETE /conversation` calls landed (new conversation, PRD §5.8). */
let clearCount = 0;

/** How many `GET /conversation` reads landed — 0 proves a gated-off surface. */
let readCount = 0;

/** How many sends the client hung up on (stop, new conversation, unmount). */
let abortedSendCount = 0;

/** Settle callbacks for streams parked by `hang`, released on demand. */
let heldStreams: Array<() => void> = [];

export function resetTutor(): void {
  store.clear();
  sentBodies = [];
  answerRequests = [];
  clearCount = 0;
  readCount = 0;
  abortedSendCount = 0;
  heldStreams = [];
  config = { ...defaultConfig };
}

export function configureTutor(overrides: Partial<TutorConfig>): void {
  config = { ...config, ...overrides };
}

export function tutorSendBodies(): SendTutorMessageInput[] {
  return sentBodies;
}

/**
 * Every `POST /messages/{id}/tutor-check-answer` the fake received, in order —
 * the request shape the card's fire-after persist is asserted against (AL-231).
 */
export function tutorAnswerRequests(): TutorCheckAnswerRecord[] {
  return answerRequests;
}

export function tutorClearCount(): number {
  return clearCount;
}

export function tutorReadCount(): number {
  return readCount;
}

export function tutorAbortedSendCount(): number {
  return abortedSendCount;
}

/**
 * Let every stream parked by `hang` run to its terminal event. The point of
 * calling this *after* an abort is to prove the client is really disconnected:
 * a stream the client hung up on discards its turn (D2) and delivers nothing, so
 * whatever the rail shows afterwards is not something a late `done` frame wrote.
 */
export function finishTutorStream(): void {
  const held = heldStreams;
  heldStreams = [];
  for (const settle of held) settle();
}

/** One recorded Tutor-check answer: the message it addressed and the choice. */
export interface TutorCheckAnswerRecord {
  message_id: string;
  selected_index: number;
}

export interface SeedMessageInput {
  id?: string;
  role: "learner" | "tutor";
  content: string;
  lesson_id?: string;
  lesson_title?: string;
  tutor_check?: TutorCheck | null;
}

/** Pre-populate a path's thread (a returning learner, PRD §5.8). */
export function seedConversation(pathId: string, messages: SeedMessageInput[]): void {
  store.set(
    pathId,
    messages.map((message, index) => ({
      id: message.id ?? `msg-${index}`,
      role: message.role,
      content: message.content,
      lesson_id: message.lesson_id ?? "seeded-lesson",
      lesson_title: message.lesson_title ?? "Generic constraints",
      tutor_check: message.tutor_check ?? null,
      created_at: "2026-07-30T12:00:00Z",
    })),
  );
}

const encoder = new TextEncoder();

/**
 * One SSE frame on this wire (docs/api.md): a named event and a single-line JSON
 * `data`. Exported so the parser's own tests build frames the same way the fake
 * serves them — two spellings of the wire format is one too many.
 */
export function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function persistTurn(
  pathId: string,
  body: SendTutorMessageInput,
  ids: { learner: string; tutor: string },
): void {
  const thread = store.get(pathId) ?? [];
  const now = new Date().toISOString();
  thread.push(
    {
      id: ids.learner,
      role: "learner",
      content: body.content,
      lesson_id: body.lesson_id,
      lesson_title: "Generic constraints",
      tutor_check: null,
      created_at: now,
    },
    {
      id: ids.tutor,
      role: "tutor",
      content: config.replyDeltas.join(""),
      lesson_id: body.lesson_id,
      lesson_title: "Generic constraints",
      tutor_check: config.check,
      created_at: now,
    },
  );
  store.set(pathId, thread);
}

export const tutorHandlers = [
  http.get(`${API_V1_BASE}/paths/:pathId/conversation`, ({ params }) => {
    readCount += 1;
    // Lazily created: an absent row is an empty thread, never a 404.
    return HttpResponse.json({ messages: store.get(params.pathId as string) ?? [] });
  }),

  http.delete(`${API_V1_BASE}/paths/:pathId/conversation`, ({ params }) => {
    clearCount += 1;
    store.delete(params.pathId as string);
    return new HttpResponse(null, { status: 204 });
  }),

  // `POST /messages/{id}/tutor-check-answer` → 204 (AL-221). Addressed by
  // *message* id, not path id: the answer belongs to the message that posed the
  // check. Re-answering overwrites (last-wins, deliberately unlike the Quick
  // check's first-wins Attempt), and nothing is graded here — the payload the
  // card already holds carries `correct_index`, so this route only records.
  http.post(
    `${API_V1_BASE}/messages/:messageId/tutor-check-answer`,
    async ({ params, request }) => {
      const messageId = params.messageId as string;
      const body = (await request.json()) as { selected_index: number };
      answerRequests.push({ message_id: messageId, selected_index: body.selected_index });

      if (config.answerError) {
        const { status, code, message } = config.answerError;
        return HttpResponse.json({ error: { code, message } }, { status });
      }

      // The route's out-of-range 422 is not faked: the card derives the index
      // by mapping `options`, so an out-of-range request is unreachable from it.
      for (const thread of store.values()) {
        for (const message of thread) {
          if (message.id === messageId && message.tutor_check) {
            message.tutor_check = { ...message.tutor_check, answered_index: body.selected_index };
          }
        }
      }
      return new HttpResponse(null, { status: 204 });
    },
  ),

  http.post(`${API_V1_BASE}/paths/:pathId/conversation/messages`, async ({ params, request }) => {
    const pathId = params.pathId as string;
    const body = (await request.json()) as SendTutorMessageInput;
    sentBodies.push(body);

    if (config.preStreamError) {
      const { status, code, message } = config.preStreamError;
      return HttpResponse.json({ error: { code, message } }, { status });
    }

    const turnIds = {
      learner: `learner-${sentBodies.length}`,
      tutor: `tutor-${sentBodies.length}`,
    };

    // The client hanging up (stop, new conversation, unmount) reaches the fake as
    // a cancel on the response body — and the server's own D2 rule follows from
    // it: a turn nobody is listening to is discarded, never persisted.
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
            // Terminal failure persists nothing — the store is untouched (D2).
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

        if (config.check) controller.enqueue(encoder.encode(frame("tutor_check", config.check)));
        for (const text of config.replyDeltas) {
          controller.enqueue(encoder.encode(frame("delta", { text })));
        }
        // A hung stream is parked deliberately: the ways out are the client
        // aborting (exactly what stop does, TDD §5.6) or `finishTutorStream()`.
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
  }),
];
