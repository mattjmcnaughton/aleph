// Signing in for real (TDD §12: "compose Keycloak for auth flows").
//
// No stubbed session and no injected cookie: the journeys drive the same OIDC
// authorization-code flow a learner does — `/auth/login` on the backend origin,
// the realm's login form, `/auth/callback` — because that flow is what mints the
// first-party session cookie every `/api/v1/*` call depends on.
//
// Ports, not origins, separate the two servers (`127.0.0.1:8000` backend,
// `127.0.0.1:5300` frontend). Cookies ignore ports, so the session cookie the
// callback sets on `127.0.0.1` is sent by the SPA on 5300 as well — which is
// exactly how `just dev` works locally.

import path from "node:path";
import { type Page, expect } from "@playwright/test";
import { BACKEND_URL } from "../servers";

/** A user seeded in the checked-in dev realm (docker/keycloak/aleph-realm.json). */
export interface RealmUser {
  username: string;
  password: string;
}

/** The regular learner every journey runs as (verified, non-admin). */
export const DEV_USER: RealmUser = { username: "dev", password: "dev" };

/**
 * The admin learner (email domain in `ADMIN_EMAIL_DOMAINS`), whose session
 * carries the model-picker allowlist — in e2e that is the stub id alone
 * (`scripts/e2e_backend.py`).
 */
export const ADMIN_USER: RealmUser = { username: "admin-dev", password: "admin-dev" };

const AUTH_DIR = path.join(import.meta.dirname, "..", ".auth");

/** Storage state written by `auth.setup.ts`, replayed by the journey specs. */
export const DEV_STORAGE_STATE = path.join(AUTH_DIR, "dev.json");
export const ADMIN_STORAGE_STATE = path.join(AUTH_DIR, "admin.json");

/**
 * Drive the real sign-in and land on the signed-in home ("Your paths").
 *
 * Idempotent about the login form: Keycloak keeps its own SSO session, so a
 * second sign-in in the same browser context skips straight past the form.
 */
export async function signIn(page: Page, user: RealmUser = DEV_USER): Promise<void> {
  await page.goto(`${BACKEND_URL}/auth/login`);

  // Where the login redirect lands is genuinely two-valued, so wait for whichever
  // arrives rather than sampling the page mid-flight: normally Keycloak's form,
  // but with the realm's own SSO session still live the flow runs straight
  // through to the callback and no form is ever shown. (A bare `isVisible()`
  // here is a snapshot of a page that may still be redirecting — it reads
  // "no form" for a form that is one hop away, and the fill then fails.)
  const username = page.locator("#username");
  const landing = await Promise.race([
    username
      .waitFor({ state: "visible" })
      .then(() => "login-form" as const)
      .catch(() => "unresolved" as const),
    page
      .waitForURL(signedInUrl)
      .then(() => "signed-in" as const)
      .catch(() => "unresolved" as const),
  ]);

  if (landing === "login-form") {
    await username.fill(user.username);
    await page.locator("#password").fill(user.password);
    await page.locator("#kc-login").click();
  }

  // The callback redirects to "/" on the *backend* origin (which serves no SPA
  // in dev — only the cookie matters here), so wait for the flow to leave
  // Keycloak before switching to the frontend.
  await page.waitForURL(signedInUrl);

  await page.goto("/");
  await expect(page.getByTestId("paths-switcher")).toBeVisible();
}

/** Past Keycloak and back on the backend origin: the session cookie is set. */
function signedInUrl(url: URL): boolean {
  return url.origin === BACKEND_URL && !url.pathname.startsWith("/auth");
}
