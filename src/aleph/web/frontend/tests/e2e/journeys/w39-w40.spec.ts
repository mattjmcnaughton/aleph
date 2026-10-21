import { createECDH, randomBytes } from "node:crypto";
import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import { expectMinTouchTarget, expectNoHorizontalOverflow } from "../fixtures/beats";
import { uniqueTopic } from "../fixtures/journey";
import { BACKEND_URL } from "../servers";

test.use({ storageState: DEV_STORAGE_STATE, permissions: ["notifications"] });

test.beforeEach(async ({ page }) => {
  // Browser-only simulation: startup must not contact a real push provider.
  await page.addInitScript(() => {
    Object.defineProperty(Notification, "permission", { get: () => "default" });
    Notification.requestPermission = async () => {
      throw new Error("This journey must not request system permission");
    };
  });
});

test(
  "W39 Reminder opt-in and hour persist without requesting device permission",
  { tag: "@w39" },
  async ({ page }) => {
    await page.goto("/");
    const initial = {
      reminder_enabled: false,
      reminder_hour: 17,
      path_ready_enabled: true,
      brief_published_enabled: true,
    };
    expect(
      (await page.request.put("/api/v1/notification-preferences", { data: initial })).ok(),
    ).toBe(true);
    await page.getByRole("link", { name: "Notifications", exact: true }).click();
    const reminder = page.getByRole("checkbox", { name: "Reminder", exact: true });
    await expect(reminder).not.toBeChecked();
    await reminder.tap();
    await expect(reminder).toBeChecked();
    const prompt = page.getByRole("region", { name: "Reminder Notifications" });
    await expect(prompt).toBeVisible();
    await expectMinTouchTarget(prompt.getByRole("button", { name: "Not now" }));
    await prompt.getByRole("button", { name: "Not now" }).tap();
    const hour = page.getByRole("combobox", { name: "Reminder hour" });
    await expectMinTouchTarget(hour);
    await hour.selectOption("23");
    await expect
      .poll(
        async () =>
          (await (await page.request.get("/api/v1/notification-preferences")).json()).reminder_hour,
      )
      .toBe(23);
    await page.reload();
    await expect(reminder).toBeChecked();
    await expect(hour).toHaveValue("23");
    await expect(prompt).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
    expect(await (await page.request.get("/api/v1/notification-preferences")).json()).toEqual({
      ...initial,
      reminder_enabled: true,
      reminder_hour: 23,
    });
  },
);

test(
  "W40 scheduled publication emits a fake push that opens the authorized Brief",
  { tag: "@w40" },
  async ({ page, context, request }) => {
    await page.goto("/");
    const session = await (await page.request.get("/api/v1/auth/session")).json();
    const seeded = await request.post(`${BACKEND_URL}/__e2e__/due-beat`, {
      data: { user_id: session.user.id, topic: uniqueTopic("Scheduled notification") },
    });
    expect(seeded.status()).toBe(200);
    const beat = await seeded.json();
    const ecdh = createECDH("prime256v1");
    ecdh.generateKeys();
    const registered = await page.request.post("/api/v1/devices", {
      data: {
        endpoint: `https://fcm.googleapis.com/fcm/send/w40-${randomBytes(12).toString("hex")}`,
        keys: {
          p256dh: ecdh.getPublicKey().toString("base64url"),
          auth: randomBytes(16).toString("base64url"),
        },
      },
    });
    expect(registered.status()).toBe(200);
    const device = await registered.json();
    expect(
      (
        await page.request.put("/api/v1/notification-preferences", {
          data: {
            reminder_enabled: false,
            reminder_hour: 17,
            path_ready_enabled: true,
            brief_published_enabled: true,
          },
        })
      ).ok(),
    ).toBe(true);
    expect((await (await page.request.get(`/api/v1/beats/${beat.id}`)).json()).entries).toEqual([]);
    const tick = await request.post(`${BACKEND_URL}/__e2e__/run-beats`);
    expect(tick.ok()).toBe(true);
    expect((await tick.json()).published).toBeGreaterThan(0);
    const detail = await (await page.request.get(`/api/v1/beats/${beat.id}`)).json();
    expect(detail.entries).toHaveLength(1);
    expect(detail.entries[0].kind).toBe("published");
    const target = `/briefs/${detail.entries[0].id}`;
    const pushes = await (await request.get(`${BACKEND_URL}/__e2e__/pushes`)).json();
    const deliveries = pushes.filter(
      (push: { device_id: string; payload: { notification: { navigate: string } } }) =>
        push.device_id === device.id &&
        new URL(push.payload.notification.navigate).pathname === target,
    );
    expect(deliveries).toHaveLength(1);
    const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
    // A reopened app is controlled by the installed worker. The synthetic click
    // cannot provide OS user activation for opening an uncontrolled new window.
    await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
    await page.reload();
    await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
    await worker.evaluate(
      (payload) => self.dispatchEvent(new PushEvent("push", { data: JSON.stringify(payload) })),
      deliveries[0].payload,
    );
    await expect
      .poll(() =>
        worker.evaluate(
          async () =>
            (await (self as unknown as ServiceWorkerGlobalScope).registration.getNotifications())
              .length,
        ),
      )
      .toBe(1);
    await worker.evaluate(async () => {
      const [notification] = await (
        self as unknown as ServiceWorkerGlobalScope
      ).registration.getNotifications();
      self.dispatchEvent(new NotificationEvent("notificationclick", { notification }));
    });
    await page.waitForURL(`**${target}`);
    await expect(page.getByTestId("brief-body")).toBeVisible();
    await expect(page.getByTestId("brief-sources")).toBeVisible();
    await page.request.delete(`/api/v1/devices/${device.id}`);
    await page.request.delete(`/api/v1/beats/${beat.id}`);
  },
);
