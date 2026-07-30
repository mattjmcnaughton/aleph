import { HttpResponse, http } from "msw";
import { API_V1_BASE, AUTH_LOGOUT_PATH, type AuthSession, type AuthUser } from "../lib/api";
import { lessonsHandlers } from "./lessons";
import { ADMIN_MODEL_ALLOWLIST } from "./models";
import { pathsHandlers } from "./paths";
import { tutorHandlers } from "./tutor";

// Contract-shaped fakes for the session endpoint AL-020/AL-021 will serve live.
// The default is an authenticated, non-admin learner; tests override per-case
// with `server.use(...)` to exercise the signed-out and provider variants.

/**
 * The default identity: a plain, non-admin learner. Exported on its own — like
 * `adminUser` below — so a test can vary one field (AL-230 serves a learner
 * holding a per-user `tutor` override) without respelling the whole user, or
 * reaching through the session union's nullable `user` to spread it.
 */
export const learnerUser: AuthUser = {
  id: "99999999-9999-4999-8999-999999999999",
  username: "learner",
  display_name: "Dev Learner",
  email: "learner@example.com",
  is_admin: false,
  model_allowlist: [],
  // Phase 2's flag ships dark (AL-203): the default fake learner sees the
  // tutor off, so any surface gated on it must stay hidden unless a test
  // deliberately serves a session with it on.
  feature_flags: { tutor: false },
};

export const authenticatedSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: learnerUser,
};

/**
 * The admin identity (email domain in `ADMIN_EMAIL_DOMAINS`, TDD §7) behind the
 * sessions below: the only user carrying a non-empty `model_allowlist`, and so
 * the only one the admin model picker (AL-065, §5.3/D14) renders for. Exported
 * on its own so a test can vary one field (e.g. a narrower `model_allowlist`)
 * without respelling the whole user; the default session above stays a plain
 * learner, and tests install these with `server.use(...)`.
 */
export const adminUser: AuthUser = {
  id: "88888888-8888-4888-8888-888888888888",
  username: "admin",
  display_name: "Dev Admin",
  email: "admin@example.com",
  is_admin: true,
  model_allowlist: [...ADMIN_MODEL_ALLOWLIST],
  // Admins dogfood the tutor before launch (ADMIN_DEFAULT_FLAGS, AL-203).
  feature_flags: { tutor: true },
};

export const adminSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: adminUser,
};

/**
 * An admin whose `MODEL_ALLOWLIST` is empty — nothing to pick, so the picker has
 * no reason to render. Guards the "admin" branch against assuming a non-empty
 * list (the allowlist is config, and config can be emptied).
 */
export const adminSessionNoModels: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...adminUser, model_allowlist: [] },
};

export const signedOutSession: AuthSession = {
  authenticated: false,
  provider: "keycloak",
  user: null,
};

export const handlers = [
  http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(authenticatedSession)),
  http.post(AUTH_LOGOUT_PATH, () => new HttpResponse(null, { status: 204 })),
  ...pathsHandlers,
  ...lessonsHandlers,
  ...tutorHandlers,
];
