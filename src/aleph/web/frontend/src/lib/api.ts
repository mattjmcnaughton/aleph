// The single module that owns the HTTP seam to the Aleph backend.
// Everything that talks to the API goes through `apiFetch` / `apiV1Path`; no
// component builds a URL or calls `fetch` directly. Resource routes live under
// `/api/v1` (TDD §6); the OIDC flow lives at unversioned `/auth/*`.

import { infiniteQueryOptions, queryOptions, skipToken } from "@tanstack/react-query";

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
    // A body that isn't JSON (a proxy's HTML 502) keeps the generic copy.
    return {};
  }
}

/**
 * Build the shared `ApiError` from a non-2xx response.
 *
 * Exported for exactly one caller besides `apiFetch`: the tutor's streamed send
 * (`lib/tutor-stream.ts`), which cannot go through `apiFetch` because it reads
 * the body progressively — but whose *pre-stream* failure is an ordinary error
 * envelope and must raise the identical shape. One implementation, so the two
 * readings of the envelope cannot drift.
 */
export async function apiErrorFrom(res: Response): Promise<ApiError> {
  const envelope = await parseErrorEnvelope(res);
  return new ApiError(
    envelope.error?.message ?? `API error: ${res.status} ${res.statusText}`,
    res.status,
    envelope.error?.code ?? `http_${res.status}`,
    envelope.error?.request_id,
    envelope.error?.details,
  );
}

// TODO(AL-020): no global 401 -> /login seam here yet. The session is fetched
// once per SPA lifetime (root `beforeLoad`), so a mid-session cookie expiry is
// not caught until reload. AL-020/AL-021 (live OIDC wiring) should add a 401
// interceptor in this wrapper that invalidates the session query and redirects.

/** Fetch wrapper: prefixes the API base, unwraps JSON, and raises ApiError. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    throw await apiErrorFrom(res);
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
  /**
   * Resolved feature flags for this learner (AL-203): every flag in the
   * backend's code registry mapped to its effective value. The backend resolves
   * them (order: `services/feature_flags.py`, restated in `docs/api.md`); the
   * frontend only reads the answer. Read it through `useFeatureFlag` in
   * `lib/feature-flags.ts` rather than indexing directly — the hook is what
   * makes an absent key resolve to off.
   */
  feature_flags: Record<string, boolean>;
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
  /**
   * The generation input — frozen after creation, never learner-editable. Kept
   * on the payload for the surfaces that must still read it (e.g. the shaper's
   * own prompt, server-side); display sites read `title` instead.
   */
  topic: string;
  /**
   * The learner-editable display label (docs/api.md). Always populated — the
   * server applies the topic fallback, so the client never respells it. This is
   * what every display site (breadcrumbs, sidebar, the h1) renders.
   */
  title: string;
  /**
   * Free-text creation input alongside `topic` (docs/api.md), null when none was
   * given. Frozen after creation like `topic` — display-only here, never
   * re-derived or edited from this surface.
   */
  guidance: string | null;
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
  /** The learner-editable display label — always populated (docs/api.md); see `PathDetail.title`. */
  title: string;
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
   * Free-text creation input alongside `topic` (docs/api.md, `GuidanceStr`,
   * 1-4000 chars). **Optional in the absent sense** — `buildCreatePathInput`
   * omits the key entirely for a blank/whitespace-only textarea rather than
   * sending an empty string, mirroring the model-slot rule below.
   */
  guidance?: string;
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

/**
 * Rename a path's learner-facing title (docs/api.md `PATCH /paths/{id}`). Never
 * `topic` — the title is the only thing this endpoint can touch, and the server
 * re-applies its own fallback, so the client sends exactly what was typed and
 * trusts nothing else. Returns the full `PathDetail` (the same shape as the poll
 * target), so the caller can write the result straight into the cached query.
 */
