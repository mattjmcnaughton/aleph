// Contract-shaped fakes + a tiny in-memory store for the Lessons API (docs/api.md,
// AL-051). Mirrors mocks/paths.ts: an in-memory map, a `seedLesson` helper, a
// `configureLessons({...})` knob for polling/rate-limit, and a `resetLessons`
// wired into tests/setup.ts. Drives the lesson view (AL-063) deterministically —
// ready / generating / failed / locked, plus the Attempt + complete state changes
// — without a live backend.
//
// Answer-hiding (W6, docs/api.md §6): the `GET` payload's `quick_check` carries
// ONLY `stem` + `options`; `correct_index`/`explanation` never appear until an
// Attempt is recorded (they live inside `attempt`). The fake enforces this by
// keeping the key separate from what `detailFor` serializes pre-Attempt.

import { HttpResponse, http } from "msw";
import {
  API_V1_BASE,
  type LessonAttempt,
  type LessonDetail,
  type LessonGenerationState,
  type LessonUnlockState,
} from "../lib/api";

/** Generic, learner-safe failure message (never raw provider text, §6). */
const GENERIC_GENERATION_ERROR = "We couldn't generate this lesson.";

interface StoredLesson {
  id: string;
  path_id: string;
  title: string;
  position_in_path: number;
  position_in_unit: number;
  unlock_state: LessonUnlockState;
  /** Terminal generation state once `pollsRemaining` hits 0. */
  resolution: LessonGenerationState;
  /** While > 0, `GET` reports `generating`; each poll decrements it. */
  pollsRemaining: number;
  /** Quick check content (served stem+options only pre-Attempt). */
  stem: string;
  options: string[];
  /** The keyed answer — NEVER serialized until an Attempt exists (W6). */
  correctIndex: number;
  explanation: string;
  readPassage: string;
  /** First-wins recorded Attempt, or null until the learner attempts. */
  attempt: LessonAttempt | null;
  /** Overrides the generic failure copy when generation resolves to `failed`. */
  generationError?: string;
}

interface LessonsConfig {
  /** Polls a lesson spends in `generating` after a retry (default 0). */
  pollsBeforeResolve: number;
  /** When true, `POST /lessons/{id}/generate` raises the daily-cap `429`. */
  generateRateLimited: boolean;
  /** When true, `POST /lessons/{id}/attempt` raises a generic `500` (C2). */
  attemptFails: boolean;
  /** When true, `POST /lessons/{id}/complete` raises a generic `500` (C2). */
  completeFails: boolean;
}

const defaultConfig: LessonsConfig = {
  pollsBeforeResolve: 0,
  generateRateLimited: false,
  attemptFails: false,
  completeFails: false,
};
let config: LessonsConfig = { ...defaultConfig };
const store = new Map<string, StoredLesson>();

/** Reset store + config between tests (wired into tests/setup.ts). */
export function resetLessons(): void {
  store.clear();
  config = { ...defaultConfig };
}

/** Tune the fake's polling/rate-limit behaviour for a single test. */
export function configureLessons(overrides: Partial<LessonsConfig>): void {
  config = { ...config, ...overrides };
}

export interface SeedLessonInput {
  id: string;
  path_id: string;
  title?: string;
  position_in_path?: number;
  position_in_unit?: number;
  unlock_state?: LessonUnlockState;
  /** Terminal generation state (default `generated`). Use `failed` for W8. */
  resolution?: LessonGenerationState;
  /** Polls to sit in `generating` before resolving (default 0). */
  pollsRemaining?: number;
  stem?: string;
  options?: string[];
  correctIndex?: number;
  explanation?: string;
  readPassage?: string;
  /**
   * Pre-record an Attempt so the reveal renders on first load (revealed-on-return).
   * Pass the selected index; the outcome/correct/explanation are derived.
   */
  attemptSelectedIndex?: number;
  generationError?: string;
}

const DEFAULT_OPTIONS = ["A type-checked superset of JS", "A new runtime", "A CSS framework"];

/** Directly seed a stored lesson (AL-063 fixtures). */
export function seedLesson(input: SeedLessonInput): void {
  const options = input.options ?? DEFAULT_OPTIONS;
  const correctIndex = input.correctIndex ?? 0;
  const explanation = input.explanation ?? "TypeScript layers static types over JavaScript.";
  const stored: StoredLesson = {
    id: input.id,
    path_id: input.path_id,
    title: input.title ?? "What TypeScript adds",
    position_in_path: input.position_in_path ?? 0,
    position_in_unit: input.position_in_unit ?? 0,
    unlock_state: input.unlock_state ?? "available",
    resolution: input.resolution ?? "generated",
    pollsRemaining: input.pollsRemaining ?? 0,
    stem: input.stem ?? "What does TypeScript add to JavaScript?",
    options,
    correctIndex,
    explanation,
    readPassage: input.readPassage ?? "TypeScript is JavaScript with syntax for types.",
    attempt: null,
    generationError: input.generationError,
  };
  if (input.attemptSelectedIndex !== undefined) {
    stored.attempt = gradeAttempt(stored, input.attemptSelectedIndex);
  }
  store.set(input.id, stored);
}

