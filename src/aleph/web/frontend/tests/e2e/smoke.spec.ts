import { expect, test } from "@playwright/test";

// AL-003 e2e harness skeleton. One @smoke spec proving the Playwright harness is
// wired end to end: the browser (at the §12 phone viewport, 390x844) boots the
// stub backend + dev frontend, the SPA's session gate reaches the real API, and
// the sign-in surface renders. AL-090 adds the W1-W8 user-journey specs beside
// this file (tagged @w1..@w8) against the same harness + stub model.

test("@smoke renders the sign-in surface for an anonymous visitor", async ({ page }) => {
  // The root `beforeLoad` gate resolves the (anonymous) session against the real
  // API and redirects an unauthenticated visitor to /login.
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);

  await expect(page.getByRole("heading", { name: "Sign in to Aleph" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Continue with/ })).toBeVisible();
});

test("@smoke reports an anonymous session from the real API", async ({ request }) => {
  // Proxied through the vite dev server to the stub backend — the same call the
  // SPA's session gate makes. It must answer 200 (never 401) when signed out.
  const response = await request.get("/api/v1/auth/session");

  expect(response.ok(), await responseText(response)).toBe(true);
  await expect(response.json()).resolves.toMatchObject({ authenticated: false });
});

// Liveness/readiness probes are served by the backend at unversioned paths the
// vite dev proxy does not forward, so they only run when BASE_URL points the
// suite straight at a running deployment (prod smoke).
test.describe("backend probes", () => {
  test.skip(!process.env.BASE_URL, "health/readiness probes require BASE_URL");

  test("@smoke serves liveness and readiness", async ({ request }) => {
    const health = await request.get("/healthz");
    expect(health.ok(), await responseText(health)).toBe(true);
    await expect(health.json()).resolves.toEqual({ status: "ok" });

    const ready = await request.get("/readyz");
    expect(ready.ok(), await responseText(ready)).toBe(true);
    await expect(ready.json()).resolves.toEqual({ status: "ready" });
  });
});

async function responseText(response: { text(): Promise<string> }): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "<response body unavailable>";
  }
}
