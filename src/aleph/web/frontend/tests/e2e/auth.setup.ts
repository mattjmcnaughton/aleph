// The `setup` project: sign in once through the real OIDC flow and save the
// session as storage state the journey specs replay (`test.use({ storageState })`).
//
// Why not sign in per spec: the code flow costs a Keycloak round trip and proves
// nothing the W1-W8 journeys are about. It is still *proven* rather than faked —
// this file performs the real flow, and W2 (resume across sessions) signs in from
// scratch again, form and all.
//
// The `@smoke` specs deliberately do NOT use these states: they assert the
// signed-out gate.

import { test as setup } from "@playwright/test";
import {
  ADMIN_STORAGE_STATE,
  ADMIN_USER,
  DEV_STORAGE_STATE,
  DEV_USER,
  UNVERIFIED_STORAGE_STATE,
  UNVERIFIED_USER,
  signIn,
} from "./fixtures/auth";

setup("sign in as the dev learner", async ({ page }) => {
  await signIn(page, DEV_USER);
  await page.context().storageState({ path: DEV_STORAGE_STATE });
});

setup("sign in as the admin learner", async ({ page }) => {
  await signIn(page, ADMIN_USER);
  await page.context().storageState({ path: ADMIN_STORAGE_STATE });
});

// The flashcards journeys' isolated account (Phase 3 TDD D15, §11 — see
// `fixtures/auth.ts`'s own note on `UNVERIFIED_USER`): W27 alone runs as this
// learner, so its one kept card never competes for a shared account's ten
// review slots against anything another spec created.
setup("sign in as the unverified learner", async ({ page }) => {
  await signIn(page, UNVERIFIED_USER);
  await page.context().storageState({ path: UNVERIFIED_STORAGE_STATE });
});
