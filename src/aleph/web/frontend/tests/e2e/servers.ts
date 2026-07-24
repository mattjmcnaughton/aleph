// Where the harness's two processes live (AL-003 `webServer` block).
//
// Owned here rather than inline in `playwright.config.ts` because the specs
// need the *backend* origin too: the OIDC flow (`/auth/login` -> Keycloak ->
// `/auth/callback`) is served by the backend at unversioned paths the vite dev
// proxy does not forward, so sign-in drives that origin directly. One source
// keeps the config's boot commands and the specs' sign-in from drifting apart.

/** Fixed: the vite dev proxy targets this port, so the backend cannot move. */
export const BACKEND_PORT = 8000;
export const FRONTEND_PORT = 5300;

export const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
export const FRONTEND_URL = `http://127.0.0.1:${FRONTEND_PORT}`;