export function updatePathTitle(input: { pathId: string; title: string }): Promise<PathDetail> {
  return apiFetch<PathDetail>(apiV1Path(`/paths/${input.pathId}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: input.title }),
  });
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

// --- Progress API (Streaks TDD §6/§8) ---------------------------------------
//
// One endpoint, `GET /progress/summary`: the global daily streak, the 49-day
// activity window and the per-path breakdown, all derived server-side from
// `lessons.completed_at` (Streaks TDD D1) — there is nothing here to trigger or
// poll, and (§7) no `refetchInterval`: unlike the paths list, nothing about a
// streak arrives asynchronously, so polling it would be pure cost. It refetches
// on exactly two triggers — a completion (D10, `routes/lessons.$lessonId.tsx`)
// and TanStack's default remount/refocus behaviour.

/** One day of the 49-day activity window (Streaks TDD §6), oldest first. */
export interface ActivityCell {
  date: string;
  count: number;
}

/** One path's row in the summary's `paths` array. Absent means zero (D5). */
export interface PathStreak {
  path_id: string;
  current_streak: number;
  best_streak: number;
  completed_today: number;
}

/** `GET /api/v1/progress/summary` body (Streaks TDD §6). */
export interface ProgressSummary {
  /** The learner's local calendar day, as the server resolved it (D3). */
  today: string;
  current_streak: number;
  best_streak: number;
  completed_today: number;
  /** Exactly `STREAK_ACTIVITY_WINDOW_DAYS` (49) entries, oldest first,
   *  zero-filled — `activity-strip.tsx`'s input, and exactly 7×7. */
  activity: ActivityCell[];
  /** Paths with at least one completion; a path with none is simply absent (D5). */
  paths: PathStreak[];
}

/** The summary for one instant's offset — always called through the options
 *  factory below, never given a hand-rolled `getTimezoneOffset()` result. */
export function getProgressSummary(tzOffsetMinutes: number): Promise<ProgressSummary> {
  return apiFetch<ProgressSummary>(
    apiV1Path(`/progress/summary?tz_offset_minutes=${tzOffsetMinutes}`),
  );
}

/**
 * The prefix the completion mutation invalidates (Streaks D10,
 * `routes/lessons.$lessonId.tsx`). Its own namespace, not a branch of
 * `PATHS_QUERY_PREFIX`: a global cross-path summary does not belong under the
 * paths prefix, even though nesting it there would make invalidation free
 * (Streaks TDD D10).
 */
export const PROGRESS_QUERY_PREFIX: readonly ["progress"] = ["progress"] as const;

/**
 * TanStack query key for the summary. The offset rides in the key itself
 * (Streaks TDD §7): crossing a timezone or a DST boundary is a cache miss and a
 * refetch rather than a stale day boundary, and that behaviour falls out of the
 * key rather than needing logic.
 */
export function progressSummaryQueryKey(
  tzOffsetMinutes: number,
): readonly ["progress", "summary", number] {
  return [...PROGRESS_QUERY_PREFIX, "summary", tzOffsetMinutes] as const;
}

/**
 * THE call site for `Date.prototype.getTimezoneOffset` in the whole app
 * (Streaks TDD §8/§15, Phase 3 TDD §8): minutes to *subtract* from UTC to
 * reach local time, so a zone ahead of UTC reports negative. Every
 * query-options factory that needs the client's offset — this one and the two
 * Phase 3 adds below — calls this function rather than the browser API
 * directly, and the grade mutation (`routes/review.tsx`) does the same when it
 * builds a request body. That is what keeps the sign convention's one place to
 * be wrong a single function rather than "every call site that remembered to
 * copy it correctly" — `api.test.ts` is the test that says it stayed that way.
 */
export function clientTimezoneOffsetMinutes(): number {
  return new Date().getTimezoneOffset();
}

/**
 * THE progress-summary query — key + fetcher paired in one place (the
 * `sessionQueryOptions` house rule). `enabled` comes from
 * `useFeatureFlag("streaks")` (Streaks TDD §8) — off means `skipToken`, i.e.
 * no request and no rendered surface, matching every other flag-gated query in
 * this file.
 *
 * The completion mutation's optimistic bump (`routes/lessons.$lessonId.tsx`,
 * D10) and the review mutation's cross-domain patch (`routes/review.tsx`,
 * Phase 3 TDD §8) both need this same key to patch the right cache entry.
 * They get it by calling this factory too and reading `.queryKey` back off
 * it, rather than hand-spelling `progressSummaryQueryKey` a second time
 * anywhere.
 */
export function progressSummaryQueryOptions(enabled: boolean) {
  const tzOffsetMinutes = clientTimezoneOffsetMinutes();
  return queryOptions({
    queryKey: progressSummaryQueryKey(tzOffsetMinutes),
    queryFn: enabled ? () => getProgressSummary(tzOffsetMinutes) : skipToken,
  });
}

// --- Flashcards API (Phase 3 TDD §6-8, docs/api.md ## Flashcards) -----------
//
// The retention loop's wire seam: the drafting trigger+poll (D5, mirroring
// paths/lessons verbatim), the daily queue and its summary (D3 — derived,
// never stored, so there is nothing here to create or delete), and grading
// (§5.4). Every route sits behind the `flashcards` flag server-side (D10);
// every factory below is fed `enabled` from `useFeatureFlag("flashcards")` and
// goes to `skipToken` when it is off — no flag, no fetch, matching every other
// gated query in this file.

/** A lesson's drafting-run state (D7). `"not_started"` is the sparse-row case
 *  — `flashcard_draft_runs` holds one row per lesson **drafting was actually
 *  triggered for** (dtos/flashcards.py), so "no row yet" is a real, distinct
 *  wire value the backend sends for every completed lesson the instant the
 *  flag flips on, not an absence the client can treat as "still generating". */
export type FlashcardDraftRunState = "not_started" | "generating" | "generated" | "failed";

/** One proposed card, pre-keep — front/back only (docs/api.md). */
export interface FlashcardDraftCard {
  id: string;
  front: string;
  back: string;
}

/** `GET /lessons/{id}/flashcard-drafts` body — the drafting poll target. */
export interface FlashcardDrafts {
  state: FlashcardDraftRunState;
  cards: FlashcardDraftCard[];
}

/** The fixed two-outcome ladder (CONTEXT.md: *Review*) — never a third value. */
export type FlashcardGrade = "again" | "got_it";

/**
 * A card's source line (D12, docs/api.md): discriminated on `kind`, so a
 * `degraded` citation genuinely carries no `lesson_id` to dereference rather
 * than a nullable one a careless render could still link.
 */
export type FlashcardCitation =
  | { kind: "linked"; lesson_id: string; lesson_title: string; path_title: string }
  | { kind: "degraded"; lesson_title: string; path_title: string };

/** One card as the review session shows it (docs/api.md `GET /reviews/queue`). */
export interface QueueCard {
  card_id: string;
  front: string;
  back: string;
  rung: number;
  /** What the *Got it* button previews — server-derived from the ladder
   *  (`FLASHCARD_LADDER_DAYS`); the client holds no second copy of it. */
  got_it_interval_days: number;
  path_id: string | null;
  source: FlashcardCitation;
}

/** `GET /api/v1/reviews/queue` body (§5.3/§6). */
export interface ReviewQueue {
  today: string;
  /** The day's selected set's size — always the **global** count, even in a
   *  filtered (`path_id`) session (§5.3's invariant). */
  total: number;
  /** Distinct cards already answered *Got it*, today. */
  completed: number;
  scope_path_id: string | null;
  /** Non-zero only when `scope_path_id` is set — the widen offer (PRD §4.10). */
  other_due_count: number;
  /** Unsatisfied only, in serve order. */
  cards: QueueCard[];
}

/** One path's share of the global selected set (sums to `due_count`, §5.3). */
export interface PathDue {
  path_id: string;
  due_count: number;
}

/** `GET /api/v1/reviews/summary` body (D9/§6): home's card, the app-bar pill,
 *  and the per-path chips — one payload, deliberately not folded into
 *  `ProgressSummary` (D9). */
export interface ReviewSummary {
  today: string;
  due_count: number;
  estimated_minutes: number;
  /** Paths with at least one due card; absent means zero, same as `PathStreak`. */
  paths: PathDue[];
}

/** `POST /api/v1/reviews` body (§5.4/§6). */
export interface GradeCardInput {
  card_id: string;
  grade: FlashcardGrade;
  /** Optimistic-concurrency token — the client already holds it (it rendered
   *  `got_it_interval_days` from it), so a mismatch is `409 stale_rung`. */
  rung_before: number;
  tz_offset_minutes: number;
}

/** `POST /api/v1/reviews`'s `200` body: the card's new projected state. */
export interface GradeCardResult {
  card_id: string;
  rung: number;
  due_on: string;
}

/** Trigger drafting for a generated lesson (D5) — fired when the lesson is
 *  opened (AL-400), not when it completes. `202`; idempotent (D7) — re-firing
 *  from a mount effect, or a mutation `onSuccess`, that React may run twice
 *  is safe either way. */
export function triggerFlashcardDrafts(lessonId: string): Promise<{ id: string }> {
  return apiFetch<{ id: string }>(apiV1Path(`/lessons/${lessonId}/flashcard-drafts`), {
    method: "POST",
  });
}

/** Poll a lesson's drafting run. */
export function getFlashcardDrafts(lessonId: string): Promise<FlashcardDrafts> {
  return apiFetch<FlashcardDrafts>(apiV1Path(`/lessons/${lessonId}/flashcard-drafts`));
}

/**
 * A drafting poll can stop once the run is `generated` or `failed` (D7) — or
 * is `not_started`, which is terminal too: nothing moves a lesson out of
 * `not_started` except a fresh `POST .../flashcard-drafts`, and that trigger
 * invalidates this very query key itself (`routes/lessons.$lessonId.tsx`), so
 * there is no event this poll could ever be waiting to observe. Missing this
 * case used to mean any lesson sitting at `not_started` — historically every
 * already-completed lesson the moment the `flashcards` flag went live, and
 * still today any lesson whose trigger was refused (`429`/`409`) or errored —
 * polled `GET .../flashcard-drafts` every 5s forever, for as long as the tab
 * stayed open.
 *
 * Since AL-400 the poll starts on lesson *open*, so `not_started` is now the
 * ordinary first reading of a freshly-opened lesson rather than an edge case:
 * the poll stops immediately and the trigger's own `invalidateQueries` is what
 * restarts it once a run exists.
 */
export function isFlashcardDraftsTerminal(drafts: FlashcardDrafts | undefined): boolean {
  if (drafts === undefined) return false;
  return (
    drafts.state === "generated" || drafts.state === "failed" || drafts.state === "not_started"
  );
}

/**
 * Keep some drafts, discard the rest (D6). `[]` is "Skip — keep none".
 *
 * The offset rides in the body because a keep *writes a due date*: the service
 * owns "today" and sets `due_on = today + ladder[0]` (D4), so this request is
 * the one place that arithmetic needs an offset to resolve against. It is the
 * same `getTimezoneOffset()`-verbatim value every other flashcards call sends,
 * read through the one wrapped call site — never a second `getTimezoneOffset()`.
 * The field is required server-side; omitting it is a 422.
 *
 * The `200 {kept_ids}` body (docs/api.md) is the ids the server actually kept
 * — nobody reads it today (the caller patches its own cache from the ids it
 * just sent, `routes/lessons.$lessonId.tsx`), but the return type still names
 * the real shape rather than `Promise<void>`, which would silently agree with
 * a fake that answered `204` instead.
 */
export function keepFlashcardDrafts(
  lessonId: string,
  keptIds: string[],
): Promise<{ kept_ids: string[] }> {
  return apiFetch<{ kept_ids: string[] }>(apiV1Path(`/lessons/${lessonId}/flashcard-drafts/keep`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kept_ids: keptIds,
      tz_offset_minutes: clientTimezoneOffsetMinutes(),
    }),
  });
}

/** The summary for one instant's offset — always called through the options
 *  factory below (mirrors `getProgressSummary`). */
export function getReviewSummary(tzOffsetMinutes: number): Promise<ReviewSummary> {
  return apiFetch<ReviewSummary>(
    apiV1Path(`/reviews/summary?tz_offset_minutes=${tzOffsetMinutes}`),
  );
}

/** The day's queue, optionally filtered to one path for display only (§5.3). */
export function getReviewQueue(
  tzOffsetMinutes: number,
  pathId: string | null,
): Promise<ReviewQueue> {
  const params = new URLSearchParams({ tz_offset_minutes: String(tzOffsetMinutes) });
  if (pathId !== null) params.set("path_id", pathId);
  return apiFetch<ReviewQueue>(apiV1Path(`/reviews/queue?${params.toString()}`));
}

/** Grade one card (§5.4). */
export function gradeCard(input: GradeCardInput): Promise<GradeCardResult> {
  return apiFetch<GradeCardResult>(apiV1Path("/reviews"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

/**
 * The prefix every flashcards query sits under (§7). One
 * `invalidateQueries({ queryKey: FLASHCARDS_QUERY_PREFIX })` reaches the
 * summary, every cached queue (any `path_id`), and every cached drafts poll —
 * the grade mutation's authoritative refetch (§8) and the keep mutation both
 * invalidate through this single prefix rather than three hand-spelled keys.
 */
export const FLASHCARDS_QUERY_PREFIX: readonly ["flashcards"] = ["flashcards"] as const;

/** TanStack query key for the due summary (§7's exact shape). */
export function reviewSummaryQueryKey(
  tzOffsetMinutes: number,
): readonly ["flashcards", "summary", number] {
  return [...FLASHCARDS_QUERY_PREFIX, "summary", tzOffsetMinutes] as const;
}

/** TanStack query key for one day's queue, scoped by offset + path (§7). */
export function reviewQueueQueryKey(
  tzOffsetMinutes: number,
  pathId: string | null,
): readonly ["flashcards", "queue", number, string | null] {
  return [...FLASHCARDS_QUERY_PREFIX, "queue", tzOffsetMinutes, pathId ?? null] as const;
}

/** TanStack query key for one lesson's drafting poll (§7). */
export function flashcardDraftsQueryKey(
  lessonId: string,
): readonly ["flashcards", "drafts", string] {
  return [...FLASHCARDS_QUERY_PREFIX, "drafts", lessonId] as const;
}

/**
 * THE due-summary query. `enabled` comes from `useFeatureFlag("flashcards")`
 * — off means `skipToken`: no request, no pill, no *Due today* card (§8).
 */
export function reviewSummaryQueryOptions(enabled: boolean) {
  const tzOffsetMinutes = clientTimezoneOffsetMinutes();
  return queryOptions({
    queryKey: reviewSummaryQueryKey(tzOffsetMinutes),
    queryFn: enabled ? () => getReviewSummary(tzOffsetMinutes) : skipToken,
  });
}

/**
 * THE daily-queue query, for `/review?path=…` (§8). `pathId` is `null` for
 * "All paths" — a display filter only (§5.3), never a second selection.
 */
export function reviewQueueQueryOptions(enabled: boolean, pathId: string | null) {
  const tzOffsetMinutes = clientTimezoneOffsetMinutes();
  return queryOptions({
    queryKey: reviewQueueQueryKey(tzOffsetMinutes, pathId),
    queryFn: enabled ? () => getReviewQueue(tzOffsetMinutes, pathId) : skipToken,
  });
}

/**
 * THE drafting-poll query, for the block below a lesson's completion state.
 * `enabled` is the caller's own `flashcardsEnabled && generation_state ===
 * "generated" && unlock_state !== "locked"` (AL-400) — the same guard the
 * trigger route now enforces server-side (`409 lesson_not_generated`), and
 * looser than the block's own render gate (still `unlock_state ===
 * "complete"`): a drafting run can exist for a generated-but-incomplete
 * lesson now that the trigger fires on open, so this query starts polling
 * well before the block is shown.
 */
export function flashcardDraftsQueryOptions(lessonId: string, enabled: boolean) {
  return queryOptions({
    queryKey: flashcardDraftsQueryKey(lessonId),
    queryFn: enabled ? () => getFlashcardDrafts(lessonId) : skipToken,
  });
}

// --- Card list (AL-410, docs/api.md ## Flashcards) --------------------------
//
// The browse/edit/delete surface a launched retention loop was missing: the
// Daily queue is capped at ten (PRD §4.4) and shows only what happens to be
// due *today*, so there was no way to find a specific kept card, fix a typo
// in one, or drop one that stopped being useful (AL-410's own justification,
// `docs/prds/phase-3-flashcards.md` §7). Same flag gate as every other route
// in this file (`useFeatureFlag("flashcards")` -> `skipToken` when off), and
// the same `FLASHCARDS_QUERY_PREFIX` every mutation here invalidates through
// — one call reaches the queue, the summary/pill, and this list together.

/**
 * One card as `/cards` shows it (`CardListItemDTO`, docs/api.md). Carries
 * `rung` — the DTO ships it (AL-410 plan §4) — but no renderer in this app
 * reads it: CONTEXT.md's *rung* is scheduler vocabulary the learner is never
 * shown, and this ticket is deliberately not the place to introduce it
 * (AL-410 plan's product call #2). Only `due_on` reaches the screen.
 */
export interface CardListItem {
  id: string;
  front: string;
  back: string;
  rung: number;
  due_on: string;
  /** Null until a learner edits the card — the trust-boundary marker
   *  ("edit provenance", AL-410 plan §1) that lets eval sampling keep
   *  telling agent-written card text apart from learner-written text. */
  edited_at: string | null;
  /** Same discriminated shape the review session's `QueueCard.source` carries
   *  (D12) — rendered by the same rule, `components/review/card-source.tsx`. */
  source: FlashcardCitation;
}

/** `GET /api/v1/flashcards` body — one page, newest-kept-first (docs/api.md). */
export interface CardList {
  cards: CardListItem[];
  /** Opaque `"{kept_at}|{id}"` keyset cursor (AL-410 plan §2) — never parsed
   *  or built here, only round-tripped back as the next page's `cursor`. A
   *  cursor (not an offset) is what keeps pagination stable across a card
   *  another tab deletes mid-browse. */
  next_cursor: string | null;
}

/** Query params for `GET /flashcards`, all optional (docs/api.md). */
export interface CardListParams {
  pathId?: string | null;
  q?: string | null;
  cursor?: string | null;
  limit?: number;
}

/** One page of the learner's kept cards, filtered/paginated server-side. */
export function getCards(params: CardListParams = {}): Promise<CardList> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.cursor) query.set("cursor", params.cursor);
  if (params.pathId) query.set("path_id", params.pathId);
  if (params.q) query.set("q", params.q);
  const qs = query.toString();
  return apiFetch<CardList>(apiV1Path(`/flashcards${qs ? `?${qs}` : ""}`));
}

/**
 * Edit a kept card's text (docs/api.md `PATCH /flashcards/{id}`). Never
 * touches `rung`/`due_on` server-side (AL-410 plan §2/§5 — fixing wording
 * does not reset what the learner knows); the caller only ever sends
 * `front`/`back`. Returns the full updated `CardListItemDTO` so `card-row.tsx`
 * can render the server's own `edited_at` rather than guessing a timestamp.
 */
export function updateCard(input: {
  cardId: string;
  front: string;
  back: string;
}): Promise<CardListItem> {
  return apiFetch<CardListItem>(apiV1Path(`/flashcards/${input.cardId}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ front: input.front, back: input.back }),
  });
}

