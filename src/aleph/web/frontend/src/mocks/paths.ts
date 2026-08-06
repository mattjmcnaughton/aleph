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
import { PATH_TITLE_MAX_LENGTH, TOPIC_MAX_LENGTH } from "../lib/onboarding";
import { ADMIN_MODEL_ALLOWLIST } from "./models";

// --- Reusable path-view fixtures (AL-062) -----------------------------------
//
// Single-payload rail fixtures for the three shapes the path view must render:
// a fresh path (nothing started), a mid path (some complete, one available), and
// a complete path (every lesson done). Ids are stable so tests can target a
// specific lesson. The path view derives its `n of m complete` readout straight
// from each payload's `unlock_state`s, so the fixtures stay the single source of
// truth and the `progress` roll-up is computed from the same units.
//
// `position_in_path` is **1-based**, because that is what the backend writes:
// `services/generation.py` increments the counter *before* each insert, so a
// path's first lesson is position 1 (`docs/api.md`'s payload agrees, and
// `domains/engagement.py` puts an empty path's first insertable slot at 1). A
// 0-based fixture is not a harmless relabelling — it silently licenses a `+ 1`
// in a component that then reads one lesson ahead against the real API.

/** Fresh path: first lesson available (generated), the rest locked. */
export const FRESH_PATH_UNITS: PathUnit[] = [
  {
    id: "u1000000-0000-4000-8000-000000000001",
    title: "Foundations & types",
    lessons: [
      {
        id: "l1000000-0000-4000-8000-000000000001",
        title: "What TypeScript adds",
        position_in_path: 1,
        generation_state: "generated",
        unlock_state: "available",
      },
      {
        id: "l1000000-0000-4000-8000-000000000002",
        title: "Primitive types",
        position_in_path: 2,
        generation_state: "ungenerated",
        unlock_state: "locked",
      },
      {
        id: "l1000000-0000-4000-8000-000000000003",
        title: "Type inference",
        position_in_path: 3,
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
        position_in_path: 1,
        generation_state: "generated",
        unlock_state: "complete",
      },
      {
        id: "l2000000-0000-4000-8000-000000000002",
        title: "Primitive types",
        position_in_path: 2,
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
        position_in_path: 3,
        generation_state: "generated",
        unlock_state: "available",
      },
      {
        id: "l2000000-0000-4000-8000-000000000004",
        title: "Narrowing",
        position_in_path: 4,
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
        position_in_path: 1,
        generation_state: "generated",
        unlock_state: "complete",
      },
      {
        id: "l3000000-0000-4000-8000-000000000002",
        title: "Primitive types",
        position_in_path: 2,
        generation_state: "generated",
        unlock_state: "complete",
      },
      {
        id: "l3000000-0000-4000-8000-000000000003",
        title: "Type inference",
        position_in_path: 3,
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
  /**
   * The learner-editable display label. `undefined` until a rename PATCH lands,
   * so `detailFor`/`summaryFor` can apply the identical topic fallback the real
   * server applies — a fixture that never renames a path must not have to spell
   * out `title: topic` itself.
   */
  title?: string;
  /** Free-text creation input (docs/api.md), null when none was given. */
  guidance: string | null;
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
  /** When true, `PATCH /paths/{id}` (rename) raises a generic `500`. */
  renameFails: boolean;
  /**
   * Milliseconds `DELETE /paths/{id}` waits before responding. Gives a test a
   * real in-flight window — the only way to observe which row reads "Deleting…".
   */
  deleteDelayMs: number;
  /**
   * The server's `MODEL_ALLOWLIST` as `POST /paths` sees it (AL-065, §5.3/D14).
   * A `model_outline`/`model_lesson` outside it is `422 validation_error`
   * (docs/api.md) — set this to a narrower list (or `[]`) to fake the allowlist
   * changing after the session that populated the picker was issued.
   */
  modelAllowlist: string[];
}

const defaultConfig: PathsConfig = {
  pollsBeforeResolve: 0,
  rateLimited: false,
  retryRateLimited: false,
  retryFails: false,
  deleteFails: false,
  renameFails: false,
  deleteDelayMs: 0,
  modelAllowlist: [...ADMIN_MODEL_ALLOWLIST],
};
let config: PathsConfig = { ...defaultConfig };
const store = new Map<string, StoredPath>();
let idCounter = 0;
/** Every id `DELETE /paths/{id}` accepted, in order (AL-064 assertions). */
const deleted: string[] = [];
/**
 * Every body `POST /paths` received, in call order and exactly as it arrived on
 * the wire (AL-065). Kept as raw JSON rather than a typed `CreatePathInput` so a
 * test can assert a key is **absent** — "no model fields" and "model fields sent
 * as null" are different payloads, and only the raw object can tell them apart.
 */
const createBodies: Array<Record<string, unknown>> = [];
/** How many times `GET /paths` was served — lets a test see the poll stop. */
let listRequests = 0;
/** How many times `PATCH /paths/{id}` (rename) was served — did Escape/Cancel
 *  really send nothing, as opposed to a request that merely resolved fast? */
let renameRequests = 0;

/** Reset store + config between tests (wired into tests/setup.ts). */
export function resetPaths(): void {
  store.clear();
  config = { ...defaultConfig };
  idCounter = 0;
  appliedCounter = 0;
  deleted.length = 0;
  createBodies.length = 0;
  listRequests = 0;
  renameRequests = 0;
}

/**
 * The raw bodies `POST /paths` was called with, in order — including the ones
 * the fake then rejected, so a test can assert what the picker *sent* even when
 * the server refused it (the 422 off-allowlist case).
 */
export function createPathBodies(): Array<Record<string, unknown>> {
  return createBodies.map((body) => ({ ...body }));
}

/** How many `GET /paths` the fake has served (the switcher's poll count). */
export function pathsListRequestCount(): number {
  return listRequests;
}

/** How many `PATCH /paths/{id}` (rename) requests the fake has served. */
export function pathRenameRequestCount(): number {
  return renameRequests;
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
  /** Display label; omit to exercise the topic fallback (the common case). */
  title?: string;
  guidance?: string | null;
  resolution?: PathStatus;
  pollsRemaining?: number;
  /** Custom outline for a `ready` path (the rail fixtures above). */
  units?: PathUnit[];
}): void {
  store.set(path.id, {
    id: path.id,
    topic: path.topic,
    title: path.title,
    guidance: path.guidance ?? null,
    level: path.level,
    resolution: path.resolution ?? "ready",
    pollsRemaining: path.pollsRemaining ?? 0,
    // **Copied, never referenced.** The fixtures above are module constants
    // shared by every test in the run, and AL-331's Apply/Undo mutate a stored
    // path's outline in place — a reference here would let one test's applied
    // lessons leak into the next test's fixture.
    units: path.units?.map((unit) => ({ ...unit, lessons: unit.lessons.map((l) => ({ ...l })) })),
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
        position_in_path: 1,
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

// --- Structural edits: what Apply and Undo do to a stored path (AL-331) -----
//
// The shaping fake (`mocks/shaping.ts`) reaches for these rather than owning a
// second copy of the outline, because Apply's whole contract is that its `path`
// is byte-for-byte `GET /paths/{id}` — a fake that answered from a private copy
// could not prove the rail's ghost rows really become the outline's real rows.
// They are deliberately *structural only*, exactly like the server's transaction
// (TDD §5.6): rows land `ungenerated` and Phase 1's pipeline owns the rest.

/** The rows one applied Addition created — what Undo has to take back. */
export interface AppliedRows {
  lessonIds: string[];
  /** Set only when the Addition grouped its lessons into a new unit. */
  unitId: string | null;
}

let appliedCounter = 0;

/** A stored path's units, made mutable (the fixtures are shared constants). */
function mutableUnits(path: StoredPath): PathUnit[] {
  if (path.units === undefined) {
    path.units = READY_UNITS.map((unit) => ({ ...unit, lessons: [...unit.lessons] }));
  }
  return path.units;
}

/**
 * `position_in_path` is a path's single total order — restate it after a shift,
 * and re-derive `unlock_state` from it.
 *
 * **Unlock state is derived, never stored** (`domains/progression.py`: complete
 * iff completed, "available iff it is the first incomplete lesson in
 * `position_in_path` order", locked otherwise). So a shift is not only a
 * renumber: a row that lands ahead of the learner's place *becomes* the
 * available lesson and the row it displaced locks behind it. A fake that left
 * every inserted row `locked` would show a path with no next lesson at all —
 * and the rail's whole continue affordance reads off exactly that state.
 */
function renumber(units: PathUnit[]): void {
  let position = 1;
  let availableTaken = false;
  for (const unit of units) {
    unit.lessons = unit.lessons.map((lesson) => {
      const complete = lesson.unlock_state === "complete";
      const available = !complete && !availableTaken;
      if (available) availableTaken = true;
      return {
        ...lesson,
        position_in_path: position++,
        unlock_state: complete ? "complete" : available ? "available" : "locked",
      };
    });
  }
}

/**
 * Insert an Addition's lessons, the way the apply transaction does: new rows are
 * ordinary `ungenerated` lessons at the named slot, and everything below them
 * shifts down. Their unlock state is not chosen here — `renumber` derives it for
 * the whole path, because that is where it comes from.
 */
export function addLessonsToPath(
  pathId: string,
  operation: {
    insert_at_position: number;
    lessons: { title: string }[];
    new_unit: { title: string; summary: string } | null;
  },
): AppliedRows {
  const path = store.get(pathId);
  if (!path) return { lessonIds: [], unitId: null };
  const units = mutableUnits(path);

  const created = operation.lessons.map((lesson) => {
    appliedCounter += 1;
    return {
      id: `a0000000-0000-4000-8000-${String(appliedCounter).padStart(12, "0")}`,
      title: lesson.title,
      // Both placeholders: `renumber` below restates the position and derives
      // the unlock state for every row on the path, this one included.
      position_in_path: 0,
      generation_state: "ungenerated" as const,
      unlock_state: "locked" as const,
    };
  });

  let unitIndex = units.findIndex((unit) =>
    unit.lessons.some((lesson) => lesson.position_in_path === operation.insert_at_position),
  );
  let rowIndex =
    unitIndex === -1
      ? -1
      : units[unitIndex].lessons.findIndex(
          (lesson) => lesson.position_in_path === operation.insert_at_position,
        );

  if (operation.new_unit) {
    appliedCounter += 1;
    const unitId = `a1000000-0000-4000-8000-${String(appliedCounter).padStart(12, "0")}`;
    units.splice(unitIndex === -1 ? units.length : unitIndex, 0, {
      id: unitId,
      title: operation.new_unit.title,
      lessons: created,
    });
    renumber(units);
    return { lessonIds: created.map((lesson) => lesson.id), unitId };
  }

  if (unitIndex === -1) {
    unitIndex = Math.max(units.length - 1, 0);
    rowIndex = units[unitIndex]?.lessons.length ?? 0;
  }
  units[unitIndex].lessons.splice(rowIndex, 0, ...created);
  renumber(units);
  return { lessonIds: created.map((lesson) => lesson.id), unitId: null };
}

/**
 * Clear a Revision's target: content gone, state back to `ungenerated`, title
 * adjusted if the operation named a new one. The lesson keeps its slot — that is
 * what makes a Revision a Revision and not an Addition (CONTEXT.md).
 */
export function reviseLessonInPath(
  pathId: string,
  lessonId: string,
  newTitle: string | null,
): { title: string } | null {
  const path = store.get(pathId);
  if (!path) return null;
  for (const unit of mutableUnits(path)) {
    const index = unit.lessons.findIndex((lesson) => lesson.id === lessonId);
    if (index === -1) continue;
    const before = unit.lessons[index];
    unit.lessons = [...unit.lessons];
    unit.lessons[index] = {
      ...before,
      title: newTitle ?? before.title,
      generation_state: "ungenerated",
    };
    return { title: before.title };
  }
  return null;
}

/** Undo's half of the Addition: the rows go, the positions unshift. */
export function removeLessonsFromPath(pathId: string, rows: AppliedRows): void {
  const path = store.get(pathId);
  if (!path) return;
  const units = mutableUnits(path);
  const removing = new Set(rows.lessonIds);
  for (const unit of units) {
    unit.lessons = unit.lessons.filter((lesson) => !removing.has(lesson.id));
  }
  if (rows.unitId !== null) {
    const index = units.findIndex((unit) => unit.id === rows.unitId);
    if (index !== -1) units.splice(index, 1);
  }
  renumber(units);
}

/** A stored path exactly as `GET /paths/{id}` serves it — Apply's `path` field. */
export function pathDetailFor(pathId: string): PathDetail | undefined {
  const path = store.get(pathId);
  return path ? detailFor(path) : undefined;
}

function detailFor(path: StoredPath): PathDetail {
  const generating = path.pollsRemaining > 0;
  const status: PathStatus = generating ? "generating" : path.resolution;
  const units = status === "ready" ? (path.units ?? READY_UNITS) : [];
  return {
    id: path.id,
    topic: path.topic,
    // The real server's fallback (docs/api.md): `title` is always populated,
    // never absent — a fixture that never renamed the path echoes its topic,
    // exactly like an untouched path fresh out of `POST /paths`.
    title: path.title ?? path.topic,
    guidance: path.guidance,
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
    title: detail.title,
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

/** `422 validation_error` — the off-allowlist model override (docs/api.md). */
function offAllowlistEnvelope(model: string) {
  return HttpResponse.json(
    {
      error: {
        code: "validation_error",
        message: `Model '${model}' is not in the allowlist.`,
        request_id: "test-request-id",
      },
    },
    { status: 422 },
  );
}

/**
 * `422 validation_error` for an over-long topic — the *other* rejection sharing
 * this status code (`TopicStr`, `dtos/paths.py`). Same envelope, nothing to do
 * with the model picker: the two are only tellable apart by what the client
 * sent, which is what keeps the picker from claiming this one.
 */
function topicTooLongEnvelope() {
  return HttpResponse.json(
    {
      error: {
        code: "validation_error",
        message: `Topic must be at most ${TOPIC_MAX_LENGTH} characters.`,
        request_id: "test-request-id",
      },
    },
    { status: 422 },
  );
}

/**
 * `422 validation_error` for a blank or over-long title — `PathTitleStr`
 * (`dtos/paths.py`, 1-200 chars stripped). The client already trims/caps
 * before sending, so this exists to catch a client that stops doing that
 * (F10) rather than to be a realistic day-to-day response.
 */
function titleInvalidEnvelope() {
  return HttpResponse.json(
    {
      error: {
        code: "validation_error",
        message: `Title must be 1-${PATH_TITLE_MAX_LENGTH} characters.`,
        request_id: "test-request-id",
      },
    },
    { status: 422 },
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
    const body = (await request.json()) as Record<string, unknown> & {
      topic: string;
      level: Level;
      guidance?: string;
    };
    createBodies.push({ ...body });
    // Request-body validation comes first, as Pydantic's does: an over-long
    // topic is `422` before anything else is considered.
    if (typeof body.topic === "string" && body.topic.trim().length > TOPIC_MAX_LENGTH) {
      return topicTooLongEnvelope();
    }
    // Model overrides are validated **before** the rate limit and any billed
    // work (docs/api.md): an id outside `MODEL_ALLOWLIST` is 422, whatever the
    // cap says. (The non-admin 403 branch is server-side only — the picker is
    // hidden for non-admins, so no test drives it through the fake.)
    for (const slot of ["model_outline", "model_lesson"] as const) {
      const chosen = body[slot];
      if (typeof chosen === "string" && !config.modelAllowlist.includes(chosen)) {
        return offAllowlistEnvelope(chosen);
      }
    }
    if (config.rateLimited) {
      return rateLimitEnvelope();
    }
    idCounter += 1;
    const id = `00000000-0000-4000-8000-${String(idCounter).padStart(12, "0")}`;
    store.set(id, {
      id,
      topic: body.topic,
      guidance: body.guidance ?? null,
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

  http.patch(`${API_V1_BASE}/paths/:id`, async ({ request, params }) => {
    // Not found first, and uncounted: a PATCH to a path this fake never had
    // did no rename work, so it should not read as one on `renameRequests`
    // (F10) — mirrors the real route's `OwnedPath` 404, which runs before any
    // write.
    const path = store.get(params.id as string);
    if (!path) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "Path not found." } },
        { status: 404 },
      );
    }
    renameRequests += 1;
    if (config.renameFails) {
      return serverErrorEnvelope();
    }
    const body = (await request.json()) as { title?: string };
    // `PathTitleStr` (docs/api.md): required, 1-200 chars, stripped
    // server-side. A blank/over-long title is `422 validation_error`, not a
    // silent no-op (F10) — the fake used to swallow both and echo the OLD
    // title back with a `200`, which would hide a client that stopped
    // trimming/capping before sending.
    const trimmed = body.title?.trim();
    if (!trimmed || trimmed.length > PATH_TITLE_MAX_LENGTH) {
      return titleInvalidEnvelope();
    }
    path.title = trimmed;
    // Echoes the full detail — the same shape `GET /paths/{id}` returns
    // (docs/api.md) — so the caller can write it straight into the poll cache.
    return HttpResponse.json(detailFor(path));
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
