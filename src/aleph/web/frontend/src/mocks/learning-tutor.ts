import { HttpResponse, http } from "msw";
import { API_V1_BASE } from "../lib/api";
import type {
  LearningOperationRequest,
  LearningRecommendation,
  LearningTutorOperation,
  LearningTutorState,
} from "../lib/learning-tutor-types";

export const emptyLearningState = (): LearningTutorState => ({
  focus: null,
  revision: 0,
  epoch: 0,
  focus_version: 0,
  recommendations: [],
  latest_refresh: null,
  current_operation_id: null,
  history: { items: [], before: null },
});
export function learningCard(kind: "path" | "beat" = "path"): LearningRecommendation {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    kind,
    status: "active",
    topic: "Electricity foundations",
    level: "some_experience",
    guidance: "Start with markets",
    description: "Build the foundations.",
    rationale: "You asked about energy.",
    evidence: [
      {
        kind: "message",
        id: null,
        completed_lessons: null,
        published_briefs: null,
        read_briefs: null,
        observed_at: null,
      },
    ],
    created_at: "2026-10-23T12:00:00Z",
    accepted_at: null,
    target_id: null,
    target_available: false,
  };
}
export function learningReceipt(
  request: LearningOperationRequest,
  status: "succeeded" | "failed" = "succeeded",
): LearningTutorOperation {
  return {
    id: request.request_id,
    epoch: request.epoch,
    kind: request.kind,
    status,
    input: { ...request },
    output:
      status === "failed"
        ? null
        : {
            outcome: "completed",
            reply: "A **whole** reply, not a fragment.",
            recommendation_ids: [],
            omitted: { paths: 0, beats: 0, briefs: 0, turns: 0, recommendations: 0 },
          },
    error_code: status === "failed" ? "provider_failed" : null,
    started_at: "2026-10-23T12:00:00Z",
    finished_at: "2026-10-23T12:00:01Z",
    cleared: false,
  };
}

/** Per-test fake owns state and request receipts, never a singleton shared across tests. */
export function learningFake(initial = emptyLearningState()) {
  const state = initial;
  const requests: LearningOperationRequest[] = [];
  const receipts = new Map<string, LearningTutorOperation>();
  const dismissed: LearningRecommendation[] = [];
  const base = `${API_V1_BASE}/learning-tutor`;
  const handlers = [
    http.get(`${base}/`, () => HttpResponse.json(state)),
    http.get(`${base}/recommendations`, () =>
      HttpResponse.json({ items: dismissed, before: null }),
    ),
    http.get(`${base}/operations/:id`, ({ params }) =>
      HttpResponse.json(receipts.get(String(params.id))),
    ),
    http.post(
      `${base}/recommendations/:id/interactions`,
      () => new HttpResponse(null, { status: 204 }),
    ),
    http.put(`${base}/recommendations/:id/decision`, async ({ params, request }) => {
      const body = (await request.json()) as { decision: string };
      const from = body.decision === "dismiss" ? state.recommendations : dismissed;
      const to = body.decision === "dismiss" ? dismissed : state.recommendations;
      const index = from.findIndex((card) => card.id === params.id);
      if (index < 0) return new HttpResponse(null, { status: 409 });
      const card = from.splice(index, 1)[0];
      to.push({ ...card, status: body.decision === "dismiss" ? "dismissed" : "active" });
      state.revision++;
      return HttpResponse.json(state);
    }),
    http.post(`${base}/operations`, async ({ request }) => {
      const body = (await request.json()) as LearningOperationRequest;
      requests.push(body);
      const receipt = receipts.get(body.request_id) ?? learningReceipt(body);
      if (!receipts.has(body.request_id)) {
        receipts.set(body.request_id, receipt);
        state.revision++;
        if (body.kind === "turn") state.history.items.unshift(receipt);
        else state.latest_refresh = receipt;
      }
      return HttpResponse.json(receipt);
    }),
  ];
  return { state, requests, receipts, dismissed, handlers };
}