/**
 * Soft-delete a kept card (docs/api.md `DELETE /flashcards/{id}` -> `204`).
 * `flashcard_reviews` survives underneath it (AL-410 plan §1's "why soft
 * delete" — a hard delete would retroactively erase past Active days from the
 * Daily streak), but that is entirely a backend concern: from here it reads
 * exactly like `deletePath` — gone, not undoable, `404` on a repeat.
 */
export function deleteCard(cardId: string): Promise<void> {
  return apiFetch<void>(apiV1Path(`/flashcards/${cardId}`), { method: "DELETE" });
}

/** TanStack query key for one filtered card list (mirrors `reviewQueueQueryKey`'s
 *  offset+path scoping, minus the offset — the list carries no due/today
 *  math, so there is nothing here a timezone crossing would ever invalidate). */
export function cardsQueryKey(
  pathId: string | null,
  q: string | null,
): readonly ["flashcards", "list", string | null, string | null] {
  return [...FLASHCARDS_QUERY_PREFIX, "list", pathId, q] as const;
}

// --- Beats & Briefs API (Phase 6 TDD §6-8, AL-530, docs/api.md ## Analyst) --
//
// The analyst's wire seam: deploy + poll (D15, mirroring paths/lessons, with
// one wrinkle neither has — a Beat's `idle` is BOTH its pre-run and its
// post-success state, so the shipped router drains before it reads
// (`routers/v1/beats.py`'s own module doc) and the client's polling
// predicate below treats `idle` as terminal on purpose: the response it is
// reading always already reflects whatever this request's own arrival
// triggered. Every route sits behind the `analyst` flag server-side
// (router-level, TDD D12); every factory below is fed `enabled` from
// `useFeatureFlag("analyst")` and goes to `skipToken` when it is off — no
// flag, no fetch, matching every other gated query in this file.
//
// Brief prefetch, the Brief reading surface, the Sources block, and read
// pings are AL-531 — this seam carries only what the rail and the home card
// need: `BeatDetail`/`BeatSummary`, never `BriefDetail`.

