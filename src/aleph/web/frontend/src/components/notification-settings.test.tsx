import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, it, vi } from "vitest";
import { API_V1_BASE } from "../lib/api";
import { pushClient } from "../lib/push";
import { server } from "../mocks/server";
import { NotificationSettings } from "./notification-settings";

const preferences = {
  reminder_enabled: true,
  reminder_hour: 17,
  path_ready_enabled: true,
  brief_published_enabled: false,
};
beforeEach(() => {
  vi.stubGlobal("PushManager", class {});
  vi.stubGlobal("Notification", { permission: "default" });
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: {} });
  server.use(
    http.get(`${API_V1_BASE}/push-config`, () => HttpResponse.json({ vapid_public_key: "test" })),
    http.get(`${API_V1_BASE}/notification-preferences`, () => HttpResponse.json(preferences)),
  );
});
function setup() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <NotificationSettings account="settings-test" />
    </QueryClientProvider>,
  );
}
it("hides unconfigured controls without prompting", async () => {
  server.use(http.get(`${API_V1_BASE}/push-config`, () => new HttpResponse(null, { status: 404 })));
  const enable = vi.spyOn(pushClient, "enable");
  setup();
  expect(await screen.findByText("Notifications are not configured.")).toBeVisible();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(enable).not.toHaveBeenCalled();
});
it("needs Home Screen explains delivery and keeps account preferences editable with retry", async () => {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue("iPhone Version/18 Safari/605");
  let saved: Record<string, unknown> = {};
  let fail = true;
  server.use(
    http.put(`${API_V1_BASE}/notification-preferences`, async ({ request }) => {
      saved = (await request.json()) as Record<string, unknown>;
      return fail ? new HttpResponse(null, { status: 503 }) : HttpResponse.json(saved);
    }),
  );
  setup();
  expect(await screen.findByText(/This browser cannot receive Notifications yet/)).toBeVisible();
  fireEvent.click(await screen.findByRole("checkbox", { name: "Path ready" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save");
  expect(screen.getByRole("checkbox", { name: "Path ready" })).toBeChecked();
  fail = false;
  fireEvent.click(screen.getByRole("checkbox", { name: "Path ready" }));
  await waitFor(() =>
    expect(screen.getByRole("checkbox", { name: "Path ready" })).not.toBeChecked(),
  );
  expect(saved).toEqual({ ...preferences, path_ready_enabled: false });
  expect(screen.queryByRole("button", { name: "Enable Notifications" })).toBeNull();
});
it("off enables only on tap, recovers errors, and renders on", async () => {
  const enable = vi
    .spyOn(pushClient, "enable")
    .mockRejectedValueOnce(Error("offline"))
    .mockResolvedValue(true);
  const permission = vi.spyOn(pushClient.platform, "permission").mockReturnValue("default");
  vi.spyOn(pushClient.platform, "getSubscription").mockResolvedValue(null);
  setup();
  const button = await screen.findByRole("button", { name: "Enable Notifications" });
  expect(enable).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't enable");
  permission.mockReturnValue("granted");
  fireEvent.click(button);
  expect(await screen.findByText("Notifications are on for this device.")).toBeVisible();
});
it("blocked has settings guidance and no enable action", async () => {
  vi.spyOn(pushClient.platform, "permission").mockReturnValue("denied");
  setup();
  expect(await screen.findByText(/Notifications are off for aleph/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Enable Notifications" })).toBeNull();
});
it("unsupported still permits account preferences", async () => {
  vi.stubGlobal("PushManager", undefined);
  Reflect.deleteProperty(window, "PushManager");
  setup();
  expect(await screen.findByText("Notifications are not available in this browser.")).toBeVisible();
  expect(await screen.findByRole("checkbox", { name: "Brief published" })).toBeEnabled();
});

it("a failed foreground check offers recovery by reopening settings without prompting", async () => {
  vi.spyOn(pushClient.platform, "permission").mockReturnValue("granted");
  vi.spyOn(pushClient.platform, "getSubscription").mockResolvedValue({ endpoint: "test" });
  const refresh = vi.spyOn(pushClient, "refresh").mockResolvedValue();
  const enable = vi.spyOn(pushClient, "enable");
  const view = setup();
  expect(await screen.findByText("Notifications are on for this device.")).toBeVisible();
  refresh.mockRejectedValueOnce(Error("offline"));
  fireEvent(document, new Event("visibilitychange"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Reopen Notifications settings");
  view.unmount();
  setup();
  expect(await screen.findByText("Notifications are on for this device.")).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(enable).not.toHaveBeenCalled();
});
