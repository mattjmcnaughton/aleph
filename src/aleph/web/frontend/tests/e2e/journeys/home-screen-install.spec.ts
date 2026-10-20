import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";

// Chromium with Safari UA/display-mode overrides exercises our decisions, not
// iPhone installation, Safari rendering, splash screens or OIDC containment.
test.use({
  storageState: DEV_STORAGE_STATE,
  userAgent:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Version/26.0 Mobile/15E148 Safari/604.1",
});

test("install instructions dismiss across reloads and route returns", async ({ page }) => {
  await page.goto("/");
  const card = page.getByRole("region", { name: "Add aleph to your Home Screen" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Not now" }).click();
  await expect(card).toHaveCount(0);
  await page.goto("/new");
  await page.getByRole("link", { name: "Your paths", exact: true }).click();
  await expect(card).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("paths-switcher")).toBeVisible();
  await expect(card).toHaveCount(0);
  await page.evaluate(() => localStorage.removeItem("aleph.install.dismissedAt"));
  await page.reload();
  await expect(card).toBeVisible();
});

test("standalone excludes instructions and forms/errors keep an in-app exit", async ({ page }) => {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setSafeAreaInsetsOverride", {
    insets: { top: 47, bottom: 34, left: 0, right: 0 },
  });
  await page.addInitScript(() => {
    const original = window.matchMedia.bind(window);
    window.matchMedia = (query) =>
      query === "(display-mode: standalone)"
        ? Object.defineProperty(original(query), "matches", { value: true })
        : original(query);
  });
  await page.goto("/");
  await expect(page.getByTestId("paths-switcher")).toBeVisible();
  await expect(page.getByRole("region", { name: "Add aleph to your Home Screen" })).toHaveCount(0);
  const header = await page.getByRole("link", { name: "Aleph home", exact: true }).boundingBox();
  expect(header?.y).toBeGreaterThanOrEqual(47);
  expect(await page.evaluate(() => getComputedStyle(document.body).paddingBottom)).toBe("34px");
  for (const route of [
    "/new",
    "/beats/new",
    "/flow/new",
    "/flow/done",
    "/paths/00000000-0000-0000-0000-000000000001",
    "/lessons/00000000-0000-0000-0000-000000000001",
    "/beats/00000000-0000-0000-0000-000000000001",
    "/briefs/00000000-0000-0000-0000-000000000001",
  ]) {
    await page.goto(route);
    await expect(page.getByRole("link", { name: "Aleph home", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Aleph home", exact: true }).click();
    await expect(page.getByTestId("paths-switcher")).toBeVisible();
  }
  const registration = await page.evaluate(async () => {
    const registrations = await navigator.serviceWorker.getRegistrations();
    return registrations.map((worker) => ({
      scope: new URL(worker.scope).pathname,
      script: new URL(
        worker.active?.scriptURL ??
          worker.installing?.scriptURL ??
          worker.waiting?.scriptURL ??
          location.href,
      ).pathname,
    }));
  });
  expect(registration).toContainEqual({ scope: "/", script: "/sw.js" });
  expect(await page.evaluate(() => caches.keys())).toEqual([]);
});
