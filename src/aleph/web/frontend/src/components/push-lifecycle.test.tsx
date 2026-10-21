import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";
import { API_V1_BASE } from "../lib/api";
import { sessionQueryOptions } from "../lib/auth";
import { pushClient } from "../lib/push";
import { authenticatedSession, learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { PushLifecycle, announcePathCreated } from "./push-lifecycle";

it("first-path preprompt is tap-only, retryable, and remembers Not now", async () => {
  localStorage.clear();
  vi.stubGlobal("PushManager", class {});
  vi.stubGlobal("Notification", { permission: "default" });
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: {} });
  server.use(
    http.get(`${API_V1_BASE}/push-config`, () =>
      HttpResponse.json({ vapid_public_key: "test-key" }),
    ),
    http.get(`${API_V1_BASE}/paths`, () => HttpResponse.json({ paths: [{ id: "first" }] })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(sessionQueryOptions.queryKey, authenticatedSession);
  const permission = vi.spyOn(pushClient.platform, "permission").mockReturnValue("default");
  const enable = vi
    .spyOn(pushClient, "enable")
    .mockRejectedValueOnce(Error("offline"))
    .mockResolvedValue(false);
  render(
    <QueryClientProvider client={client}>
      <PushLifecycle />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(client.getQueryData(["push-config", learnerUser.id])).toBeTruthy());
  expect(enable).not.toHaveBeenCalled();
  announcePathCreated(learnerUser.id);
  expect(await screen.findByText("Want to know the moment a path is ready?")).toBeVisible();
  expect(enable).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  expect(await screen.findByText("Couldn't enable Notifications. Try again.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  expect(await screen.findByText(/Notifications are off for aleph/)).toBeVisible();
  permission.mockReturnValue("denied");
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  announcePathCreated(learnerUser.id);
  expect(screen.queryByRole("region")).not.toBeInTheDocument();
  expect(localStorage.getItem("aleph:first-path-push-shown")).toBe("1");
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "serviceWorker");
});
