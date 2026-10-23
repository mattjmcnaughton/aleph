import { test, expect, type Page } from "@playwright/test";
import { BACKEND_URL } from "../servers";

// W41: account-wide conversation/focus/dismiss/recovery. W42: edited creation
// from recommendations. W43: independent dark/analyst flag boundaries.
// Each journey gets a fresh synthetic account in the disposable stub database;
// fixed stub Topics and account-wide clear must never share the OIDC dev account.
async function freshAccount(page: Page, flags = { learning_tutor: true, analyst: true }) {
  const response = await page.request.post(`${BACKEND_URL}/__e2e__/learning-account`, {
    data: flags,
  });
  expect(response.ok()).toBe(true);
  await page.goto("/tutor");
}
async function send(page: Page, text: string) {
  await page.getByLabel("Message your Tutor").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

test("@w41 Learning conversation persists whole turns, dismisses and restores", async ({
  page,
}) => {
  let posts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/learning-tutor/operations"))
      posts++;
  });
  await freshAccount(page);
  await expect(page.getByLabel("Learning focus")).toBeVisible();
  expect(posts).toBe(0);
  await page.getByRole("button", { name: "Find directions to explore", exact: true }).click();
  await expect(page.getByTestId("learning-needs_context")).toBeVisible();
  await page.getByLabel("Learning focus").fill("[lt-path] Electricity markets");
  await page.getByRole("button", { name: "Save focus" }).click();
  await expect(page.getByText(/Learning focus saved/)).toBeVisible();
  await send(page, "[lt-delay] [lt-path] Teach me electricity foundations");
  await expect(page.getByText(/Your Tutor is thinking/)).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("learning-turn")).toHaveCount(1, { timeout: 30_000 });
  await expect(page.getByTestId("learning-recommendation")).toHaveCount(1);
  expect(posts).toBe(2);
  await page.getByRole("button", { name: "Not interested" }).click();
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByTestId("learning-recommendation")).toHaveCount(1);
  await page.getByRole("link", { name: "Aleph home" }).click();
  await expect(page.getByTestId("learning-recommendation")).toHaveCount(1);
  await page.getByRole("link", { name: "Tutor", exact: true }).click();
  await page.getByRole("button", { name: "Clear Learning conversation" }).click();
  await page.getByRole("button", { name: "Confirm clear" }).click();
  await expect(page.getByTestId("learning-turn")).toHaveCount(0);
  await expect(page.getByTestId("learning-recommendation")).toHaveCount(0);
  await expect(page.getByLabel("Learning focus")).toHaveValue("[lt-path] Electricity markets");
});

test("@w42 recommendation discussion → edited Beat deployment; independent path acceptance", async ({
  page,
}) => {
  await freshAccount(page);
  await send(page, "[lt-beat] Follow energy developments");
  await expect(page.getByTestId("learning-recommendation")).toHaveCount(1, { timeout: 30_000 });
  await page.getByRole("link", { name: "Discuss", exact: true }).click();
  await expect(page.getByLabel("Message your Tutor")).toBeFocused();
  await send(page, "Why follow this rather than start another path?");
  await expect(page.getByTestId("learning-turn")).toHaveCount(2, { timeout: 30_000 });
  await page.getByRole("link", { name: "Explore Beat" }).click();
  await expect(page.getByLabel("Topic", { exact: true })).toHaveValue(
    "Synthetic energy developments",
  );
  await page.getByLabel("Topic", { exact: true }).fill("Edited energy policy");
  await page.getByLabel("Guidance", { exact: true }).fill("Policy, not stock prices");
  await page.getByLabel("Reports on").selectOption("3");
  await page.getByRole("button", { name: "Deploy analyst", exact: true }).click();
  await expect(page).toHaveURL(/\/beats\/[0-9a-f-]{36}/);
  const beatId = page.url().split("/").pop();
  const beat = await page.request.get(`${BACKEND_URL}/api/v1/beats/${beatId}`);
  expect(await beat.json()).toMatchObject({
    topic: "Edited energy policy",
    guidance: "Policy, not stock prices",
    anchor_weekday: 3,
  });
  await page.goto("/tutor");
  await expect(page.getByTestId("learning-recommendation")).toHaveCount(0);
  await send(page, "[lt-path] Learn the foundations next");
  await expect(page.getByRole("link", { name: "Explore path" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("link", { name: "Explore path" }).click();
  await page.getByLabel("Topic", { exact: true }).fill("Edited electricity foundations");
  await page.getByRole("button", { name: "Build my path" }).click();
  await expect(page).toHaveURL(/\/paths\/[0-9a-f-]{36}/, { timeout: 60_000 });
  await page.goto("/");
  await expect(page.getByTestId("learning-recommendation")).toHaveCount(0);
});

test("@w43 flag off leaves ordinary creation available; analyst off hides Beat doors", async ({
  page,
}) => {
  await freshAccount(page, { learning_tutor: false, analyst: true });
  await expect(page.getByText("Your account-wide Tutor isn't available.")).toBeVisible();
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Tutor", exact: true })).toHaveCount(0);
  await expect(page.getByTestId("learning-recommendations")).toHaveCount(0);
  await page.goto("/new?topic=Ordinary%20path");
  await expect(page.getByLabel("Topic", { exact: true })).toHaveValue("Ordinary path");
  await freshAccount(page, { learning_tutor: true, analyst: false });
  await send(page, "[lt-path] Learn electricity");
  await expect(page.getByRole("link", { name: "Explore path" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("link", { name: "Explore Beat" })).toHaveCount(0);
  await page.goto("/beats/new");
  await expect(page.getByTestId("beats-new-unavailable")).toBeVisible();
});