/** A Beat's research lifecycle (TDD §4/§6). `idle` is deliberately both the
 *  pre-run state (a fresh Beat, nothing claimed yet) and the post-success
 *  state (the last run published or Skipped) — see
 *  `isBeatResearchStateTerminal` below for why that is safe to poll on. */
export type BeatResearchState = "idle" | "researching" | "failed" | "refused";

/** The only Cadence this slice ships (TDD §4.11/§6) — a `Literal` on the
 *  wire, restated here rather than widened to `string`. */
export type Cadence = "weekly";

/**
 * One published row in the Beat rail (`PublishedEntryDTO`, TDD §6). Carries
 * no `skip_line` at all — a published entry has nothing to skip.
 */
export interface PublishedEntry {
  kind: "published";
  id: string;
  number: number;
  published_on: string;
  title: string;
  /** Non-null once the read ping fires (AL-531 builds that surface) — this
   *  ticket never writes it, only renders whatever the server already has. */
  read_at: string | null;
}

/**
 * One Skipped period's row (`SkippedEntryDTO`, CONTEXT.md: Skipped, D2).
 * Carries no `title`/`read_at` at all — a Skipped entry has no body to title
 * and nothing to mark read.
 */
export interface SkippedEntry {
  kind: "skipped";
  id: string;
  number: null;
  published_on: string;
  skip_line: string;
}

