import { queryOptions } from "@tanstack/react-query";
import { ApiError, apiFetch, apiV1Path } from "./api";
import type {
  LearningHistoryPage,
  LearningOperationRequest,
  LearningRecommendation,
  LearningTutorOperation,
  LearningTutorState,
} from "./learning-tutor-types";

export const LEARNING_KEY = ["learning-tutor"] as const;
const path = (suffix = "/") => apiV1Path(`/learning-tutor${suffix}`);
export const learningStateOptions = (account: string) =>
  queryOptions({
    queryKey: [...LEARNING_KEY, account, "state"],
    queryFn: ({ signal }) => apiFetch<LearningTutorState>(path(), { signal }),
    staleTime: 0,
    refetchOnWindowFocus: "always" as const,
  });
export const learningDraftOptions = (account: string, id: string) =>
  queryOptions({
    queryKey: [...LEARNING_KEY, account, "draft", id],
    queryFn: ({ signal }) =>
      apiFetch<LearningRecommendation>(path(`/recommendations/${encodeURIComponent(id)}`), {
        signal,
      }),
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: "always" as const,
  });
export const getLearningOperation = (id: string, signal?: AbortSignal) =>
  apiFetch<LearningTutorOperation>(path(`/operations/${encodeURIComponent(id)}`), { signal });
export const getLearningHistory = (before: string, signal?: AbortSignal) =>
  apiFetch<LearningHistoryPage>(path(`/history?before=${encodeURIComponent(before)}`), { signal });
export const startLearningOperation = (body: LearningOperationRequest) =>
  apiFetch<LearningTutorOperation>(path("/operations"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
export const saveLearningFocus = (focus: string, revision: number) =>
  apiFetch<LearningTutorState>(path("/focus"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ focus: focus.trim() || null, revision }),
  });
export const clearLearningConversation = (revision: number) =>
  apiFetch<LearningTutorState>(path(`/conversation?revision=${revision}`), { method: "DELETE" });
export const decideLearningRecommendation = (
  id: string,
  decision: "dismiss" | "restore",
  revision: number,
) =>
  apiFetch<LearningTutorState>(path(`/recommendations/${encodeURIComponent(id)}/decision`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, revision }),
  });

export function learningReason(error: unknown): string | undefined {
  if (!(error instanceof ApiError) || typeof error.details !== "object" || !error.details) return;
  return "reason" in error.details ? String(error.details.reason) : undefined;
}

export function learningError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return "This Tutor or recommendation is no longer available.";
    if (error.status === 410)
      return "The accepted target was deleted. This recommendation cannot create it again.";
    if (error.status === 429)
      return "You've reached the limit. Keep your input and try again later.";
    if (error.status === 409) {
      if (learningReason(error) === "recommendation_capacity")
        return "Three recommendations are already active. Dismiss one before restoring another.";
      if (learningReason(error) === "request_reused")
        return "This request was already used with different details. Recover the original result before trying again.";
      return "Your learning context or recommendation changed. Review the latest state before trying again. Your input is still here.";
    }
  }
  return "We couldn't reach your Tutor. Your input is still here. Try again explicitly.";
}

// Only uncertain operation bodies are retained, in this tab and account. They are
// removed on acknowledgement/clear/sign-out; never stored in a URL or telemetry.
export const recoveryKey = (account: string) => `learning-tutor-request:${account}`;
export function readLearningRequest(account: string): LearningOperationRequest | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(recoveryKey(account)) ?? "null");
    return value &&
      typeof value.request_id === "string" &&
      typeof value.epoch === "number" &&
      typeof value.revision === "number" &&
      (value.kind === "refresh" || (value.kind === "turn" && typeof value.content === "string"))
      ? value
      : null;
  } catch {
    return null;
  }
}

export function clearLearningStorage() {
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith("learning-tutor-")) sessionStorage.removeItem(key);
  }
}

export function recordLearningInteraction(
  account: string,
  id: string,
  interaction: "shown" | "explore" | "discuss",
  surface: "home" | "tutor",
) {
  const key = `learning-tutor-session:${account}`;
  let session = sessionStorage.getItem(key);
  if (!session) {
    session = crypto.randomUUID();
    sessionStorage.setItem(key, session);
  }
  const seen = `learning-tutor-shown:${account}:${surface}:${id}`;
  if (interaction === "shown" && sessionStorage.getItem(seen)) return;
  if (interaction === "shown") sessionStorage.setItem(seen, "1");
  void apiFetch(path(`/recommendations/${encodeURIComponent(id)}/interactions`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interaction, surface, session_id: session }),
  }).catch(() => {});
}
