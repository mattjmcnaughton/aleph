import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../mocks/server";
import { frame } from "../mocks/tutor";
import { API_V1_BASE, ApiError } from "./api";
import { TutorStreamError, streamTutorReply } from "./tutor-stream";

// The SSE reader (AL-230, TDD §5.4 / docs/api.md "The send endpoint's event
// stream"). Everything here drives the real `fetch` + `ReadableStream` path
// through MSW streamed bodies — no parser internals are poked at, because the
// bugs worth catching (a frame split across two network chunks, a heartbeat
// comment mistaken for data) only exist at that seam.

const PATH_ID = "p1000000-0000-4000-8000-000000000001";
const LESSON_ID = "l1000000-0000-4000-8000-000000000001";
const SEND_URL = `${API_V1_BASE}/paths/:pathId/conversation/messages`;

const encoder = new TextEncoder();

/** Serve the send endpoint as `text/event-stream`, one chunk per array item. */
function serveChunks(chunks: string[]): void {
  server.use(
    http.post(SEND_URL, () => {
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
          controller.close();
        },
      });
      return new HttpResponse(stream, {
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
      });
    }),
  );
}

// `frame` is imported from the fake rather than respelled here: the parser and
// the thing it parses must agree on the wire format by construction.
const DONE = frame("done", {
  learner_message_id: "m-learner",
  tutor_message_id: "m-tutor",
});

interface Collected {
  deltas: string[];
  checks: unknown[];
  /** Set by the stop test to wait on the stream rather than on a timer. */
  onFirstDelta?: () => void;
}

function send(collected: Collected, signal?: AbortSignal) {
  return streamTutorReply({
    pathId: PATH_ID,
    input: { lesson_id: LESSON_ID, content: "Explain this simpler", source: "typed" },
    signal,
    onDelta: (text) => collected.deltas.push(text),
    onTutorCheck: (check) => collected.checks.push(check),
  });
}

function collector(): Collected {
  return { deltas: [], checks: [] };
}

