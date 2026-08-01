import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../mocks/server";
import { frame } from "../mocks/tutor";
import { API_V1_BASE, ApiError } from "./api";
import { type Proposal, TutorStreamError, streamShapingReply } from "./tutor-stream";

// The shaping half of the SSE reader (AL-330, TDD §5.4): the *same* transport as
// 2A — "Phase 2 §5.4 verbatim, plus one named event" — pointed at the shaping
// conversation endpoint and carrying `proposal`.
//
// These tests exist for the two things that are genuinely new, and nothing else:
// the endpoint + body shape (no `lesson_id`: a shaping turn is about the path,
// not a lesson), and the `proposal` frame. Everything the two clients share —
// chunk reassembly, heartbeat comments, malformed data, drops, stop — is pinned
// once in `tutor-stream.test.ts` and is not respelled here; a second copy of
// those assertions would only drift.

const PATH_ID = "p1000000-0000-4000-8000-000000000001";
const SEND_URL = `${API_V1_BASE}/paths/:pathId/shaping/conversation/messages`;

const encoder = new TextEncoder();

const DONE = frame("done", {
  learner_message_id: "m-learner",
  tutor_message_id: "m-tutor",
});

/** The payload shape TDD §4 fixes: `{operations, summary}`, operations untagged. */
const PROPOSAL: Proposal = {
  summary: "Adds 2 lessons on `unknown` before Utility Types (≈ 10 min).",
  operations: [
    {
      insert_at_position: 3,
      new_unit: null,
      lessons: [{ title: "`unknown` vs `any`" }, { title: "Narrowing `unknown`" }],
      rationale: "You missed the narrowing check, and Utility Types assumes it.",
      estimated_minutes: 10,
    },
  ],
};

/** Serve the shaping send endpoint as `text/event-stream`, one chunk per item. */
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

describe("streamShapingReply — the shaping turn's stream", () => {
  it("[AL-330] POSTs to the shaping conversation endpoint with no `lesson_id`", async () => {
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

    await streamShapingReply({
      pathId: PATH_ID,
      input: {
        content: "What's missing?",
        source: "suggestion",
        model: "anthropic/claude-sonnet-5",
      },
    });

    expect(seen).not.toBeNull();
    const request = seen as unknown as { url: string; body: Record<string, unknown> };
    expect(request.url).toContain(`/api/v1/paths/${PATH_ID}/shaping/conversation/messages`);
    // The shaping thread is not lesson-scoped (PRD §5.8), so the 2A body's
    // `lesson_id` is absent rather than null — the server would 422 an extra key.
    expect(request.body).toEqual({
      content: "What's missing?",
      source: "suggestion",
      model: "anthropic/claude-sonnet-5",
    });
  });

  it("[AL-330] surfaces a `proposal` event's full payload before the reply settles", async () => {
    serveChunks([
      frame("delta", { text: "Here's what I'd add. " }),
      frame("proposal", PROPOSAL),
      DONE,
    ]);

    const deltas: string[] = [];
    const proposals: Proposal[] = [];
    const done = await streamShapingReply({
      pathId: PATH_ID,
      input: { content: "Add practice on narrowing", source: "typed" },
      onDelta: (text) => deltas.push(text),
      onProposal: (proposal) => proposals.push(proposal),
    });

    expect(deltas).toEqual(["Here's what I'd add. "]);
    // The payload is delivered whole — the card renders from the operations, not
    // from the prose (CONTEXT.md: *Proposal* is data, not prose).
    expect(proposals).toEqual([PROPOSAL]);
    expect(done).toEqual({ learner_message_id: "m-learner", tutor_message_id: "m-tutor" });
  });

  it("[AL-330] delivers a `proposal` that lands between deltas, in stream order", async () => {
    serveChunks([
      frame("delta", { text: "One moment — " }),
      frame("proposal", PROPOSAL),
      frame("delta", { text: "that's the shape of it." }),
      DONE,
    ]);

    const order: string[] = [];
    await streamShapingReply({
      pathId: PATH_ID,
      input: { content: "go on", source: "typed" },
      onDelta: () => order.push("delta"),
      onProposal: () => order.push("proposal"),
    });

    expect(order).toEqual(["delta", "proposal", "delta"]);
  });

  it("[AL-330] settles without a proposal when the reply carries none", async () => {
    // The ordinary case, and the **declined edit** case too (TDD §5.5): a reply
    // with no payload is a turn, not a failure — no machine tag distinguishes it.
    serveChunks([frame("delta", { text: "I can't reorder lessons, but I can…" }), DONE]);

    const proposals: Proposal[] = [];
    await streamShapingReply({
      pathId: PATH_ID,
      input: { content: "reorder unit 2", source: "typed" },
      onProposal: (proposal) => proposals.push(proposal),
    });

    expect(proposals).toEqual([]);
  });

  it("[AL-330] fails the stream when a `proposal` payload is not the shape it promises", async () => {
    // The payload is cast, not re-parsed (D4: `agents/shaper.py` validates it
    // before the service emits it). That cast is only honest if a payload that
    // *isn't* a Proposal still fails **here**, as the worded stream failure the
    // rail already knows how to say — rather than reaching the card and throwing
    // out of render, which words nothing and takes the whole route with it.
    serveChunks([frame("proposal", { summary: "Adds two lessons." }), DONE]);

    const proposals: Proposal[] = [];
    await expect(
      streamShapingReply({
        pathId: PATH_ID,
        input: { content: "Add practice on narrowing", source: "typed" },
        onProposal: (proposal) => proposals.push(proposal),
      }),
    ).rejects.toMatchObject({ name: "TutorStreamError", code: "stream_malformed" });
    // And nothing half-formed was handed to the caller on the way out.
    expect(proposals).toEqual([]);
  });

  it("[AL-330] rejects with the `error` event's code and message (terminal failure)", async () => {
    serveChunks([
      frame("delta", { text: "partial" }),
      frame("error", { code: "timeout", message: "That took too long." }),
    ]);

    await expect(
      streamShapingReply({ pathId: PATH_ID, input: { content: "hi", source: "typed" } }),
    ).rejects.toMatchObject({ name: "TutorStreamError", code: "timeout" });
  });

  it("[AL-330] rejects with an ApiError for a pre-stream JSON failure envelope", async () => {
    // The one this surface adds over 2A: a non-`ready` path is a `409` before
    // any streaming starts (TDD §5.5), and it must read as an ordinary API error.
    server.use(
      http.post(SEND_URL, () =>
        HttpResponse.json(
          { error: { code: "path_not_ready", message: "There's no structure to shape yet." } },
          { status: 409 },
        ),
      ),
    );

    const failure = await streamShapingReply({
      pathId: PATH_ID,
      input: { content: "hi", source: "typed" },
    }).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).message).toBe("There's no structure to shape yet.");
    expect(failure).not.toBeInstanceOf(TutorStreamError);
  });
});