/**
 * The rail's one list of both kinds (`BriefEntryDTO`, TDD §6: "entries is
 * ONE list of both kinds, never two arrays") — narrow on `kind` in every
 * renderer, never assume a nullable field belongs to both variants.
 */
export type BriefEntry = PublishedEntry | SkippedEntry;

/**
 * `GET /api/v1/beats/{id}` body (TDD §6) — the poll target and the rail.
 * `entries` is newest first, **never locked** (PRD §3): every entry in the
 * list is always fully rendered, unlike a path's lesson list.
 */
export interface BeatDetail {
  id: string;
  topic: string;
  level: Level;
  guidance: string | null;
  /** Python's Monday==0 convention (CONTEXT.md: Anchor day). */
  anchor_weekday: number;
  cadence: Cadence;
  research_state: BeatResearchState;
  research_started_at: string | null;
  /** Non-null only when `research_state === "refused"`. */
  refusal_message: string | null;
  entries: BriefEntry[];
}

/**
 * One row of `GET /api/v1/beats` (`BeatSummaryDTO`, TDD §6): the learner's
 * Beats with unread counts and research state — no `entries`, which is the
 * detail poll's own payload.
 */
export interface BeatSummary {
  id: string;
  topic: string;
  level: Level;
  anchor_weekday: number;
  cadence: Cadence;
  research_state: BeatResearchState;
  research_started_at: string | null;
  refusal_message: string | null;
  /** Published, unread Briefs only (TDD §6) — a Skipped entry never counts,
   *  since nothing can ever stamp one read. */
  unread_count: number;
}

