// The single module that owns the HTTP seam to the Aleph backend.
// Everything that talks to the API goes through `apiFetch` / `apiV1Path`; no
// component builds a URL or calls `fetch` directly. Resource routes live under
// `/api/v1` (TDD §6); the OIDC flow lives at unversioned `/auth/*`.

import { queryOptions, skipToken } from "@tanstack/react-query";

const API_BASE = import.meta.env?.VITE_API_URL ?? "";

/** Base path for versioned, session-cookie-protected resources (TDD §6). */
export const API_V1_BASE = "/api/v1";

/** Unversioned OIDC endpoints (habagou auth flow, TDD §6/§7). */
export const AUTH_LOGIN_PATH = "/auth/login";
export const AUTH_LOGOUT_PATH = "/auth/logout";

// --- Error handling ---------------------------------------------------------

type ErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
    details?: unknown;
  };
};

/** Thrown for any non-2xx API response, carrying the backend error envelope. */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
    public readonly requestId?: string,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function parseErrorEnvelope(res: Response): Promise<ErrorEnvelope> {
  try {
    return (await res.json()) as ErrorEnvelope;
  } catch {
    return {};
  }
}

// TODO(AL-020): no global 401 -> /login seam here yet. The session is fetched
// once per SPA lifetime (root `beforeLoad`), so a mid-session cookie expiry is
// not caught until reload. AL-020/AL-021 (live OIDC wiring) should add a 401
// interceptor in this wrapper that invalidates the session query and redirects.

/** Fetch wrapper: prefixes the API base, unwraps JSON, and raises ApiError. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const envelope = await parseErrorEnvelope(res);
    const code = envelope.error?.code ?? `http_${res.status}`;
    const message = envelope.error?.message ?? `API error: ${res.status} ${res.statusText}`;
    throw new ApiError(
      message,
      res.status,
      code,
      envelope.error?.request_id,
      envelope.error?.details,
    );
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

/** Build a versioned resource path, e.g. apiV1Path("/paths") -> "/api/v1/paths". */
export function apiV1Path(path: `/${string}`): string {
  return `${API_V1_BASE}${path}`;
}

// --- Session contract -------------------------------------------------------
//
// Shape assumed for `GET /api/v1/auth/session` (TDD §6/§7). AL-020/AL-021 (the
// live OIDC wiring) must serve exactly this. Until then MSW fakes it.

/**
 * Configured OIDC provider (TDD §7): "keycloak" in dev/CI, "auth0" in prod.
 * Kept as a bare string for habagou parity — the value is only rendered, never
 * branched on structurally.
 */
export type OidcProvider = string;

export interface AuthUser {
  /** Local account UUID — the (issuer, subject) identity maps to this (TDD §7). */
  id: string;
  username: string;
  display_name: string;
  /** null when the IdP reported email_verified:false (TDD §7). */
  email: string | null;
  /** Derived from ADMIN_EMAIL_DOMAINS, never stored (TDD §7). */
  is_admin: boolean;
  /**
   * Admin model-picker options: bare model-id strings from MODEL_ALLOWLIST
   * (TDD §14, e.g. "anthropic/claude-sonnet-5"). Populated for admins, [] for
   * everyone else. Rendered as raw ids; no display labels in Phase 1.
   */
  model_allowlist: string[];
}

export type AuthSession =
  | { authenticated: true; provider: OidcProvider; user: AuthUser }
  | { authenticated: false; provider: OidcProvider; user: null };

/** Current session: user identity, admin flag, model allowlist (TDD §6). */
export function getAuthSession(): Promise<AuthSession> {
  return apiFetch<AuthSession>(apiV1Path("/auth/session"));
}

/** End the session server-side; the caller navigates to /login afterwards. */
export async function logout(): Promise<void> {
  await apiFetch<void>(AUTH_LOGOUT_PATH, { method: "POST" });
}

