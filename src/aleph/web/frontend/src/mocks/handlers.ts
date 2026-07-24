import { HttpResponse, http } from "msw";
import { API_V1_BASE, AUTH_LOGOUT_PATH, type AuthSession, type AuthUser } from "../lib/api";
import { lessonsHandlers } from "./lessons";
import { ADMIN_MODEL_ALLOWLIST } from "./models";
import { pathsHandlers } from "./paths";

// Contract-shaped fakes for the session endpoint AL-020/AL-021 will serve live.
// The default is an authenticated, non-admin learner; tests override per-case
// with `server.use(...)` to exercise the signed-out and provider variants.

export const authenticatedSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: {
    id: "99999999-9999-4999-8999-999999999999",
    username: "learner",
    display_name: "Dev Learner",
    email: "learner@example.com",
    is_admin: false,
    model_allowlist: [],
  },
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
];
