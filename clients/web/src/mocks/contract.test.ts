import { HttpResponse } from "msw";
import { describe, expect, expectTypeOf, it } from "vitest";
import type { Schemas } from "../lib/api";
import {
  type ContractResponse,
  contract,
  errorEnvelope,
  noContent,
  sseFrame,
  toMswPath,
} from "./contract";

// The MSW handlers' responses are typed from the generated schema (iOS TDD §4.3),
// so a mock cannot drift from the contract. These are compile-time assertions as
// much as runtime ones: `just typecheck-fe` runs over this file, and each
// `@ts-expect-error` below fails the typecheck if the line it guards ever
// compiles, i.e. if the typing stopped catching that drift.

describe("contract-typed MSW handlers", () => {
  it("type a handler's response as the schema's body for that operation", () => {
    expectTypeOf<ContractResponse<"/api/v1/paths/{path_id}", "get">>().toEqualTypeOf<
      Schemas["PathDetailResponse"] | Schemas["ErrorEnvelope"]
    >();
    expectTypeOf<ContractResponse<"/api/v1/paths/{path_id}", "delete">>().toEqualTypeOf<
      Schemas["ErrorEnvelope"] | null
    >();
  });

  it("reject a response body the contract does not declare", () => {
    contract.get("/api/v1/paths", () =>
      // @ts-expect-error -- `paths` must be `PathSummaryDTO[]`, not a string.
      HttpResponse.json({ paths: "not a list" }),
    );
    contract.get("/api/v1/progress/summary", () =>
      // @ts-expect-error -- an error body must be the shared envelope.
      HttpResponse.json({ error: { code: "not_found" } }, { status: 404 }),
    );
    // @ts-expect-error -- `/api/v1/nonexistent` is not a contract route.
    contract.get("/api/v1/nonexistent", () => noContent());
  });

  it("register the contract path in MSW's `:param` form", () => {
    expect(toMswPath("/api/v1/paths/{path_id}/conversation")).toBe(
      "/api/v1/paths/:path_id/conversation",
    );
    const handler = contract.get("/api/v1/briefs/{brief_id}", () =>
      errorEnvelope(404, "not_found", "Brief not found."),
    );
    expect(handler.info.path).toBe("/api/v1/briefs/:brief_id");
    expect(handler.info.method).toBe("GET");
  });

  it("type each SSE frame's data by its event name", () => {
    expect(sseFrame("delta", { text: "hi" })).toBe('event: delta\ndata: {"text":"hi"}\n\n');
    // @ts-expect-error -- a `done` frame carries both message ids.
    sseFrame("done", { learner_message_id: "l1" });
  });

  it("render the error envelope with a request id, as the backend does", async () => {
    const body = await errorEnvelope(409, "conflict", "Busy.", {
      details: { reason: "x" },
    }).json();
    expect(body).toEqual({
      error: {
        code: "conflict",
        message: "Busy.",
        request_id: "test-request-id",
        details: { reason: "x" },
      },
    });
  });
});