// --- Domain state enums + terminal-state predicates -------------------------
//
// The trigger+poll surfaces (TDD §5.4) share the generic polling helper in
// `./polling`; these predicates tell it when to stop for each poll target.

/** Path outline lifecycle (TDD §4). */
export type PathStatus = "pending" | "generating" | "ready" | "failed" | "refused";

/** Per-lesson generation lifecycle (TDD §4). */
export type LessonGenerationState = "ungenerated" | "generating" | "generated" | "failed";

/** A path poll can stop once the outline is ready, failed, or refused. */
export function isPathStatusTerminal(status: PathStatus | undefined): boolean {
  return status === "ready" || status === "failed" || status === "refused";
}

/** A lesson poll can stop once content is generated or generation failed. */
export function isLessonStateTerminal(state: LessonGenerationState | undefined): boolean {
  return state === "generated" || state === "failed";
}

// --- Paths API (AL-050 wire contract, docs/api.md) --------------------------
//
// Trigger + poll (§5.4/D5): `POST /paths` returns `202 {id}` and the client
// polls `GET /paths/{id}` until `status` resolves. `POST /paths/{id}/retry`
// re-claims a `failed` outline (§5.6/W8). All three go through `apiFetch`.

/** The learner's self-assessed starting point, chosen at onboarding (CONTEXT). */
export type Level = "new_to_it" | "some_experience" | "work_in_it";

/** Per-lesson axes carried in the path detail (the two orthogonal states). */
export type LessonUnlockState = "locked" | "available" | "complete";

export interface PathLesson {
  id: string;
  title: string;
  position_in_path: number;
  generation_state: LessonGenerationState;
  unlock_state: LessonUnlockState;
}

export interface PathUnit {
  id: string;
  title: string;
  lessons: PathLesson[];
}

/**
 * Per-path lesson roll-up (`PathProgressDTO`, docs/api.md / `dtos/paths.py`): the
 * counts behind both the "Your paths" switcher summary and the path-view header.
 * The wire field is an **object**, not a scalar — a bare count can't distinguish
 * "generated" from "completed". AL-062 derives its `n of m complete` readout from
 * the `unlock_state`s in `units`; AL-064 (the switcher) consumes these counts.
 */
export interface PathProgress {
  total_lessons: number;
  generated_lessons: number;
  completed_lessons: number;
}

/** `GET /api/v1/paths/{id}` body — the poll target (docs/api.md). */
export interface PathDetail {
  id: string;
  topic: string;
  level: Level;
  status: PathStatus;
  /** Non-null **only** when `status == "refused"` (docs/api.md). */
  refusal_message: string | null;
  progress: PathProgress;
  units: PathUnit[];
}

/**
 * One row of `GET /api/v1/paths` — the "Your paths" switcher (docs/api.md,
 * `PathSummaryDTO`). The outline itself is deliberately absent: the switcher
 * shows topic, level, status and the progress roll-up; the units/lessons rail
 * is the path view's payload.
 */
export interface PathSummary {
  id: string;
  topic: string;
  level: Level;
  /** Effective status — a stale `generating` reads as `failed` (docs/api.md). */
  status: PathStatus;
  progress: PathProgress;
}

/**
 * `GET /api/v1/paths` body — the learner's paths, newest first. Wrapped in an
 * object (never a bare top-level array) so the payload can grow fields without
 * a breaking shape change (`PathListResponse`, docs/api.md).
 */
export interface PathList {
  paths: PathSummary[];
}

/** `POST /api/v1/paths` / `POST /api/v1/paths/{id}/retry` body — `202 {id}`. */
export interface PathCreated {
  id: string;
}