describe("streamTutorReply — SSE over a streamed POST", () => {
  it("POSTs the turn to the conversation endpoint as JSON", async () => {
    let seen: { url: string; body: unknown } | null = null;
    server.use(
      http.post(SEND_URL, async ({ request }) => {
        seen = { url: request.url, body: await request.json() };
        return new HttpResponse(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode(DONE));
              controller.close();
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        );
      }),
    );

    await streamTutorReply({
      pathId: PATH_ID,
      input: {
        lesson_id: LESSON_ID,
        content: "Go deeper",
        source: "suggestion",
        model: "anthropic/claude-haiku-4-5",
      },
    });

    expect(seen).not.toBeNull();
    const request = seen as unknown as { url: string; body: Record<string, unknown> };
    expect(request.url).toContain(`/api/v1/paths/${PATH_ID}/conversation/messages`);
    expect(request.body).toEqual({
      lesson_id: LESSON_ID,
      content: "Go deeper",
      source: "suggestion",
      model: "anthropic/claude-haiku-4-5",
    });
  });

  it("delivers named `delta` events in order and resolves with the `done` ids", async () => {
    serveChunks([frame("delta", { text: "Think of " }), frame("delta", { text: "<T>" }), DONE]);
    const collected = collector();

    const done = await send(collected);

    expect(collected.deltas).toEqual(["Think of ", "<T>"]);
    expect(done).toEqual({ learner_message_id: "m-learner", tutor_message_id: "m-tutor" });
  });

  it("reassembles an event split across network chunks", async () => {
    // The split lands mid-JSON *and* mid-frame — the two places a naive
    // per-chunk parser loses data.
    serveChunks([
      'event: delta\ndata: {"te',
      'xt":"half"}\n',
      "\nevent: delt",
      `a\ndata: {"text":"and half"}\n\n${DONE}`,
    ]);
    const collected = collector();

    await send(collected);

    expect(collected.deltas.join("")).toBe("halfand half");
  });

  it("ignores `: ping` heartbeat comments", async () => {
    serveChunks([": ping\n\n", frame("delta", { text: "answer" }), ": ping\n\n", DONE]);
    const collected = collector();

    const done = await send(collected);

    expect(collected.deltas).toEqual(["answer"]);
    expect(done.tutor_message_id).toBe("m-tutor");
  });

  it("ignores an unknown named event, so the wire can grow additively", async () => {
    // Forward compatibility, stated as a test: a client that treated an
    // unrecognised event as fatal would make every new server event a breaking
    // change. The stream must run past it and settle normally.
    serveChunks([
      frame("tutor_mood", { mood: "encouraging" }),
      frame("delta", { text: "answer" }),
      DONE,
    ]);
    const collected = collector();

    const done = await send(collected);

    expect(collected.deltas).toEqual(["answer"]);
    expect(done.tutor_message_id).toBe("m-tutor");
  });

  it("rejects a malformed `data` line as a stream failure, not a transport one", async () => {
    // The distinction the rail's copy hangs on: a `SyntaxError` escaping the
    // parser would reach `failureCopy` as an unknown error and be worded "check
    // your connection", which by this point is never the truth.
    serveChunks([frame("delta", { text: "half" }), "event: delta\ndata: {not json\n\n", DONE]);
    const collected = collector();

    const failure = await send(collected).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(TutorStreamError);
    expect((failure as TutorStreamError).code).toBe("stream_malformed");
    expect(collected.deltas).toEqual(["half"]);
  });

  it("surfaces a `tutor_check` event's payload before the reply settles", async () => {
    const check = {
      stem: "What does K extends keyof T guarantee?",
      options: ["That T has a key", "That K is one of T's key names", "That K is a string"],
      correct_index: 1,
      explanation: "That is what lets the return type be T[K].",
      answered_index: null,
    };
    serveChunks([frame("tutor_check", check), frame("delta", { text: "One for you" }), DONE]);
    const collected = collector();

    await send(collected);

    expect(collected.checks).toEqual([check]);
  });

  it("rejects with the `error` event's code and message (terminal failure)", async () => {
    serveChunks([
      frame("delta", { text: "partial" }),
      frame("error", { code: "upstream_error", message: "The tutor didn't answer." }),
    ]);
    const collected = collector();

    const failure = await send(collected).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(TutorStreamError);
    expect((failure as TutorStreamError).code).toBe("upstream_error");
    expect((failure as TutorStreamError).message).toBe("The tutor didn't answer.");
    // The partial text still reached the caller; discarding it is the caller's
    // job (a turn exists whole or not at all, TDD D2).
    expect(collected.deltas).toEqual(["partial"]);
  });

  it("rejects when the transport drops before any terminal event", async () => {
    serveChunks([frame("delta", { text: "half a sen" })]);

    const failure = await send(collector()).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(TutorStreamError);
    expect((failure as TutorStreamError).code).toBe("stream_dropped");
  });

  it("rejects with an ApiError for a pre-stream JSON failure envelope", async () => {
    server.use(
      http.post(SEND_URL, () =>
        HttpResponse.json(
          { error: { code: "conflict", message: "A reply is already in flight." } },
          { status: 409 },
        ),
      ),
    );

    const failure = await send(collector()).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(409);
    expect((failure as ApiError).code).toBe("conflict");
  });

  it("aborts the request when the caller's signal fires (stop)", async () => {
    const controller = new AbortController();
    server.use(
      http.post(SEND_URL, () => {
        const stream = new ReadableStream<Uint8Array>({
          start(streamController) {
            streamController.enqueue(encoder.encode(frame("delta", { text: "starting" })));
            // Never closed: only the abort can end this stream.
          },
        });
        return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
      }),
    );
    const collected = collector();
    // Wait on the stream itself, not on the clock: the first delta is proof the
    // read loop is running, which is the state the abort has to land in. A timer
    // would only be *probably* long enough, and slowly.
    const streaming = new Promise<void>((resolve) => {
      collected.onFirstDelta = resolve;
    });

    const pending = streamTutorReply({
      pathId: PATH_ID,
      input: { lesson_id: LESSON_ID, content: "Explain this simpler", source: "typed" },
      signal: controller.signal,
      onDelta: (text) => {
        collected.deltas.push(text);
        collected.onFirstDelta?.();
      },
    }).catch((error: unknown) => error);
    await streaming;
    controller.abort();
    const failure = await pending;

    expect((failure as Error).name).toBe("AbortError");
    expect(collected.deltas).toEqual(["starting"]);
  });
});
