import { HttpResponse, http } from "msw";
import { API_V1_BASE, AUTH_LOGOUT_PATH, type AuthSession } from "../lib/api";
import { lessonsHandlers } from "./lessons";
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
