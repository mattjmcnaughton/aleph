// Contract-shaped fakes + a tiny in-memory store for the Paths API (docs/api.md,
// AL-050). This drives all four onboarding resolutions deterministically so the
// AL-061 state-machine tests — and AL-062/063/064, which reuse these handlers —
// can force ready / refused / failed / rate-limited without a live backend.
//
// Convention: the *topic string* selects the resolution via sentinels, so a
// test only has to type a topic. Independently, `configurePaths({...})` tunes
// how many polls a path sits in `generating` before it resolves, and can force
// `POST /paths` to raise the 429 envelope.

import { HttpResponse, http } from "msw";
import {
  API_V1_BASE,
  type Level,
  type PathDetail,
  type PathStatus,
  type PathUnit,
} from "../lib/api";

/** Topic contains this (case-insensitive) → the outline refuses (W7). */
export const REFUSED_TOPIC_SENTINEL = "refuse-me";
/** Topic contains this → the outline fails, retryable (W8). */
export const FAILED_TOPIC_SENTINEL = "fail-me";

/** Resolution the store drives a freshly-created path toward. */
function resolutionForTopic(topic: string): PathStatus {
  const t = topic.toLowerCase();
  if (t.includes(REFUSED_TOPIC_SENTINEL)) return "refused";
  if (t.includes(FAILED_TOPIC_SENTINEL)) return "failed";
  return "ready";
}

const REFUSAL_MESSAGE =
  "Aleph can't build a path on this topic. It sits outside what we can safely teach. Try a different topic.";

interface StoredPath {
  id: string;
  topic: string;
  level: Level;
  /** The terminal status this path resolves to once `pollsRemaining` hits 0. */
  resolution: PathStatus;
  /** While > 0, `GET` reports `generating`; each poll decrements it. */
  pollsRemaining: number;
}

interface PathsConfig {
  /** Polls a new path spends in `generating` before resolving (default 0). */
  pollsBeforeResolve: number;
  /** When true, `POST /paths` raises the daily-cap `429` envelope. */
  rateLimited: boolean;
  /** When true, `POST /paths/{id}/retry` raises the daily-cap `429` (W8/F1). */
  retryRateLimited: boolean;
  /** When true, `POST /paths/{id}/retry` raises a generic `500` (F1). */
  retryFails: boolean;
}

const defaultConfig: PathsConfig = {
  pollsBeforeResolve: 0,
  rateLimited: false,
  retryRateLimited: false,
  retryFails: false,
};
let config: PathsConfig = { ...defaultConfig };
const store = new Map<string, StoredPath>();
let idCounter = 0;

/** Reset store + config between tests (wired into tests/setup.ts). */
export function resetPaths(): void {
  store.clear();
  config = { ...defaultConfig };
  idCounter = 0;
}

/** Tune the fake's polling/rate-limit behaviour for a single test. */
export function configurePaths(overrides: Partial<PathsConfig>): void {
  config = { ...config, ...overrides };
}

/** Directly seed a stored path (AL-062 reuse: a ready path to open). */
export function seedPath(path: {
  id: string;
  topic: string;
  level: Level;
  resolution?: PathStatus;
  pollsRemaining?: number;
}): void {
  store.set(path.id, {
    id: path.id,
    topic: path.topic,
    level: path.level,
    resolution: path.resolution ?? "ready",
    pollsRemaining: path.pollsRemaining ?? 0,
  });
}

/** Minimal but contract-shaped outline for a `ready` path (AL-062 fills this in). */
const READY_UNITS: PathUnit[] = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    title: "Foundations",
    lessons: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        title: "Getting started",
        position_in_path: 0,
        generation_state: "ungenerated",
        unlock_state: "available",
      },
    ],
  },
];

function detailFor(path: StoredPath): PathDetail {
  const generating = path.pollsRemaining > 0;
  const status: PathStatus = generating ? "generating" : path.resolution;
  return {
    id: path.id,
    topic: path.topic,
    level: path.level,
    status,
    refusal_message: status === "refused" ? REFUSAL_MESSAGE : null,
    progress: 0,
    units: status === "ready" ? READY_UNITS : [],
  };
}

function rateLimitEnvelope() {
  return HttpResponse.json(
    {
      error: {
        code: "rate_limited",
        message: "You've reached today's limit for new paths. Try again tomorrow.",
        request_id: "test-request-id",
      },
    },
    { status: 429 },
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

export const pathsHandlers = [
  http.post(`${API_V1_BASE}/paths`, async ({ request }) => {
    if (config.rateLimited) {
      return rateLimitEnvelope();
    }
    const body = (await request.json()) as { topic: string; level: Level };
    idCounter += 1;
    const id = `00000000-0000-4000-8000-${String(idCounter).padStart(12, "0")}`;
    store.set(id, {
      id,
      topic: body.topic,
      level: body.level,
      resolution: resolutionForTopic(body.topic),
      pollsRemaining: config.pollsBeforeResolve,
    });
    return HttpResponse.json({ id }, { status: 202 });
  }),

  http.get(`${API_V1_BASE}/paths/:id`, ({ params }) => {
    const path = store.get(params.id as string);
    if (!path) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "Path not found." } },
        { status: 404 },
      );
    }
    const detail = detailFor(path);
    // A poll advances the generating clock (mirrors the real trigger+poll).
    if (path.pollsRemaining > 0) {
      path.pollsRemaining -= 1;
    }
    return HttpResponse.json(detail);
  }),

  http.post(`${API_V1_BASE}/paths/:id/retry`, ({ params }) => {
    if (config.retryRateLimited) {
      return rateLimitEnvelope();
    }
    if (config.retryFails) {
      return serverErrorEnvelope();
    }
    const path = store.get(params.id as string);
    if (path && path.resolution === "failed") {
      // A retry re-claims the failed outline; this time it succeeds (W8).
      path.resolution = "ready";
      path.pollsRemaining = config.pollsBeforeResolve;
    }
    return HttpResponse.json({ id: params.id }, { status: 202 });
  }),
];
