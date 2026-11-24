// PP3 — Copy a shared path (Public profiles TDD §9).
//
// The owner (admin-dev) turns their profile on and shares a path. An anonymous
// visitor opens it, signs in through the typed copy intent as `dev`, lands on
// New path prefilled with the owner's inputs, submits, and the new path drafts
// its own outline with the credit line.

import { type Browser, type Page, expect, test } from "@playwright/test";
import { ADMIN_STORAGE_STATE, DEV_USER } from "../fixtures/auth";
import { GENERATION_TIMEOUT, createPath, uniqueTopic } from "../fixtures/journey";
import { BACKEND_URL, FRONTEND_URL } from "../servers";

// Owner writes publish data, so they are Origin-checked (SR 5).
const SAME_ORIGIN = { Origin: FRONTEND_URL };

/** The owner's Handle: permanent, so reuse it on a database that has one. */
async function ensureProfileOn(page: Page): Promise<string> {
  const current = await page.request.get("/api/v1/account/public-profile");
  expect(current.ok()).toBe(true);
  const existing = (await current.json()).handle as string | null;
  const handle = existing ?? `pp3-${Date.now().toString(36)}`;
  const put = await page.request.put("/api/v1/account/public-profile", {
    headers: SAME_ORIGIN,
    data: { handle, profile_public: true, profile_show_activity: true },
  });
  expect(put.ok()).toBe(true);
  return handle;
}

async function anonymousPage(browser: Browser): Promise<Page> {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  return context.newPage();
}

test.describe("PP3 copy a shared path", { tag: "@pp3" }, () => {
  test("a visitor signs in from a shared path and gets their own drafting copy", async ({
    browser,
  }) => {
    // --- the owner shares a path ------------------------------------------------
    const ownerContext = await browser.newContext({ storageState: ADMIN_STORAGE_STATE });
    const owner = await ownerContext.newPage();
    const topic = uniqueTopic("Shared Rust ownership");
    const pathId = await createPath(owner, topic, "some_experience");
    const handle = await ensureProfileOn(owner);
    const share = await owner.request.put(`/api/v1/paths/${pathId}/shared`, {
      headers: SAME_ORIGIN,
      data: { shared: true },
    });
    expect(share.ok()).toBe(true);
    const ownerName = (await (await owner.request.get("/api/v1/auth/session")).json()).user
      .display_name as string;
    await ownerContext.close();

    // --- an anonymous visitor reads the profile and the path -----------------------
    const visitor = await anonymousPage(browser);
    const profile = await visitor.request.get(`/api/v1/public/profiles/${handle}`);
    expect(profile.status()).toBe(200);
    expect(profile.headers()["cache-control"]).toBe("no-store");
    expect(profile.headers()["x-robots-tag"]).toBe("noindex");
    expect(profile.headers()["set-cookie"]).toBeUndefined();

    await visitor.goto(`/@${handle}`);
    await expect(visitor.getByTestId("public-header")).toBeVisible();
    await visitor.getByTestId("public-path-row").filter({ hasText: topic }).click();
    await expect(visitor).toHaveURL(new RegExp(`/@${handle}/paths/${pathId}$`));
    const signIn = visitor.getByRole("link", { name: "Sign in to start this path" });
    await expect(signIn).toHaveAttribute("href", `/auth/login?copy=path:${handle}:${pathId}`);

    // --- sign in as another learner through the copy intent --------------------------
    // The harness's realm registers the backend's callback, not the SPA's port
    // (fixtures/auth.ts), so the flow runs on the backend origin; the callback's
    // redirect is what the intent is for, then the SPA opens that same URL.
    await visitor.goto(`${BACKEND_URL}/auth/login?copy=path:${handle}:${pathId}`);
    await visitor.locator("#username").fill(DEV_USER.username);
    await visitor.locator("#password").fill(DEV_USER.password);
    await visitor.locator("#kc-login").click();
    const destination = `/new?from=${handle}/${pathId}`;
    await visitor.waitForURL(`${BACKEND_URL}${destination}`);

    // --- New path, prefilled -----------------------------------------------------------
    await visitor.goto(destination);
    await expect(visitor.getByTestId("copy-from-notice")).toContainText(ownerName);
    await expect(visitor.locator("#onboarding-topic")).toHaveValue(topic);
    await visitor.getByRole("button", { name: "Build my path" }).click();

    // --- the copy drafts its own outline, credited ------------------------------------
    await visitor.waitForURL(/\/paths\/[0-9a-f-]{36}/, { timeout: GENERATION_TIMEOUT });
    const copyId = visitor.url().match(/\/paths\/([0-9a-f-]{36})/)?.[1];
    expect(copyId).toBeTruthy();
    expect(copyId).not.toBe(pathId);
    await expect(visitor.getByTestId("copy-credit")).toContainText(ownerName);
    const detail = await (await visitor.request.get(`/api/v1/paths/${copyId}`)).json();
    expect(detail).toMatchObject({
      topic,
      level: "some_experience",
      source: { handle, path_id: pathId },
    });
    await visitor.context().close();
  });
});