export interface CreatePathInput {
  topic: string;
  level: Level;
  /**
   * Admin model-picker overrides (AL-052/AL-065, §5.3/D14, docs/api.md): bare
   * OpenRouter ids drawn from the session's `user.model_allowlist`, pinning the
   * outline / lesson slot on this path. **Optional in the absent sense** — an
   * unset slot must leave the key off the payload entirely, because sending an
   * override at all is `403 forbidden` for a non-admin and an id outside the
   * allowlist is `422 validation_error`. Omitted (the common case) uses the
   * server's configured slot model.
   */
  model_outline?: string;
  model_lesson?: string;
}

/** Create a path and trigger its outline (§5.1). Returns the new path id. */
export function createPath(input: CreatePathInput): Promise<PathCreated> {
  return apiFetch<PathCreated>(apiV1Path("/paths"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

/** Poll a path's detail (outline status + units) — the trigger+poll GET. */
export function getPath(id: string): Promise<PathDetail> {
  return apiFetch<PathDetail>(apiV1Path(`/paths/${id}`));
}

/** Re-claim a `failed` outline (§5.6/W8). Terminal paths are a silent no-op. */
export function retryPath(id: string): Promise<PathCreated> {
  return apiFetch<PathCreated>(apiV1Path(`/paths/${id}/retry`), { method: "POST" });
}

/** The learner's paths, newest first — the switcher's payload (§5.5). */
export function listPaths(): Promise<PathList> {
  return apiFetch<PathList>(apiV1Path("/paths"));
}

/**
 * Hard-delete one path and its progress (`204`, cascades to units/lessons/
 * attempts). Destructive and not undoable in MVP — the caller MUST confirm
 * first (§5.5/W5). Deletes only this path; the learner's others are untouched.
 */
export function deletePath(id: string): Promise<void> {
  return apiFetch<void>(apiV1Path(`/paths/${id}`), { method: "DELETE" });
}

/**
 * The prefix every paths query sits under: one
 * `invalidateQueries({ queryKey: PATHS_QUERY_PREFIX })` reaches the switcher
 * list *and* every cached path detail. Its caller is lesson completion, which
 * moves state on both surfaces at once (AL-090/W1: the rail's unlock states and
 * the two progress readouts).
 *
 * Declared first, and the two keys below are built from it, so that reach is
 * structural: a key that did not extend this prefix could not be written without
 * saying so.
 */
export const PATHS_QUERY_PREFIX: readonly ["paths"] = ["paths"] as const;

/** TanStack query key for a single path's detail poll. */
export function pathQueryKey(id: string): readonly ["paths", string] {
  return [...PATHS_QUERY_PREFIX, id] as const;
}

/**
 * TanStack query key for the switcher's list. A constant, not a factory: the
 * list takes no argument (the account comes from the session cookie), unlike
 * `pathQueryKey(id)`. The `"list"` segment sits where a path id sits in
 * `pathQueryKey`, which is unambiguous because ids are UUIDs.
 */
export const PATHS_LIST_QUERY_KEY: readonly ["paths", "list"] = [
  ...PATHS_QUERY_PREFIX,
  "list",
] as const;

/**
 * THE "Your paths" list query — key + fetcher paired in one place, the house
 * rule from `sessionQueryOptions`/`pathQueryOptions`. Takes no argument (the
 * account comes from the session cookie), so it is a value, not a factory.
 *
 * No `refetchOnWindowFocus` override: the app-wide default (`makeQueryClient`)
 * leaves it off, so a path deleted in another tab lingers here until the next
 * mount or invalidation. Deliberate for MVP — one setting for the whole app
 * beats a per-query exception, and the row's own view 404s on open.
 */
export const pathsListQueryOptions = queryOptions({
  queryKey: PATHS_LIST_QUERY_KEY,
  queryFn: listPaths,
});

/**
 * When the switcher can stop refetching `GET /paths`. The list is not a
 * trigger+poll target of its own, but a freshly created path can still be
 * `pending`/`generating` when the learner lands here (they navigated back off
 * onboarding), and nothing else would move that row. So the list rides the same
 * shared backoff (`./polling`) while any row is non-terminal and stops once
 * every path has resolved.
 */
export function isPathListTerminal(list: PathList | undefined): boolean {
  if (list === undefined) return false;
  return list.paths.every((path) => isPathStatusTerminal(path.status));
}

/**
 * THE path-detail query — key + fetcher paired in one place (the
 * `sessionQueryOptions` house rule in `lib/auth.ts`). Callers spread it into
 * `useQuery` and layer on their own options (e.g. onboarding adds
 * `refetchInterval`). Pass `null` before a path exists: the query idles on
 * `skipToken` instead of firing, so no caller hand-spells the key/fetcher pair
 * or casts a nullable id (TanStack v5 idiom, replaces `enabled` + `as string`).
 */
export function pathQueryOptions(id: string | null) {
  return queryOptions({
    queryKey: pathQueryKey(id ?? "idle"),
    queryFn: id === null ? skipToken : () => getPath(id),
  });
}

/**
 * When the path view can stop polling `GET /paths/{id}` (§5.4, §14). The view
 * lands here the moment the outline is `ready`, but on-demand generation keeps
 * running: the available lesson (and its `PREFETCH_N` successors, §14) can still
 * be `generating`. So the poll runs while the outline is non-terminal **or** any
 * lesson in the payload is still generating, and stops once everything visible
 * is stable — matching the shared backoff cadence in `./polling`.
 */
export function isPathViewTerminal(detail: PathDetail | undefined): boolean {
  if (detail === undefined) return false;
  if (!isPathStatusTerminal(detail.status)) return false;
  const anyLessonResolving = detail.units.some((unit) =>
    unit.lessons.some((lesson) => {
      // A prefetching successor (any unlock state, §14) still mid-generation.
      if (lesson.generation_state === "generating") return true;
      // The reachable gap (§14): the outline is `ready` and a lesson is already
      // `available`, but its content is still `ungenerated` — `poll_path` spawns
      // the resume *then* snapshots, so the ready payload can precede the claim.
      // Stopping here would strand the learner on an available-but-empty lesson,
      // so keep polling until that lesson's generation state is terminal.
      if (lesson.unlock_state === "available")
        return !isLessonStateTerminal(lesson.generation_state);
      return false;
    }),
  );
  return !anyLessonResolving;
}

/** True once `apiFetch` raised the daily-cap envelope (`429 rate_limited`). */
export function isRateLimited(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 429 || error.code === "rate_limited");
}

/**
 * True once `apiFetch` raised a `404` — a deep link to a path that doesn't
 * exist (deleted, or never owned by this learner). Terminal for polling: unlike
 * a transient network blip, a 404 never resolves, and each poll of the real
 * `GET /paths/{id}` spawns a backend resume, so retrying forever is harmful.
 */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/**
 * True once `apiFetch` raised a `422 validation_error`. On `POST /paths` the
 * only reachable cause is an off-allowlist model override (docs/api.md) — topic
 * length and the level enum are constrained by the form itself — so the model
 * picker (AL-065) claims this error and shows it against the picker rather than
 * letting the generic "something went wrong" copy swallow a fixable choice.
 */
export function isValidationError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 422 || error.code === "validation_error");
}

