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

/** `GET /api/v1/paths/{id}` body — the poll target (docs/api.md). */
export interface PathDetail {
  id: string;
  topic: string;
  level: Level;
  status: PathStatus;
  /** Non-null **only** when `status == "refused"` (docs/api.md). */
  refusal_message: string | null;
  progress: number;
  units: PathUnit[];
}

/** `POST /api/v1/paths` / `POST /api/v1/paths/{id}/retry` body — `202 {id}`. */
export interface PathCreated {
  id: string;
}

export interface CreatePathInput {
  topic: string;
  level: Level;
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

/** TanStack query key for a single path's detail poll. */
export function pathQueryKey(id: string): readonly ["paths", string] {
  return ["paths", id] as const;
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

/** True once `apiFetch` raised the daily-cap envelope (`429 rate_limited`). */
export function isRateLimited(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 429 || error.code === "rate_limited");
}
