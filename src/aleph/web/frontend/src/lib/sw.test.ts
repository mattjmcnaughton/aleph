import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

function worker(clients: { url: string; navigate: (url: string) => Promise<unknown> }[] = []) {
  const handlers: Record<string, (event: unknown) => void> = {};
  const shown: unknown[] = [];
  const opened: unknown[] = [];
  const self = {
    location: { origin: "https://aleph.test" },
    addEventListener: (name: string, handler: (event: unknown) => void) => {
      handlers[name] = handler;
    },
    registration: { showNotification: async (...args: unknown[]) => shown.push(args) },
    clients: { matchAll: async () => clients, openWindow: async (url: string) => opened.push(url) },
  };
  runInNewContext(readFileSync("public/sw.js", "utf8"), { self, URL });
  return { handlers, shown, opened };
}

describe("notification-only worker", () => {
  it.each(["navigate", "focus"])("opens the target if a window rejects %s", async (failure) => {
    const { handlers, opened } = worker([
      {
        url: "https://aleph.test/",
        navigate: async () => {
          if (failure === "navigate") throw Error("closed");
          return {
            focus: async () => {
              throw Error("closed");
            },
          };
        },
      },
    ]);
    let pending = Promise.resolve();
    handlers.notificationclick({
      notification: { close() {}, data: { navigate: "/paths/1" } },
      waitUntil: (p: Promise<void>) => {
        pending = p;
      },
    });
    await pending;
    expect(opened).toEqual(["https://aleph.test/paths/1"]);
  });

  it("navigates and focuses an existing same-origin window", async () => {
    const navigations: string[] = [];
    let focused = false;
    const { handlers, opened } = worker([
      {
        url: "https://aleph.test/",
        navigate: async (url) => {
          navigations.push(url);
          return {
            focus: async () => {
              focused = true;
            },
          };
        },
      },
    ]);
    let pending: Promise<unknown> = Promise.resolve();
    handlers.notificationclick({
      notification: { close() {}, data: { navigate: "/paths/1" } },
      waitUntil: (p: Promise<unknown>) => {
        pending = p;
      },
    });
    await pending;
    expect(navigations).toEqual(["https://aleph.test/paths/1"]);
    expect(focused).toBe(true);
    expect(opened).toEqual([]);
  });

  it.each(["https://evil.test/", "//evil.test/", "javascript:alert(1)", {}, null])(
    "click target %j falls back to home",
    async (navigate) => {
      const { handlers, opened } = worker();
      let pending: Promise<unknown> = Promise.resolve();
      handlers.notificationclick({
        notification: { close() {}, data: { navigate } },
        waitUntil: (p: Promise<unknown>) => {
          pending = p;
        },
      });
      await pending;
      expect(opened).toEqual(["https://aleph.test/"]);
    },
  );

  it("shows a declarative push and opens its same-origin target", async () => {
    const { handlers, shown, opened } = worker();
    const pending: Promise<unknown>[] = [];
    const notification = {
      title: "Path ready",
      body: "Ready",
      navigate: "/paths/123",
      tag: "path:123",
      lang: "en",
    };
    handlers.push({
      data: { json: () => ({ web_push: 8030, notification }) },
      waitUntil: (p: Promise<unknown>) => pending.push(p),
    });
    await Promise.all(pending);
    expect(shown).toHaveLength(1);
    handlers.notificationclick({
      notification: { close() {}, data: { navigate: notification.navigate } },
      waitUntil: (p: Promise<unknown>) => pending.push(p),
    });
    await Promise.all(pending);
    expect(opened).toEqual(["https://aleph.test/paths/123"]);
    expect(Object.keys(handlers).sort()).toEqual(["notificationclick", "push"]);
  });

  it.each([
    null,
    {},
    { web_push: 8030, notification: { title: "X", body: "X", navigate: "https://evil.test" } },
    { web_push: 8030, notification: { title: {}, body: "X", navigate: "/" } },
  ])("drops malformed or external payload %j", async (payload) => {
    const { handlers, shown } = worker();
    handlers.push({ data: { json: () => payload }, waitUntil() {} });
    expect(shown).toEqual([]);
  });
});
