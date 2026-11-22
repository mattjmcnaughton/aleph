// New path, drafted from an idea (CONTEXT.md: *Idea*, *Path draft*, *Interview*).
//
// Idea -> Aleph's draft -> interview answers -> redraft -> Use this draft ->
// Build my path -> the path. Against the stub backend: the stub's draft names
// the idea's first words, and a redraft adds one guidance line per answer and
// tightens the topic, so the changed markers are deterministic. Nothing is
// created until the learner submits the ordinary form (Constitution §2).

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import { GENERATION_TIMEOUT, pathIdFromUrl, uniqueTopic } from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("New path from an idea", { tag: "@path-draft" }, () => {
  test("an idea, sharpened by an interview, becomes the path the learner builds", async ({
    page,
  }) => {
    // The idea's first eight words become the stub's topic, so the unique
    // stamp sits inside them.
    const subject = uniqueTopic("Sourdough");
    const idea = `${subject} bread baking at home for complete beginners please.`;

    await page.goto("/new");
    await page.getByRole("textbox", { name: "Start from an idea" }).fill(idea);
    await page.getByRole("button", { name: "Draft it for me" }).click();

    // --- Aleph's draft -------------------------------------------------------
    const draft = page.getByTestId("path-draft");
    await expect(draft).toBeVisible({ timeout: GENERATION_TIMEOUT });
    await expect(draft).toContainText("Aleph's draft");
    await expect(draft).toContainText(subject);
    const firstLines = await draft.getByTestId("guidance-line").count();

    // --- the interview: one question at a time ---------------------------------
    await page.getByRole("button", { name: "Sharpen it: ask me a few questions" }).click();
    const interview = page.getByTestId("draft-interview");
    await expect(interview).toContainText("Question 1 of 4", { timeout: GENERATION_TIMEOUT });
    // Options are sr-only inputs in their labels: the label is what is tapped.
    await interview.locator("label", { hasText: "Work" }).click();
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(interview).toContainText("Question 2 of 4");
    await page
      .getByRole("textbox", { name: "Or say it in your own words…" })
      .fill("I bake yeasted bread already");
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await interview.locator("label", { hasText: "Short, about 8 lessons" }).click();
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(interview).toContainText("Question 4 of 4");
    await interview.locator("label", { hasText: "History" }).click();
    await interview.locator("label", { hasText: "Tooling" }).click();
    await page.getByRole("button", { name: "Redraft", exact: true }).click();

    // --- the sharpened draft marks what the answers changed --------------------
    await expect(draft).toContainText("Aleph's draft, sharpened", { timeout: GENERATION_TIMEOUT });
    await expect(draft).toContainText("Redrafted from your idea and 4 answers.");
    await expect(draft.getByTestId("topic-changed")).toHaveText("From your answers");
    await expect(draft.locator('[data-testid="guidance-line"][data-changed="true"]')).toHaveCount(
      4,
    );
    await expect(draft.getByTestId("guidance-line")).toHaveCount(firstLines + 4);
    await expect(draft).toContainText("I bake yeasted bread already");
    await expect(draft).toContainText("History; Tooling");

    // --- Use this draft fills the form; nothing is created yet ----------------
    await page.getByRole("button", { name: "Use this draft" }).click();
    await expect(page.getByTestId("draft-filled-notice")).toBeVisible();
    const topic = page.locator("#onboarding-topic");
    await expect(topic).toHaveValue(`${subject} bread baking at home for complete, focused`);
    const draftedTopic = await topic.inputValue();
    await expect(page.locator("#onboarding-guidance")).toHaveValue(/I bake yeasted bread already/);
    await expect(page.locator('input[name="level"][value="some_experience"]')).toBeChecked();

    // --- Build my path: the ordinary create, with the drafted inputs ----------
    await page.getByRole("button", { name: "Build my path" }).click();
    await page.waitForURL(/\/paths\//, { timeout: GENERATION_TIMEOUT });
    await expect(page.getByTestId("path-rail")).toBeVisible({ timeout: GENERATION_TIMEOUT });
    const detail = await page.request.get(`/api/v1/paths/${pathIdFromUrl(page.url())}`);
    expect(detail.ok()).toBe(true);
    expect(await detail.json()).toMatchObject({
      topic: draftedTopic,
      level: "some_experience",
      teaching_style: "Use concrete, everyday examples.",
    });
  });
});
