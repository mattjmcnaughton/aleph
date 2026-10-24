/** Dark-gated Learning Tutor wire contracts; presentation is a later slice. */
export interface LearningEvidence {
  kind: "path" | "beat" | "brief" | "focus" | "message";
  id: string | null;
  completed_lessons: number | null;
  published_briefs: number | null;
  read_briefs: number | null;
  observed_at: string | null;
}

export interface LearningRecommendation {
  id: string;
  kind: "path" | "beat";
  status: "active" | "dismissed" | "superseded" | "accepted";
  topic: string | null;
  level: string | null;
  guidance: string | null;
  description: string | null;
  rationale: string | null;
  evidence: LearningEvidence[] | null;
  created_at: string;
  accepted_at: string | null;
  target_id: string | null;
  target_available: boolean;
}

export interface LearningTutorOperation {
  id: string;
  epoch: number;
  kind: "turn" | "refresh";
  status: "running" | "succeeded" | "failed";
  error_code: string | null;
  input: Record<string, unknown> | null;
  output: LearningOperationOutput | null;
  started_at: string;
  finished_at: string | null;
  cleared: boolean;
}

export interface LearningHistoryPage {
  items: LearningTutorOperation[];
  before: string | null;
}

export interface LearningRecommendationPage {
  items: LearningRecommendation[];
  before: string | null;
}

export interface LearningTutorState {
  focus: string | null;
  revision: number;
  focus_version: number;
  epoch: number;
  recommendations: LearningRecommendation[];
  latest_refresh: LearningTutorOperation | null;
  current_operation_id: string | null;
  history: LearningHistoryPage;
}

export interface LearningFocusRequest {
  focus: string | null;
  revision: number;
}

export interface LearningDecisionRequest {
  decision: "dismiss" | "restore";
  revision: number;
}

export type LearningOperationRequest = {
  request_id: string;
  revision: number;
  epoch: number;
} & (
  | { kind: "turn"; content: string; recommendation_id?: string }
  | { kind: "refresh"; content?: never; recommendation_id?: never }
);

export interface LearningInteractionRequest {
  interaction: "shown" | "explore" | "discuss";
  surface: "home" | "tutor";
  session_id: string;
}

export interface LearningOperationOutput {
  outcome: "needs_context" | "completed";
  omitted: Record<"turns" | "paths" | "beats" | "briefs" | "recommendations", number>;
  reply?: string | null;
  question?: { prompt: string; options: string[] } | null;
  existing_work?: {
    kind: "path" | "beat" | "brief";
    supplied_target_key: string;
  }[];
  recommendation_ids?: string[];
  replacement?: string | null;
  recommendations?: {
    kind: "path" | "beat";
    topic: string;
    level: "new_to_it" | "some_experience" | "work_in_it";
    guidance: string | null;
    description: string;
    rationale: string;
    evidence_keys: string[];
  }[];
}
