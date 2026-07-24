// Contract-shaped fakes + a tiny in-memory store for the Paths API (docs/api.md,
// AL-050). This drives all four onboarding resolutions deterministically so the
// AL-061 state-machine tests — and AL-062/063/064, which reuse these handlers —
// can force ready / refused / failed / rate-limited without a live backend.
//
// Convention: the *topic string* selects the resolution via sentinels, so a
// test only has to type a topic. Independently, `configurePaths({...})` tunes
// how many polls a path sits in `generating` before it resolves, and can force
// `POST /paths` to raise the 429 envelope or `DELETE /paths/{id}` to fail.
//
// The same store also serves the switcher (AL-064): `GET /paths` lists it
// newest-first as `PathSummaryDTO` rows, and `DELETE /paths/{id}` hard-deletes
// exactly one entry (recorded in `deletedPathIds()` for assertions).

import { HttpResponse, delay, http } from "msw";
import {
  API_V1_BASE,
  type Level,
  type PathDetail,
  type PathProgress,
  type PathStatus,
  type PathSummary,
  type PathUnit,
} from "../lib/api";

// --- Reusable path-view fixtures (AL-062) -----------------------------------
//
// Single-payload rail fixtures for the three shapes the path view must render:
// a fresh path (nothing started), a mid path (some complete, one available), and
// a complete path (every lesson done). Ids are stable so tests can target a
// specific lesson. The path view derives its `n of m complete` readout straight
// from each payload's `unlock_state`s, so the fixtures stay the single source of
// truth and the `progress` roll-up is computed from the same units.

/** Fresh path: first lesson available (generated), the rest locked. */
export const FRESH_PATH_UNITS: PathUnit[] = [
  {
    id: "u1000000-0000-4000-8000-000000000001",
    title: "Foundations & types",
    lessons: [
      {
        id: "l1000000-0000-4000-8000-000000000001",
        title: "What TypeScript adds",
        position_in_path: 0,
        generation_state: "generated",
        unlock_state: "available",
      },
      {
        id: "l1000000-0000-4000-8000-000000000002",
        title: "Primitive types",
        position_in_path: 1,
        generation_state: "ungenerated",
        unlock_state: "locked",
      },
      {
        id: "l1000000-0000-4000-8000-000000000003",
        title: "Type inference",
        position_in_path: 2,
        generation_state: "ungenerated",
        unlock_state: "locked",
      },
    ],
  },
];

/** Mid path: two lessons complete, the third available, the fourth locked. */
export const MID_PATH_UNITS: PathUnit[] = [
  {
    id: "u2000000-0000-4000-8000-000000000001",
    title: "Foundations & types",
    lessons: [
      {
        id: "l2000000-0000-4000-8000-000000000001",
        title: "What TypeScript adds",
        position_in_path: 0,
        generation_state: "generated",
        unlock_state: "complete",
      },
      {
        id: "l2000000-0000-4000-8000-000000000002",
        title: "Primitive types",
        position_in_path: 1,
        generation_state: "generated",
        unlock_state: "complete",
      },
    ],
  },
  {
    id: "u2000000-0000-4000-8000-000000000002",
    title: "Functions & narrowing",
    lessons: [
      {
        id: "l2000000-0000-4000-8000-000000000003",
        title: "Function types",
        position_in_path: 2,
        generation_state: "generated",
        unlock_state: "available",
      },
      {
        id: "l2000000-0000-4000-8000-000000000004",
        title: "Narrowing",
        position_in_path: 3,
        generation_state: "ungenerated",
        unlock_state: "locked",
      },
    ],
  },
];

/** Complete path: every lesson complete (path-complete treatment, revisitable). */
export const COMPLETE_PATH_UNITS: PathUnit[] = [
  {
    id: "u3000000-0000-4000-8000-000000000001",
    title: "Foundations & types",
    lessons: [
      {
        id: "l3000000-0000-4000-8000-000000000001",
        title: "What TypeScript adds",
        position_in_path: 0,
        generation_state: "generated",
        unlock_state: "complete",
      },
      {
        id: "l3000000-0000-4000-8000-000000000002",
        title: "Primitive types",
        position_in_path: 1,
        generation_state: "generated",
        unlock_state: "complete",
      },
      {
        id: "l3000000-0000-4000-8000-000000000003",
        title: "Type inference",
        position_in_path: 2,
        generation_state: "generated",
        unlock_state: "complete",
      },
    ],
  },
];

/**
 * The `PathProgressDTO` roll-up for a payload's units (docs/api.md): counts over
 * the effective lesson states. Kept in lockstep with `units` so the contract the
 * switcher (AL-064) will read stays honest even though the path view derives its
 * own header count from `unlock_state`.
 */
function progressFor(units: PathUnit[]): PathProgress {
  const lessons = units.flatMap((unit) => unit.lessons);
  return {
    total_lessons: lessons.length,
    generated_lessons: lessons.filter((l) => l.generation_state === "generated").length,
    completed_lessons: lessons.filter((l) => l.unlock_state === "complete").length,
  };
}

