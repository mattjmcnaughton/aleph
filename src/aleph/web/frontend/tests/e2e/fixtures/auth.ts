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
 *
 * Also the account W24-W26 (`journeys/w2[4-6].spec.ts`) run the flashcards
 * suite's shared-account journeys as (Phase 3 TDD D15, §11): it is otherwise
 * idle (W1's own admin sub-test opens a lesson but never completes one, so it
 * drafts and reviews nothing), which is what makes it safe to reuse rather
 * than adding a fourth realm user this document has no license to add.
 */
export const ADMIN_USER: RealmUser = { username: "admin-dev", password: "admin-dev" };

/**
 * A learner seeded only to give one flashcards journey (W27) an account no
 * other spec ever touches (Phase 3 TDD D15, §11's E2E section). Its unverified
 * email plays no role here — `auth.py` only ever drops an unverified email
 * from identity, it never blocks sign-in or gates a feature — this is simply
 * the one realm user besides `dev`/`admin-dev` already checked in
 * (`docker/keycloak/aleph-realm.json`, otherwise used only by the backend's
 * own `tests/integration/test_auth_keycloak.py`), so reusing it needs no new
 * realm user either.
 *
 * Why W27 alone needs a *fully* idle account, when W24-W26 can share one:
 * `select_daily_queue` is blind to `satisfied` (Phase 3 TDD §5.1), so a card
 * competes for the day's ten slots whether or not it has already been graded
 * — residue does not shrink a shared account's candidate pool, it only grows
 * it. W25/W26 can be written to read whatever the day's actual queue holds
 * (see their own headers), but W27 has exactly one card and needs it to
 * *land* in the selected ten at all — a property no assertion can rescue
 * once the account's total candidate count exceeds the cap.
 */
export const UNVERIFIED_USER: RealmUser = {
  username: "unverified-dev",
  password: "unverified-dev",
};

const AUTH_DIR = path.join(import.meta.dirname, "..", ".auth");

/** Storage state written by `auth.setup.ts`, replayed by the journey specs. */
export const DEV_STORAGE_STATE = path.join(AUTH_DIR, "dev.json");
export const ADMIN_STORAGE_STATE = path.join(AUTH_DIR, "admin.json");
export const UNVERIFIED_STORAGE_STATE = path.join(AUTH_DIR, "unverified.json");

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
