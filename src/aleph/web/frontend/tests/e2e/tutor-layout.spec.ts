import { expect, test } from "@playwright/test";

// Real rendering with fixture API responses: no account, database or model needed.
test("Tutor controls stay reachable while reading a long conversation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const lesson = {
    id: "11111111-1111-4111-8111-111111111111",
    path_id: "22222222-2222-4222-8222-222222222222",
    title: "Generic constraints for reusable functions and collection types",
    position_in_path: 1,
    position_in_unit: 1,
    generation_state: "generated",
    unlock_state: "available",
    read_passage:
      "## Keeping generic functions useful\n\nA constraint describes the minimum shape a type must have.\n\n".repeat(
        15,
      ),
    quick_check: {
      stem: "What does a constraint guarantee?",
      options: ["A shape", "A value", "A copy"],
    },
    attempt: null,
    generation_error: null,
  };
  const messages = Array.from({ length: 12 }, (_, i) => ({
    id: `message-${i}`,
    role: i % 2 ? "tutor" : "learner",
    content:
      i % 2
        ? "A constraint keeps flexibility **without losing type safety**.\n\nStrings and arrays have a length; numbers do not.\n\n".repeat(
            3,
          )
        : "Why not just use any?",
    lesson_id: lesson.id,
    lesson_title: lesson.title,
    tutor_check: null,
    created_at: "2026-10-22T12:00:00Z",
  }));
  await page.route("**/api/v1/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    let json: unknown = {};
    if (pathname.endsWith("/auth/session")) {
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
          feature_flags: { tutor: true },
        },
      };
    } else if (pathname.endsWith("/conversation")) json = { messages };
    else if (pathname.includes("/lessons/")) json = lesson;
    else if (pathname.endsWith("/paths")) json = { paths: [] };
    else if (pathname.includes("/paths/"))
      json = {
        id: lesson.path_id,
        title: "TypeScript",
        topic: "TypeScript",
        level: "some_experience",
        status: "ready",
        guidance: null,
        teaching_style: null,
        units: [{ id: "unit", title: "Functions and types", lessons: [lesson] }],
        progress: { total_lessons: 1, completed_lessons: 0, generated_lessons: 1 },
      };
    else return route.fulfill({ status: 404, body: "" });
    await route.fulfill({ json });
  });
  await page.goto(`/lessons/${lesson.id}`);
  await page.getByTestId("lesson-read-passage").waitFor();
  await page.evaluate(() => window.scrollTo(0, 500));
  const readingPosition = await page.evaluate(() => scrollY);
  await page.getByRole("button", { name: "Tutor", exact: true }).click();
  const close = page.getByRole("button", { name: "Close the tutor" });
  const input = page.getByRole("textbox", { name: "Ask about this lesson" });
  const thread = page.getByTestId("tutor-rail-messages");
  await page.getByTestId("tutor-rail-message").last().waitFor();
  await expect(close).toBeInViewport({ ratio: 1 });
  const closeBox = await close.boundingBox();
  expect(closeBox?.width).toBeGreaterThanOrEqual(44);
  expect(closeBox?.height).toBeGreaterThanOrEqual(44);

  await thread.evaluate((element) => {
    element.scrollTop = 0;
  });
  await expect(close).toBeInViewport({ ratio: 1 });
  await close.click();
  expect(await page.evaluate(() => scrollY)).toBe(readingPosition);
  await page.getByRole("button", { name: "Tutor", exact: true }).click();
  await expect(page.getByTestId("tutor-rail-message")).toHaveCount(12);

  // A short screen plus a multi-line draft must not push either control out.
  await page.setViewportSize({ width: 320, height: 568 });
  await input.fill("Explain the difference in detail.\n".repeat(15));
  await expect(close).toBeInViewport({ ratio: 1 });
  await expect(input).toBeInViewport({ ratio: 1 });

  // Model the keyboard's independent visual viewport, including browser pan.
  // Resizing the page alone would not catch a layout-viewport-only solution.
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
    expect(box).not.toBeNull();
    expect(box?.y).toBeGreaterThanOrEqual(90);
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(330);
  }
  await page.getByLabel("Suggested questions", { exact: true }).click();
  for (const control of [close, input]) {
    const box = await control.boundingBox();
    expect(box?.y).toBeGreaterThanOrEqual(90);
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(330);
  }
  for (const suggestion of await page.getByTestId("tutor-rail-suggestion").all()) {
    await suggestion.scrollIntoViewIfNeeded();
    await suggestion.click({ trial: true, timeout: 2000 });
    const box = await suggestion.boundingBox();
    expect(box?.y).toBeGreaterThanOrEqual(90);
  }
  await close.click({ trial: true });
  await page.getByLabel("Suggested questions", { exact: true }).click();
  await page.getByLabel("Tutor options", { exact: true }).click();
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await expect(close).toBeInViewport({ ratio: 1 });
  await page.getByRole("button", { name: "Keep", exact: true }).click();
  await expect(page.getByTestId("tutor-rail-message")).toHaveCount(12);
  await close.click();
  await expect(page.getByTestId("tutor-rail")).toHaveCount(0);
});
