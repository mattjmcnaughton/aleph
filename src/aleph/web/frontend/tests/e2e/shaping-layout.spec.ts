import { expect, test } from "@playwright/test";

// Real layout with fixture API responses; shaping mutations are covered by W17–W21.
test("Shaping controls stay reachable through conversation, suggestions and history", async ({
  page,
}) => {
  const pathId = "22222222-2222-4222-8222-222222222222";
  const messages = Array.from({ length: 12 }, (_, index) => ({
    id: `message-${index}`,
    role: index % 2 ? "tutor" : "learner",
    content:
      index % 2
        ? "We can add practice **without changing completed lessons**.\n\n".repeat(5)
        : "Add practice with generic constraints.",
    proposal: null,
    created_at: "2026-10-30T12:00:00Z",
  }));
  await page.route("**/api/v1/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    let json: unknown;
    if (pathname.endsWith("/auth/session"))
      json = {
        authenticated: true,
        provider: "keycloak",
        user: {
          id: "learner",
          username: "learner",
          display_name: "Learner",
          email: "learner@example.com",
          is_admin: false,
          model_allowlist: [],
          feature_flags: { shaping: true },
        },
      };
    else if (pathname.endsWith("/shaping/conversation")) json = { messages };
    else if (pathname.endsWith("/changes")) json = { changes: [] };
    else if (pathname.endsWith("/paths")) json = { paths: [] };
    else if (pathname.endsWith(`/paths/${pathId}`))
      json = {
        id: pathId,
        title: "TypeScript generic constraints for reusable functions and collection types",
        topic: "TypeScript",
        level: "some_experience",
        status: "ready",
        guidance: null,
        teaching_style: null,
        units: Array.from({ length: 12 }, (_, index) => ({
          id: `unit-${index}`,
          title: `Functions and types ${index + 1}`,
          lessons: [
            {
              id: `lesson-${index}`,
              title: `Generic constraints ${index + 1}`,
              position_in_path: index + 1,
              position_in_unit: 1,
              generation_state: "generated",
              unlock_state: index ? "locked" : "available",
            },
          ],
        })),
        progress: { total_lessons: 12, completed_lessons: 0, generated_lessons: 12 },
      };
    else return route.fulfill({ status: 404, body: "" });
    await route.fulfill({ json });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/paths/${pathId}`);
  await page.getByTestId("shaping-rail-mark").waitFor();
  await page.evaluate(() => window.scrollTo(0, 500));
  const readingPosition = await page.evaluate(() => scrollY);
  await page.getByTestId("shaping-rail-mark").click();
  const close = page.getByRole("button", { name: "Close shaping", exact: true });
  const input = page.getByRole("textbox", { name: "Ask to change this path" });
  await expect(page.getByTestId("shaping-rail-message")).toHaveCount(12);
  const closeBox = await close.boundingBox();
  expect(closeBox?.width).toBeGreaterThanOrEqual(44);
  expect(closeBox?.height).toBeGreaterThanOrEqual(44);
  await close.click();
  expect(await page.evaluate(() => scrollY)).toBe(readingPosition);
  await page.getByTestId("shaping-rail-mark").click();
  await expect(page.getByTestId("shaping-rail-message")).toHaveCount(12);

  await page.setViewportSize({ width: 320, height: 568 });
  await input.fill("Add practice without changing completed lessons.\n".repeat(15));
  await expect(close).toBeInViewport({ ratio: 1 });
  await expect(input).toBeInViewport({ ratio: 1 });
  await page.evaluate(() => {
    const viewport = window.visualViewport;
    if (!viewport) throw new Error("VisualViewport unavailable");
    Object.defineProperties(viewport, {
      height: { configurable: true, value: 240 },
      offsetTop: { configurable: true, value: 90 },
    });
    viewport.dispatchEvent(new Event("resize"));
    viewport.dispatchEvent(new Event("scroll"));
  });
  for (const control of [close, input]) {
    const box = await control.boundingBox();
    expect(box?.y).toBeGreaterThanOrEqual(90);
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(330);
  }
  await page.getByLabel("Suggested changes", { exact: true }).click();
  await expect(page.getByTestId("shaping-rail-suggestion")).toHaveCount(4);
  for (const control of [close, input]) {
    const box = await control.boundingBox();
    expect(box?.y).toBeGreaterThanOrEqual(90);
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(330);
  }
  for (const suggestion of await page.getByTestId("shaping-rail-suggestion").all()) {
    await suggestion.scrollIntoViewIfNeeded();
    await suggestion.click({ trial: true });
  }
  await page.getByRole("button", { name: "Add practice on…", exact: true }).click();
  await expect(input).toHaveValue("Add practice on ");
  await expect(input).toBeFocused();
  await input.pressSequentially("generic constraints");
  await expect(input).toHaveValue("Add practice on generic constraints");
  await expect(page.getByTestId("shaping-rail-suggestion")).toHaveCount(0);
  await close.click({ trial: true });
  await page.getByLabel("Shaping options", { exact: true }).click();
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  for (const control of [close, input]) {
    const box = await control.boundingBox();
    expect(box?.y).toBeGreaterThanOrEqual(90);
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(330);
  }
  await close.click({ trial: true });
  await page.getByRole("button", { name: "Keep", exact: true }).click();
  await expect(page.getByTestId("shaping-rail-message")).toHaveCount(12);
  await page.getByLabel("Shaping options", { exact: true }).click();
  await page.getByRole("button", { name: "Changes", exact: true }).click();
  const historyClose = page.getByRole("button", { name: "Close change history" });
  await expect(page.getByTestId("shaping-rail-history-empty")).toBeVisible();
  const historyCloseBox = await historyClose.boundingBox();
  expect(historyCloseBox?.width).toBeGreaterThanOrEqual(44);
  expect(historyCloseBox?.height).toBeGreaterThanOrEqual(44);
  expect(historyCloseBox?.y).toBeGreaterThanOrEqual(90);
  expect((historyCloseBox?.y ?? 0) + (historyCloseBox?.height ?? 0)).toBeLessThanOrEqual(330);
  await historyClose.click();
  await expect(input).toHaveValue("Add practice on generic constraints");
  await close.click();
  expect(await page.evaluate(() => scrollY)).toBe(readingPosition);

  // The shared shell must still dock on desktop, independently of page scroll.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.reload();
  await page.getByTestId("shaping-rail-mark").click();
  const column = await page.getByTestId("shaping-rail-column").boundingBox();
  expect(column?.width).toBe(400);
  await page.evaluate(() => window.scrollTo(0, 700));
  await expect(close).toBeInViewport({ ratio: 1 });
  await expect(input).toBeInViewport({ ratio: 1 });
});
