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

import { HttpResponse, delay, http } from "msw";
import { API_V1_BASE } from "../lib/api";
import type { Change, ShapingConflictReason, ShapingMessage } from "../lib/shaping";
import { isAddLessons } from "../lib/shaping";
import type { Proposal, SendShapingMessageInput } from "../lib/tutor-stream";
import {
  type AppliedRows,
  addLessonsToPath,
  pathDetailFor,
  removeLessonsFromPath,
  reviseLessonInPath,
} from "./paths";
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
  /**
   * When set, `POST /messages/{id}/apply-proposal` answers the coded `409`
   * (AL-331) instead of applying — the first-class stale path, not an error
   * corner (TDD §5.8).
   */
  applyConflict: ShapingConflictConfig | null;
  /** When set, `POST /changes/{id}/undo` answers the coded `409` instead. */
  undoConflict: ShapingConflictConfig | null;
  /** When true, apply answers a plain `500` — the transaction-failure branch. */
  applyFails: boolean;
  /** When true, `GET /paths/{id}/changes` answers a plain `500`. */
  changesFail: boolean;
  /**
   * Milliseconds apply waits before answering. Gives a test a real in-flight
   * window — the only way to observe the card's `applying` state.
   */
  applyDelayMs: number;
}

const defaultConfig: ShapingConfig = {
  replyDeltas: ["Two short lessons ", "would close that gap."],
  proposal: null,
  failWith: null,
  hang: false,
  preStreamError: null,
  applyConflict: null,
  undoConflict: null,
  applyFails: false,
  changesFail: false,
  applyDelayMs: 0,
};

let config: ShapingConfig = { ...defaultConfig };

/** path id -> the shaping thread, oldest first. Absent = no conversation row. */
const store = new Map<string, ShapingMessage[]>();

/** path id -> its Change history, **newest first** (`GET /changes`, AL-331). */
const changeStore = new Map<string, Change[]>();

/** change id -> the rows its Apply created, so Undo can take exactly them back. */
const appliedRows = new Map<string, AppliedRows>();

/**
 * change id -> the shaping message whose Proposal made it (`path_changes`' FK).
 *
 * The link the *resolution* is derived from (TDD §4): undoing a Change makes
 * exactly that one message read back `undone`, and says nothing about any other
 * applied Proposal on the thread.
 */
const changeMessage = new Map<string, string>();

let applyCallCount = 0;
let undoCallCount = 0;
let changeReadCount = 0;

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
  changeStore.clear();
  appliedRows.clear();
  changeMessage.clear();
  applyCallCount = 0;
  undoCallCount = 0;
  changeReadCount = 0;
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

// --- Apply, Undo & the Change history (AL-321's wire, AL-331's caller) -------
//
// Verified field by field against `dtos/shaping.py` and `routers/v1/shaping.py`.
// Two things this fake insists on, because the card and the sheet are built
// against them:
//
//  1. **Apply really applies.** It mutates the *paths* fake (`mocks/paths.ts`)
//     and answers `{change, path}` where `path` is that store's own
//     `GET /paths/{id}` body — so "ghost rows swap for real rows in one round
//     trip" is proven against the same payload the rail already renders, not
//     against a hand-written echo of it. Undo takes the same rows back.
//  2. **Every refusal is the shared envelope with `details.reason`** — including
//     `request_id`, which the real one always carries. A client that quietly
//     depended on a bare `409` would only find out in production.

/** A `409 conflict` the fake is configured to answer with, on apply or undo. */
export interface ShapingConflictConfig {
  reason: ShapingConflictReason;
  /** The server's own learner-facing wording — the card renders it verbatim. */
  message: string;
}

export function shapingApplyCount(): number {
  return applyCallCount;
}

export function shapingUndoCount(): number {
  return undoCallCount;
}

/** How many `GET /changes` reads landed — 0 proves an unopened sheet is free. */
export function shapingChangeReadCount(): number {
  return changeReadCount;
}

/** A path's Change history as the fake currently holds it. */
export function shapingChanges(pathId: string): Change[] {
  return changeStore.get(pathId) ?? [];
}

/** Pre-populate a path's Change history (a path shaped in an earlier session). */
export function seedChanges(pathId: string, changes: Change[]): void {
  changeStore.set(pathId, [...changes]);
}

function conflictEnvelope(status: number, conflict: ShapingConflictConfig) {
  return HttpResponse.json(
    {
      error: {
        code: "conflict",
        message: conflict.message,
        request_id: "req-shaping-conflict",
        details: { reason: conflict.reason },
      },
    },
    { status },
  );
}

