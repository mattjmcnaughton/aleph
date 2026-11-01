import { expect, test } from "@playwright/test";

// Real browser/CSS, fixture API responses. Database eligibility is independently
// exercised by tests/integration/test_milestones.py, not simulated by this test.
for (const days of [100, 500, 1000]) {
  for (const reduced of [false, true]) {
    test(`constellation ${days}, reduced motion ${reduced}`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.emulateMedia({ reducedMotion: reduced ? "reduce" : "no-preference" });
      let completed = false;
      const lessonId = "11111111-1111-4111-8111-111111111111";
      const pathId = "22222222-2222-4222-8222-222222222222";
      await page.route("**/api/v1/**", async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        const lesson = {
          id: lessonId,
          path_id: pathId,
          title: "Reading an EXPLAIN plan",
          position_in_path: 1,
          position_in_unit: 1,
          generation_state: "generated",
          unlock_state: completed ? "complete" : "available",
          read_passage: "Start with the scan: which rows will it read?",
          quick_check: null,
          attempt: null,
          generation_error: null,
        };
        let json: unknown;
        if (pathname.endsWith("/auth/session"))
          json = {
            authenticated: true,
            provider: "keycloak",
            user: {
              id: "learner",
              username: "learner",
              display_name: "Learner",
              email: null,
              is_admin: false,
              model_allowlist: [],
              feature_flags: {},
            },
          };
        else if (pathname.endsWith("/complete")) {
          const milestones = completed ? [] : [{ metric: "active_days", days }];
          completed = true;
          json = { id: lessonId, unlock_state: "complete", path_completion: null, milestones };
        } else if (pathname.includes("/lessons/")) json = lesson;
        else if (pathname.endsWith("/paths")) json = { paths: [] };
        else if (pathname.includes("/paths/"))
          json = {
            id: pathId,
            title: "SQL performance",
            topic: "SQL performance",
            level: "some_experience",
            status: "ready",
            guidance: null,
            teaching_style: null,
            units: [{ id: "unit", title: "Indexes", lessons: [lesson] }],
            progress: {
              total_lessons: 1,
              completed_lessons: completed ? 1 : 0,
              generated_lessons: 1,
            },
          };
        else return route.fulfill({ status: 404, body: "" });
        await route.fulfill({ json });
      });
      await page.goto(`/lessons/${lessonId}`);
      await page.getByRole("button", { name: "Mark complete", exact: true }).click();
      const celebration = page.getByTestId("milestone-celebration");
      await expect(celebration).toContainText(`${days.toLocaleString()} total Active days`);
      await expect(celebration.locator(".milestone-star")).toHaveCount(
        days === 100 ? 14 : days === 500 ? 26 : 40,
      );
      if (reduced) {
        await expect(celebration.locator(".milestone-traveler")).toHaveCSS("display", "none");
        await expect(celebration.locator(".milestone-ripple")).toHaveCSS("display", "none");
        await expect(celebration.locator(".milestone-connection")).toHaveCSS(
          "animation-name",
          "none",
        );
        await expect(celebration.locator(".milestone-connection")).toHaveCSS(
          "stroke-dashoffset",
          "0px",
        );
      } else {
        await expect(celebration.locator(".milestone-connection")).toHaveCSS(
          "animation-name",
          "milestone-connect",
        );
        await expect(celebration.locator(".milestone-traveler")).toHaveCSS(
          "animation-iteration-count",
          "1",
        );
      }
      await page.reload();
      await expect(page.getByTestId("lesson-completed")).toBeVisible();
      await expect(page.getByTestId("milestone-celebration")).toHaveCount(0);
    });
  }
}
