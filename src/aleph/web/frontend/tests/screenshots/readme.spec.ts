import { type Page, test } from "@playwright/test";
import { http, HttpResponse, getResponse } from "msw";
import { API_V1_BASE } from "../../src/lib/api";
import { handlers } from "../../src/mocks/handlers";
import { BRIEF_ID, HEALTHCARE_PATH_ID, LESSON_ID, demoUser, seedDemo } from "./demo-data";

// Captures the README screenshots into docs/screenshots/readme/. Run with
// `just screenshots`; rerun after a visible UI change and commit the PNGs.

const OUT = new URL("../../../../../../docs/screenshots/readme", import.meta.url).pathname;
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 860 };

const demoHandlers = [
  http.get(`${API_V1_BASE}/auth/session`, () =>
    HttpResponse.json({ authenticated: true, provider: "auth0", user: demoUser }),
  ),
  ...handlers,
];

async function serveDemo(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const body = req.postDataBuffer();
    // The handlers are registered as origin-relative paths; serve them as if the
    // page and the fakes share one origin.
    const request = new Request(`http://localhost${url.pathname}${url.search}`, {
      method: req.method(),
      headers: await req.allHeaders(),
      body: body && req.method() !== "GET" ? new Uint8Array(body) : undefined,
    });
    const response = await getResponse(demoHandlers, request, {
      baseUrl: "http://localhost",
    });
    if (!response) return route.fulfill({ status: 404, body: "" });
    await route.fulfill({
      status: response.status,
      headers: Object.fromEntries(response.headers),
      body: Buffer.from(await response.arrayBuffer()),
    });
  });
  // Keep the install prompt out of the shots.
  await page.addInitScript(() => {
    localStorage.setItem("aleph.install.dismissedAt", String(Date.now()));
  });
}

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(600);
}

async function shot(page: Page, name: string): Promise<void> {
  await settle(page);
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

test.beforeEach(async ({ page }) => {
  seedDemo();
  await serveDemo(page);
});

test("home", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto("/");
  await page.getByText("How US healthcare is paid for").first().waitFor();
  await shot(page, "home-phone");
  await page.setViewportSize(DESKTOP);
  await shot(page, "home-desktop");
});

test("new path", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto("/new");
  await page.getByRole("button", { name: "I'll fill in the form myself" }).click();
  await page.locator("#onboarding-topic").fill("How the Fed sets interest rates");
  await page.locator('label[for="level-new_to_it"]').click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "new-path-phone");
});

test("path view with a Proposal", async ({ page }) => {
  await page.setViewportSize(DESKTOP);
  await page.goto(`/paths/${HEALTHCARE_PATH_ID}`);
  await page.getByTestId("path-rail").waitFor();
  // Tall enough to reach the available and locked lessons below Unit 1.
  await page.setViewportSize({ width: DESKTOP.width, height: 1240 });
  await shot(page, "path-desktop");
  await page.setViewportSize(DESKTOP);
  await page.getByTestId("shaping-rail-mark").click();
  await page.getByTestId("shaping-rail").waitFor();
  await shot(page, "shape-desktop");
});

test("lesson and tutor", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto(`/lessons/${LESSON_ID}`);
  await page.getByTestId("lesson-read-passage").waitFor();
  await shot(page, "lesson-phone");
  await page.getByTestId("tutor-rail-mark").click();
  await page.getByTestId("tutor-rail").waitFor();
  await shot(page, "tutor-phone");
});

test("Brief", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto(`/briefs/${BRIEF_ID}`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await shot(page, "brief-phone");
});

test("Flow", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto("/flow/new");
  await page.getByTestId("flow-start").waitFor();
  await shot(page, "flow-phone");
});