/** `GET /api/v1/beats` body — the learner's Beats, newest first. Wrapped in
 *  an object (never a bare top-level array), the `PathListResponse`
 *  precedent, so the payload can grow fields without a breaking shape
 *  change. */
export interface BeatList {
  beats: BeatSummary[];
}

/** `POST /api/v1/beats` body (docs/api.md, `DeployBeatRequest`). Admin-only
 *  model overrides exist on the wire contract but this ticket's deploy form
 *  never renders that picker (TDD §8's own field list omits it) — the type
 *  still names the real shape rather than hiding it. */
export interface DeployBeatInput {
  topic: string;
  level: Level;
  anchor_weekday: number;
  guidance?: string;
  model_research?: string;
  model_brief?: string;
}

/**
 * Deploy an analyst. Returns the full `BeatDetail` — the same shape the poll
 * target does, and by the time this `202` resolves the first run is already
 * claimed (TDD D15: "researched immediately, not at the first Anchor day"),
 * so the response's own `research_state` already reflects that claim rather
 * than a stale pre-claim `idle` (`updatePathTitle`'s return-the-full-detail
 * precedent, not `createPath`'s bare id — this seam's poll starts from a
 * real state, never an assumed one).
 *
 * `tz_offset_minutes` rides on every Beats call, this one included
 * (code-review FIX 1) — `clientTimezoneOffsetMinutes()`, the one wrapped
 * call site every other tz-sensitive request in this file already goes
 * through. It is not cosmetic here: the arrival drain this `POST` triggers
 * (TDD §6's "the first run is claimed in the same request") derives
 * `local_today` from it, and that is the date this Beat's first Brief
 * publishes under (D4a) — the router defaults to `0` (UTC) when it is
 * omitted, so a learner west of UTC opening the app in the evening would get
 * a Brief dated a day ahead of their own calendar.
 */