function notFoundEnvelope() {
  return HttpResponse.json(
    { error: { code: "not_found", message: "Not found.", request_id: "req-shaping-404" } },
    { status: 404 },
  );
}

/** Which path holds this message — the ownership walk, in one line of fake. */
function pathOfMessage(messageId: string): { pathId: string; message: ShapingMessage } | null {
  for (const [pathId, thread] of store.entries()) {
    const message = thread.find((candidate) => candidate.id === messageId);
    if (message) return { pathId, message };
  }
  return null;
}

export const shapingHandlers = [
  http.post(`${API_V1_BASE}/messages/:messageId/apply-proposal`, async ({ params }) => {
    applyCallCount += 1;
    if (config.applyDelayMs > 0) await delay(config.applyDelayMs);
    if (config.applyConflict) return conflictEnvelope(409, config.applyConflict);
    if (config.applyFails) {
      return HttpResponse.json(
        {
          error: {
            code: "internal_error",
            message: "Something went wrong.",
            request_id: "req-shaping-500",
          },
        },
        { status: 500 },
      );
    }

    const owned = pathOfMessage(params.messageId as string);
    if (owned === null || owned.message.proposal === null) return notFoundEnvelope();
    const { pathId, message } = owned;
    const proposal = message.proposal;
    if (proposal === null) return notFoundEnvelope();

    // The apply transaction, structurally (§5.6 step 3): rows land, positions
    // shift, a revised lesson goes back to `ungenerated` keeping its slot.
    const rows: AppliedRows = { lessonIds: [], unitId: null };
    const kinds = new Set<Change["kinds"][number]>();
    for (const operation of proposal.operations) {
      if (isAddLessons(operation)) {
        const created = addLessonsToPath(pathId, operation);
        rows.lessonIds.push(...created.lessonIds);
        rows.unitId = created.unitId ?? rows.unitId;
        kinds.add("add_lessons");
      } else {
        reviseLessonInPath(pathId, operation.lesson_id, operation.new_title);
        kinds.add("revise_lesson");
      }
    }

    const change: Change = {
      id: `c0000000-0000-4000-8000-${String(applyCallCount).padStart(12, "0")}`,
      summary: proposal.summary,
      kinds: [...kinds],
      status: "applied",
      applied_at: new Date().toISOString(),
      undone_at: null,
    };
    appliedRows.set(change.id, rows);
    changeMessage.set(change.id, message.id);
    changeStore.set(pathId, [change, ...(changeStore.get(pathId) ?? [])]);
    // Resolution is derived server-side (TDD §4) — a live change row references
    // this message now, so the next thread read reports `applied`.
    message.proposal = { ...proposal, resolution: "applied" };

    const path = pathDetailFor(pathId);
    if (path === undefined) return notFoundEnvelope();
    return HttpResponse.json({ change, path });
  }),

  http.post(`${API_V1_BASE}/changes/:changeId/undo`, ({ params }) => {
    undoCallCount += 1;
    if (config.undoConflict) return conflictEnvelope(409, config.undoConflict);

    const changeId = params.changeId as string;
    for (const [pathId, changes] of changeStore.entries()) {
      const index = changes.findIndex((candidate) => candidate.id === changeId);
      if (index === -1) continue;
      const rows = appliedRows.get(changeId);
      if (rows) removeLessonsFromPath(pathId, rows);
      changes[index] = {
        ...changes[index],
        status: "undone",
        undone_at: new Date().toISOString(),
      };
      // Undo is a status, never a delete — and the proposal that made *this*
      // Change reads back as `undone` on the next thread read. Only that one:
      // resolution follows the `path_changes` row's own message FK, so another
      // applied Proposal on the same thread is untouched.
      const messageId = changeMessage.get(changeId);
      for (const message of store.get(pathId) ?? []) {
        if (message.id === messageId && message.proposal !== null) {
          message.proposal = { ...message.proposal, resolution: "undone" };
        }
      }
      return new HttpResponse(null, { status: 204 });
    }
    return notFoundEnvelope();
  }),

  http.get(`${API_V1_BASE}/paths/:pathId/changes`, ({ params }) => {
    changeReadCount += 1;
    if (config.changesFail) {
      return HttpResponse.json(
        {
          error: {
            code: "internal_error",
            message: "Something went wrong.",
            request_id: "req-shaping-500",
          },
        },
        { status: 500 },
      );
    }
    return HttpResponse.json({ changes: changeStore.get(params.pathId as string) ?? [] });
  }),

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
