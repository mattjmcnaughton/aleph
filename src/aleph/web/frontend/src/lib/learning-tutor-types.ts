/** LT-1 wire contracts only. No learner surface or generation admission yet. */
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
  output: Record<string, unknown> | null;
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