export function deployBeat(input: DeployBeatInput): Promise<BeatDetail> {
  const params = new URLSearchParams({
    tz_offset_minutes: String(clientTimezoneOffsetMinutes()),
  });
  return apiFetch<BeatDetail>(apiV1Path(`/beats?${params.toString()}`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

/**
 * Poll one Beat's detail: research state + the rail — the trigger+poll GET
 * (TDD D15; the router drains this Beat before it reads, so the response
 * always reflects whatever this request's own arrival just triggered).
 *
 * `tz_offset_minutes` rides on this call (code-review FIX 1), the same
 * `clientTimezoneOffsetMinutes()` every other tz-sensitive request in this
 * file sends — **never** folded into `beatQueryKey` (TDD §7 fixes that key
 * as `["beats", id]`; the offset is a request parameter here, not a cache
 * dimension). Without it the drain this GET triggers derives `local_today`
 * from the server's UTC default, and `domains/cadence.is_claimable` /
 * `published_on` both key off that date (D4/D4a) — silently UTC for every
 * learner who never passes their own offset.
 */
export function getBeat(id: string, tzOffsetMinutes: number): Promise<BeatDetail> {
  return apiFetch<BeatDetail>(apiV1Path(`/beats/${id}?tz_offset_minutes=${tzOffsetMinutes}`));
}

/**
 * The learner's Beats, newest first, with unread counts (TDD §6). Also
 * drains every claimable Beat server-side (TDD D15) — never polled from
 * here (TDD §7: "nothing polls the beats list").
 *
 * `tz_offset_minutes` rides on this call too (code-review FIX 1), for the
 * identical reason `getBeat` above needs it: this GET drains exactly the
 * same way, so the arrival's `local_today` must come from this client's own
 * offset, never the server's UTC default.
 */
export function listBeats(tzOffsetMinutes: number): Promise<BeatList> {
  const params = new URLSearchParams({ tz_offset_minutes: String(tzOffsetMinutes) });
  return apiFetch<BeatList>(apiV1Path(`/beats?${params.toString()}`));
}

/**
 * Re-claim a `failed` run (TDD §6/D3). A Beat that is not actually `failed`
 * is a silent no-op server-side (`idle` above all — it is a Beat's healthy
 * steady state, not a stray retry target) — the client always polls
 * `GET /beats/{id}` for the outcome regardless of which branch ran.
 *
 * `tz_offset_minutes` rides on this call as well (code-review FIX 1),
 * computed internally via `clientTimezoneOffsetMinutes()` — the
 * `keepFlashcardDrafts` precedent for a mutation whose single variable
 * (here, the Beat id) is not itself a request-body object a caller builds
 * field by field. A won retry claim still derives `local_today` (D4a) for
 * the Brief it publishes, exactly as the arrival drain's own claim does — a
 * retry firing after midnight UTC but before midnight local must not
 * publish under tomorrow's date by the learner's own calendar.
 */
export function retryBeat(id: string): Promise<BeatDetail> {
  const params = new URLSearchParams({
    tz_offset_minutes: String(clientTimezoneOffsetMinutes()),
  });
  return apiFetch<BeatDetail>(apiV1Path(`/beats/${id}/retry?${params.toString()}`), {
    method: "POST",
  });
}

/** Hard-delete a Beat (also how standing orders change — PRD §4.11: delete
 *  and redeploy). Destructive and not undoable, the `deletePath` precedent —
 *  no caller in this ticket's scope yet. */
export function deleteBeat(id: string): Promise<void> {
  return apiFetch<void>(apiV1Path(`/beats/${id}`), { method: "DELETE" });
}

/**
 * TanStack query key for the learner's Beats — TDD §7's exact shape,
 * `["beats"]`, unlike `PATHS_LIST_QUERY_KEY`'s own `["paths", "list"]`: a
 * `"list"` segment would never collide with `beatQueryKey(id)` below either
 * (ids are UUIDs), so the TDD's shorter key is followed verbatim rather than
 * respelling the paths precedent.
 */
export const BEATS_LIST_QUERY_KEY: readonly ["beats"] = ["beats"] as const;

/** TanStack query key for one Beat's detail poll — TDD §7's exact shape,
 *  `["beats", id]`. */
export function beatQueryKey(id: string): readonly ["beats", string] {
  return ["beats", id] as const;
}

/**
 * When the Beat detail poll can stop (TDD §7: "polls only while
 * `research_state === 'researching'`… stops on any terminal state").
 * `idle` is deliberately terminal here even though it is also a Beat's
 * pre-run state: the shipped router (`routers/v1/beats.py`) drains before it
 * reads, so a response this predicate ever sees that still says `idle`
 * genuinely means nothing is in flight right now — never "a run I haven't
 * heard about yet". `undefined` (nothing fetched yet) is non-terminal, the
 * `isPathStatusTerminal` precedent.
 */
export function isBeatResearchStateTerminal(state: BeatResearchState | undefined): boolean {
  return state === "idle" || state === "failed" || state === "refused";
}

/** `BeatDetail`-shaped wrapper around `isBeatResearchStateTerminal` — what
 *  `routes/beats.$beatId.tsx` actually feeds `makePollingRefetchInterval`. */
export function isBeatDetailTerminal(detail: BeatDetail | undefined): boolean {
  return isBeatResearchStateTerminal(detail?.research_state);
}

/**
 * THE Beats-list query — key + fetcher paired (the `sessionQueryOptions`
 * house rule). `enabled` is `useFeatureFlag("analyst")` — off means
 * `skipToken`: no request, no rendered surface (TDD §8). No
 * `refetchInterval` is ever layered onto this one: TDD §7 is explicit that
 * nothing polls the list, because a Beat that starts researching does so
 * because this learner's own arrival triggered it, so the client already
 * knows without asking again.
 */
export function beatsListQueryOptions(enabled: boolean) {
  return queryOptions({
    queryKey: BEATS_LIST_QUERY_KEY,
    // `tz_offset_minutes` rides on the request (code-review FIX 1), never on
    // the key above — TDD §7 fixes `BEATS_LIST_QUERY_KEY` as `["beats"]`,
    // unlike `progressSummaryQueryKey`'s own offset-bearing key.
    queryFn: enabled ? () => listBeats(clientTimezoneOffsetMinutes()) : skipToken,
  });
}

/**
 * THE Beat-detail query — key + fetcher paired, the poll target
 * (`routes/beats.$beatId.tsx`, and `routes/beats.new.tsx`'s own seed write).
 * `id: null` idles on `skipToken` exactly like `pathQueryOptions` does for a
 * not-yet-created path — `routes/beats.new.tsx` has no id until the deploy
 * mutation resolves. `enabled` is `useFeatureFlag("analyst")`, layered onto
 * that same idle check: either reason is enough to skip the request.
 */
export function beatQueryOptions(id: string | null, enabled: boolean) {
  return queryOptions({
    queryKey: beatQueryKey(id ?? "idle"),
    // `tz_offset_minutes` rides on the request (code-review FIX 1), never on
    // the key above — TDD §7 fixes `beatQueryKey` as `["beats", id]`, unlike
    // `progressSummaryQueryKey`'s own offset-bearing key.
    queryFn: id !== null && enabled ? () => getBeat(id, clientTimezoneOffsetMinutes()) : skipToken,
  });
}

/**
 * THE card-list query, for `/cards` (AL-410 plan §6) — an **infinite** query,
 * unlike every other factory in this file, because "Load more" is the one
 * place this phase paginates (a single page + `next_cursor`-driven button,
 * never scroll-triggered auto-fetch — `routes/cards.tsx` calls
 * `fetchNextPage()` from a button's `onClick` alone, per the plan's explicit
 * "do not build infinite scroll"). `useInfiniteQuery` over hand-rolled
 * page-appending state buys one thing that would otherwise have to be
 * reinvented: `invalidateQueries({queryKey: FLASHCARDS_QUERY_PREFIX})` (the
 * edit/delete mutations' own rule) refetches every page already loaded, each
 * one keyed off the cursor the *previous* page's own refetch actually
 * returned — so a page fetched before a delete still lines up with the
 * server's post-delete rows rather than silently collapsing back to page one
 * and discarding whatever "Load more" had already fetched.
 */
export function cardsQueryOptions(
  enabled: boolean,
  { pathId, q }: { pathId: string | null; q: string | null },
) {
  return infiniteQueryOptions({
    queryKey: cardsQueryKey(pathId, q),
    queryFn: enabled
      ? ({ pageParam }: { pageParam: string | null }) => getCards({ pathId, q, cursor: pageParam })
      : skipToken,
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage: CardList) => lastPage.next_cursor,
  });
}