// --- Lessons API (AL-051 wire contract, docs/api.md) ------------------------
//
// Same trigger + poll model as paths (§5.4/D5): `POST /lessons/{id}/generate`
// returns `202` and the client polls `GET /lessons/{id}` until `generation_state`
// resolves. `attempt` and `complete` are synchronous state changes (not
// generation triggers). Answer-hiding (W6, §6): the pre-Attempt payload carries
// NO correct answer anywhere — `correct_index`/`explanation` live only inside
// `attempt`, which is `null` until the learner records an Attempt.

/** The result of an Attempt: correct or incorrect (CONTEXT — non-gating). */
export type LessonOutcome = "correct" | "incorrect";

/**
 * The Quick check as served pre-Attempt: stem + options ONLY. The keyed answer
 * (`correct_index`) and `explanation` are deliberately absent here (W6) — they
 * arrive only inside `attempt` once an Attempt is recorded.
 */
export interface QuickCheck {
  stem: string;
  options: string[];
}

/**
 * The reveal boundary (`AttemptResult`, docs/api.md): present on `GET` only once
 * an Attempt exists (revealed-on-return), and returned by `POST .../attempt`.
 * First-wins — a re-submit returns the first Attempt's stored outcome.
 */
export interface LessonAttempt {
  selected_index: number;
  outcome: LessonOutcome;
  correct_index: number;
  explanation: string;
}