/** Deterministic grading (mirrors domains/grading): index match → correct. */
function gradeAttempt(lesson: StoredLesson, selectedIndex: number): LessonAttempt {
  return {
    selected_index: selectedIndex,
    outcome: selectedIndex === lesson.correctIndex ? "correct" : "incorrect",
    correct_index: lesson.correctIndex,
    explanation: lesson.explanation,
  };
}

function detailFor(lesson: StoredLesson): LessonDetail {
  const generating = lesson.pollsRemaining > 0;
  const generationState: LessonGenerationState = generating ? "generating" : lesson.resolution;
  const generated = generationState === "generated";
  return {
    id: lesson.id,
    path_id: lesson.path_id,
    title: lesson.title,
    position_in_path: lesson.position_in_path,
    position_in_unit: lesson.position_in_unit,
    generation_state: generationState,
    unlock_state: lesson.unlock_state,
    read_passage: generated ? lesson.readPassage : null,
    // Answer-hiding (W6): stem + options only, never the keyed answer.
    quick_check: generated ? { stem: lesson.stem, options: lesson.options } : null,
    // The reveal appears only once an Attempt exists (revealed-on-return).
    attempt: lesson.attempt,
    generation_error:
      generationState === "failed" ? (lesson.generationError ?? GENERIC_GENERATION_ERROR) : null,
  };
}

function notFoundEnvelope() {
  return HttpResponse.json(
    { error: { code: "not_found", message: "Lesson not found." } },
    { status: 404 },
  );
}

function serverErrorEnvelope() {
  return HttpResponse.json(
    {
      error: {
        code: "internal_error",
        message: "Something went wrong.",
        request_id: "test-request-id",
      },
    },
    { status: 500 },
  );
}

function rateLimitEnvelope() {
  return HttpResponse.json(
    {
      error: {
        code: "rate_limited",
        message: "You've reached today's limit for lesson generation. Try again tomorrow.",
        request_id: "test-request-id",
      },
    },
    { status: 429 },
  );
}

export const lessonsHandlers = [
  http.get(`${API_V1_BASE}/lessons/:id`, ({ params }) => {
    const lesson = store.get(params.id as string);
    if (!lesson) {
      return notFoundEnvelope();
    }
    const detail = detailFor(lesson);
    // A poll advances the generating clock (mirrors the real trigger+poll).
    if (lesson.pollsRemaining > 0) {
      lesson.pollsRemaining -= 1;
    }
    return HttpResponse.json(detail);
  }),

  http.post(`${API_V1_BASE}/lessons/:id/generate`, ({ params }) => {
    if (config.generateRateLimited) {
      return rateLimitEnvelope();
    }
    const lesson = store.get(params.id as string);
    if (!lesson) {
      return notFoundEnvelope();
    }
    // A retry re-claims a failed lesson; this time it resolves to `generated`.
    if (lesson.resolution === "failed") {
      lesson.resolution = "generated";
      lesson.pollsRemaining = config.pollsBeforeResolve;
    }
    return HttpResponse.json({ id: lesson.id }, { status: 202 });
  }),

  http.post(`${API_V1_BASE}/lessons/:id/attempt`, async ({ params, request }) => {
    if (config.attemptFails) {
      return serverErrorEnvelope();
    }
    const lesson = store.get(params.id as string);
    if (!lesson) {
      return notFoundEnvelope();
    }
    // Locked → 403; a complete lesson stays attemptable (§6).
    if (lesson.unlock_state === "locked") {
      return HttpResponse.json(
        { error: { code: "forbidden", message: "This lesson is locked." } },
        { status: 403 },
      );
    }
    // No Quick check yet (still generating / not generated) → 409.
    if (lesson.resolution !== "generated" || lesson.pollsRemaining > 0) {
      return HttpResponse.json(
        { error: { code: "conflict", message: "This lesson has no Quick check yet." } },
        { status: 409 },
      );
    }
    const body = (await request.json()) as { selected_index: number };
    // First-wins: a re-submit returns the first Attempt, never overwrites it.
    if (lesson.attempt === null) {
      lesson.attempt = gradeAttempt(lesson, body.selected_index);
    }
    return HttpResponse.json(lesson.attempt);
  }),

  http.post(`${API_V1_BASE}/lessons/:id/complete`, ({ params }) => {
    if (config.completeFails) {
      return serverErrorEnvelope();
    }
    const lesson = store.get(params.id as string);
    if (!lesson) {
      return notFoundEnvelope();
    }
    // Locked → 403; complete is idempotent (§6).
    if (lesson.unlock_state === "locked") {
      return HttpResponse.json(
        { error: { code: "forbidden", message: "This lesson is locked." } },
        { status: 403 },
      );
    }
    lesson.unlock_state = "complete";
    return HttpResponse.json({ id: lesson.id, unlock_state: "complete" });
  }),
];