/** Zeroed roll-up for a path with no visible outline (non-ready statuses). */
const EMPTY_PROGRESS: PathProgress = {
  total_lessons: 0,
  generated_lessons: 0,
  completed_lessons: 0,
};

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
  /** Outline once `ready` (AL-062 rail fixtures). Defaults to READY_UNITS. */
  units?: PathUnit[];
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
  /** When true, `DELETE /paths/{id}` raises a generic `500` (AL-064/W5). */
  deleteFails: boolean;
  /**
   * Milliseconds `DELETE /paths/{id}` waits before responding. Gives a test a
   * real in-flight window — the only way to observe which row reads "Deleting…".
   */
  deleteDelayMs: number;
}

const defaultConfig: PathsConfig = {
  pollsBeforeResolve: 0,
  rateLimited: false,
  retryRateLimited: false,
  retryFails: false,
  deleteFails: false,
  deleteDelayMs: 0,
};
let config: PathsConfig = { ...defaultConfig };
const store = new Map<string, StoredPath>();
let idCounter = 0;
/** Every id `DELETE /paths/{id}` accepted, in order (AL-064 assertions). */
const deleted: string[] = [];
/** How many times `GET /paths` was served — lets a test see the poll stop. */
let listRequests = 0;

/** Reset store + config between tests (wired into tests/setup.ts). */
export function resetPaths(): void {
  store.clear();
  config = { ...defaultConfig };
  idCounter = 0;
  deleted.length = 0;
  listRequests = 0;
}

/** How many `GET /paths` the fake has served (the switcher's poll count). */
export function pathsListRequestCount(): number {
  return listRequests;
}

/**
 * The ids the fake actually hard-deleted, in call order. Lets a test assert
 * "exactly one DELETE, for exactly this path" off the fake's own record rather
 * than by spying on `fetch` (fakes over mocks).
 */
export function deletedPathIds(): string[] {
  return [...deleted];
}

/**
 * Drop a path from the store the way *another client* would: it vanishes
 * server-side without this client's DELETE ever running, so the next request
 * for it 404s. Deliberately not recorded in `deletedPathIds()` — nobody called
 * `DELETE` — which is what lets a test tell the two apart.
 */
export function forgetPath(id: string): void {
  store.delete(id);
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
  /** Custom outline for a `ready` path (the rail fixtures above). */
  units?: PathUnit[];
}): void {
  store.set(path.id, {
    id: path.id,
    topic: path.topic,
    level: path.level,
    resolution: path.resolution ?? "ready",
    pollsRemaining: path.pollsRemaining ?? 0,
    units: path.units,
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
        // `generated` (not `ungenerated`): a `ready` outline whose available
        // lesson still has no content is a *non-terminal* path-view state
        // (`isPathViewTerminal`), so the poll would never stop with the old
        // default. The steady ready state has the available lesson generated.
        generation_state: "generated",
        unlock_state: "available",
      },
    ],
  },
];

function detailFor(path: StoredPath): PathDetail {
  const generating = path.pollsRemaining > 0;
  const status: PathStatus = generating ? "generating" : path.resolution;
  const units = status === "ready" ? (path.units ?? READY_UNITS) : [];
  return {
    id: path.id,
    topic: path.topic,
    level: path.level,
    status,
    refusal_message: status === "refused" ? REFUSAL_MESSAGE : null,
    // Roll-up derived from the same units the rail renders (docs/api.md).
    progress: status === "ready" ? progressFor(units) : EMPTY_PROGRESS,
    units,
  };
}

/**
 * The switcher row for a stored path (`PathSummaryDTO`, docs/api.md): the same
 * effective status the detail poll reports, plus the progress roll-up over the
 * same units — so list and detail can never disagree in a test.
 */
function summaryFor(path: StoredPath): PathSummary {
  const detail = detailFor(path);
  return {
    id: detail.id,
    topic: detail.topic,
    level: detail.level,
    status: detail.status,
    progress: detail.progress,
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

  // Registered before `/paths/:id` for readability; MSW matches the exact path
  // regardless, so the two never collide.
  http.get(`${API_V1_BASE}/paths`, () => {
    listRequests += 1;
    // Newest first (docs/api.md). The store is insertion-ordered, so the most
    // recently seeded/created path leads.
    const paths = [...store.values()].reverse().map(summaryFor);
    // Keep the generating clock moving under whichever poll is running, so a
    // path seeded with `pollsRemaining` resolves on the switcher too.
    for (const path of store.values()) {
      if (path.pollsRemaining > 0) path.pollsRemaining -= 1;
    }
    return HttpResponse.json({ paths });
  }),

  http.delete(`${API_V1_BASE}/paths/:id`, async ({ params }) => {
    if (config.deleteDelayMs > 0) {
      await delay(config.deleteDelayMs);
    }
    if (config.deleteFails) {
      return serverErrorEnvelope();
    }
    const id = params.id as string;
    if (!store.has(id)) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "Path not found." } },
        { status: 404 },
      );
    }
    // Hard delete, only this path — the learner's others are untouched (W5).
    store.delete(id);
    deleted.push(id);
    return new HttpResponse(null, { status: 204 });
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
