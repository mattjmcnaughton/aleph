import { createECDH, randomBytes } from "node:crypto";
import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import { startPath, uniqueTopic } from "../fixtures/journey";
import { BACKEND_URL } from "../servers";

test.use({ storageState: DEV_STORAGE_STATE, permissions: ["notifications"] });

test(
  "W38 tap enables a Device; fake Path ready is displayed and opens its path",
  { tag: "@w38" },
  async ({ page, context, request }) => {
    const ecdh = createECDH("prime256v1");
    ecdh.generateKeys();
    const subscription = {
      endpoint: `https://fcm.googleapis.com/fcm/send/e2e-${randomBytes(12).toString("hex")}`,
      keys: {
        p256dh: ecdh.getPublicKey().toString("base64url"),
        auth: randomBytes(16).toString("base64url"),
      },
    };
    // Only browser-test initialization replaces native APIs. No app override or
    // production flag exists, and no real push service is contacted.
    await page.addInitScript((json) => {
      Object.defineProperty(Notification, "permission", {
        get: () => sessionStorage.getItem("e2e:permission") ?? "default",
      });
      Notification.requestPermission = async () => {
        sessionStorage.setItem("e2e:permission", "granted");
        return "granted";
      };
      const sub = {
        toJSON: () => json,
        unsubscribe: async () => {
          sessionStorage.removeItem("e2e:subscription");
          return true;
        },
      };
      PushManager.prototype.getSubscription = async () =>
        sessionStorage.getItem("e2e:subscription") ? (sub as unknown as PushSubscription) : null;
      PushManager.prototype.subscribe = async () => {
        sessionStorage.setItem("e2e:subscription", "1");
        return sub as unknown as PushSubscription;
      };
    }, subscription);
    await page.goto("/");
    // Disposable isolated test database: remove this test learner's paths so this
    // exercises the first-path moment, not a synthetic application event.
    const paths = await (await page.request.get("/api/v1/paths")).json();
    for (const path of paths.paths) await page.request.delete(`/api/v1/paths/${path.id}`);
    await page.reload();
    expect(await page.evaluate(() => sessionStorage.getItem("e2e:permission"))).toBeNull();
    await startPath(page, uniqueTopic("First push path"), "new_to_it");
    const prompt = page.getByRole("region", { name: "Path ready Notifications" });
    await expect(prompt).toBeVisible();
    const registered = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/devices") && response.request().method() === "POST",
    );
    await prompt.getByRole("button", { name: "Yes", exact: true }).tap();
    expect((await registered).status()).toBe(200);
    const device = await (await registered).json();
    await expect(prompt).toHaveCount(0);
    await startPath(page, uniqueTopic("Push target"), "new_to_it");
    await page.waitForURL(/\/paths\//);
    const target = new URL(page.url()).pathname;
    let payload: { notification: { tag: string; navigate: string } } | undefined;
    await expect
      .poll(async () => {
        const pushes = await (await request.get(`${BACKEND_URL}/__e2e__/pushes`)).json();
        payload = pushes.find(
          (push: { device_id: string; payload: { notification: { navigate: string } } }) =>
            push.device_id === device.id &&
            new URL(push.payload.notification.navigate).pathname === target,
        )?.payload;
        return Boolean(payload);
      })
      .toBe(true);
    const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
    await worker.evaluate((msg) => {
      self.dispatchEvent(new PushEvent("push", { data: JSON.stringify(msg) }));
    }, payload);
    await expect
      .poll(() =>
        worker.evaluate(
          async () =>
            (await (self as unknown as ServiceWorkerGlobalScope).registration.getNotifications())
              .length,
        ),
      )
      .toBe(1);
    await page.goto("/");
    await worker.evaluate(async () => {
      const [notification] = await (
        self as unknown as ServiceWorkerGlobalScope
      ).registration.getNotifications();
      self.dispatchEvent(new NotificationEvent("notificationclick", { notification }));
    });
    await page.waitForURL(`**${target}`);
    await expect(page.getByTestId("path-rail")).toBeVisible();
    const removed = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/v1/devices/${device.id}`) &&
        response.request().method() === "DELETE",
    );
    await page.getByRole("button", { name: "Sign out" }).tap();
    expect((await removed).status()).toBe(204);
    await page.waitForURL("**/login");
    expect(await page.evaluate(() => sessionStorage.getItem("e2e:subscription"))).toBeNull();
  },
);