/** `GET /api/v1/lessons/{id}` body — the poll target (docs/api.md). */
export interface LessonDetail {
  id: string;
  path_id: string;
  title: string;
  position_in_path: number;
  position_in_unit: number;
  generation_state: LessonGenerationState;
  unlock_state: LessonUnlockState;
  /** Non-null only when `generation_state == generated`. */
  read_passage: string | null;
  /** Non-null only when `generation_state == generated` (stem + options only). */
  quick_check: QuickCheck | null;
  /** Non-null only once an Attempt is recorded — the answer-reveal (W6). */
  attempt: LessonAttempt | null;
  /** Non-null only when `generation_state == failed` (learner-safe message). */
  generation_error: string | null;
}

/** `POST /api/v1/lessons/{id}/complete` body — `200 {id, unlock_state}`. */
export interface LessonCompleted {
  id: string;
  unlock_state: LessonUnlockState;
}

/** Poll a lesson's detail (generation state + content once generated). */
export function getLesson(id: string): Promise<LessonDetail> {
  return apiFetch<LessonDetail>(apiV1Path(`/lessons/${id}`));
}

/** Ensure/retry this lesson's generation (§5.2/W8). Returns the lesson id. */
export function generateLesson(id: string): Promise<{ id: string }> {
  return apiFetch<{ id: string }>(apiV1Path(`/lessons/${id}/generate`), { method: "POST" });
}

/** Record the Attempt (first-wins) and get the graded reveal (the boundary). */
export function attemptLesson(id: string, selectedIndex: number): Promise<LessonAttempt> {
  return apiFetch<LessonAttempt>(apiV1Path(`/lessons/${id}/attempt`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_index: selectedIndex }),
  });
}

/** Mark the lesson complete (non-gating; idempotent on an already-complete one). */
export function completeLesson(id: string): Promise<LessonCompleted> {
  return apiFetch<LessonCompleted>(apiV1Path(`/lessons/${id}/complete`), { method: "POST" });
}

/** TanStack query key for a single lesson's detail poll. */
export function lessonQueryKey(id: string): readonly ["lessons", string] {
  return ["lessons", id] as const;
}

/**
 * THE lesson-detail query — key + fetcher paired (mirrors `pathQueryOptions`).
 * The lesson id always exists (the route param), so — unlike `pathQueryOptions`,
 * which onboarding drives from a not-yet-created path — there is no `skipToken`
 * idle branch here.
 */
export function lessonQueryOptions(id: string) {
  return queryOptions({
    queryKey: lessonQueryKey(id),
    queryFn: () => getLesson(id),
  });
}

/**
 * When the lesson view can stop polling `GET /lessons/{id}` (§5.4). A locked
 * lesson has no content to watch, so it is terminal immediately; otherwise the
 * poll runs until generation resolves (`generated`/`failed`). A learner viewing
 * an available-but-`ungenerated` lesson keeps polling — the GET itself is the
 * trigger that spawns the resume, so content lands within a poll.
 */
export function isLessonViewTerminal(detail: LessonDetail | undefined): boolean {
  if (detail === undefined) return false;
  if (detail.unlock_state === "locked") return true;
  return isLessonStateTerminal(detail.generation_state);
}
