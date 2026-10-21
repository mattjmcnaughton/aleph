import { describe, expect, it, vi } from "vitest";
import { PushClient, browserPushPlatform, pushState, type PushPlatform } from "./push";

class Platform implements PushPlatform {
  allowed: NotificationPermission = "default";
  prompts = 0;
  subscriptions = 0;
  subscription: PushSubscriptionJSON | null = null;
  permission() {
    return this.allowed;
  }
  async requestPermission() {
    this.prompts++;
    return this.allowed;
  }
  async getSubscription() {
    return this.subscription;
  }
  async subscribe() {
    this.subscriptions++;
    this.subscription = { endpoint: "https://push.test/1", keys: { auth: "auth", p256dh: "key" } };
    return this.subscription;
  }
  async unsubscribe() {
    this.subscription = null;
  }
}

describe("push client", () => {
  it("reclaims endpoint ownership when returning to an account within the debounce", async () => {
    const platform = new Platform();
    platform.allowed = "granted";
    let account = "a";
    let owner = "";
    const client = new PushClient(
      platform,
      {
        register: async () => {
          owner = account;
          return { id: account };
        },
        remove: async () => {},
      },
      localStorage,
    );
    await client.refresh(account, "key", 1);
    account = "b";
    await client.refresh(account, "key", 2);
    account = "a";
    await client.refresh(account, "key", 3);
    expect(owner).toBe("a");
  });

  it.each([false, true])(
    "disposes a late subscription after bounded logout (new account: %s)",
    async (switchAccount) => {
      vi.useFakeTimers();
      try {
        const platform = new Platform();
        platform.allowed = "granted";
        let finish!: () => void;
        const subscribe = platform.subscribe.bind(platform);
        platform.subscribe = async () => {
          platform.subscribe = subscribe;
          await new Promise<void>((resolve) => {
            finish = resolve;
          });
          return subscribe();
        };
        const registered: string[] = [];
        let account = "a";
        const client = new PushClient(
          platform,
          {
            register: async () => {
              registered.push(account);
              return { id: account };
            },
            remove: async () => {},
          },
          localStorage,
        );
        const refresh = client.refresh(account, "key");
        await vi.advanceTimersByTimeAsync(0);
        const cleanup = client.cleanup(account);
        await vi.advanceTimersByTimeAsync(3000);
        await cleanup;
        let next = Promise.resolve();
        if (switchAccount) {
          account = "b";
          client.resume(account);
          next = client.refresh(account, "key");
        }
        finish();
        await Promise.all([refresh, next]);
        expect(registered).toEqual(switchAccount ? ["b"] : []);
        expect(platform.subscription !== null).toBe(switchAccount);
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it("an unavailable worker produces a retryable error instead of waiting forever", async () => {
    vi.useFakeTimers();
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { ready: new Promise(() => {}) },
    });
    try {
      const result = expect(browserPushPlatform.getSubscription()).rejects.toThrow(
        "Service worker unavailable",
      );
      await vi.advanceTimersByTimeAsync(10000);
      await result;
    } finally {
      Reflect.deleteProperty(navigator, "serviceWorker");
      vi.useRealTimers();
    }
  });

  it("a logout during subscription lookup cannot subscribe or register afterwards", async () => {
    const platform = new Platform();
    platform.allowed = "granted";
    let finish!: (value: PushSubscriptionJSON | null) => void;
    platform.getSubscription = () =>
      new Promise((resolve) => {
        finish = resolve;
      });
    let registrations = 0;
    const client = new PushClient(
      platform,
      { register: async () => ({ id: String(++registrations) }), remove: async () => {} },
      localStorage,
    );
    const refresh = client.refresh("old", "key");
    await Promise.resolve();
    const cleanup = client.cleanup("old");
    finish(null);
    await Promise.all([refresh, cleanup]);
    expect(platform.subscriptions).toBe(0);
    expect(registrations).toBe(0);
    await client.refresh("old", "key");
    expect(registrations).toBe(0);
  });

  it("retries registration failures without prompting and keeps account ids separate", async () => {
    const platform = new Platform();
    platform.allowed = "granted";
    const records = new Map<string, string>();
    let calls = 0;
    const client = new PushClient(
      platform,
      {
        register: async () => {
          if (++calls === 1) throw Error("offline");
          return { id: String(calls) };
        },
        remove: async () => {},
      },
      {
        getItem: (k) => records.get(k) ?? null,
        setItem: (k, v) => {
          records.set(k, v);
        },
        removeItem: (k) => {
          records.delete(k);
        },
      },
    );
    await expect(client.refresh("a", "key", 1)).rejects.toThrow("offline");
    await client.refresh("a", "key", 2);
    await client.refresh("b", "key", 3);
    expect([...records.values()]).toEqual(["2", "3"]);
    expect(platform.prompts).toBe(0);
  });

  it("never asks permission on startup, recovers a missing subscription and debounces", async () => {
    const platform = new Platform();
    let registrations = 0;
    const client = new PushClient(
      platform,
      { register: async () => ({ id: String(++registrations) }), remove: async () => {} },
      localStorage,
    );
    await client.refresh("account", "key", 100);
    expect(platform.prompts).toBe(0);
    platform.allowed = "granted";
    await client.refresh("account", "key", 101);
    await client.refresh("account", "key", 102);
    expect(registrations).toBe(1);
    platform.subscription = null;
    await client.refresh("account", "key", 600102);
    expect(platform.subscriptions).toBe(2);
    expect(registrations).toBe(2);
    await client.cleanup("account");
    expect(platform.subscription).toBeNull();
  });

  it("does not repeat a denied prompt and still unsubscribes if delete fails", async () => {
    const platform = new Platform();
    const client = new PushClient(
      platform,
      {
        register: async () => ({ id: "id" }),
        remove: async () => {
          throw Error("offline");
        },
      },
      localStorage,
    );
    platform.allowed = "denied";
    expect(await client.enable("a", "key")).toBe(false);
    expect(platform.prompts).toBe(0);
    platform.allowed = "granted";
    expect(await client.enable("a", "key")).toBe(true);
    await client.cleanup("a");
    expect(platform.subscription).toBeNull();
  });

  it.each([
    [false, "browser_ok", "granted", true, "hidden"],
    [true, "needs_home_screen", "default", false, "needs_home_screen"],
    [true, "unsupported", "default", false, "unsupported"],
    [true, "browser_ok", "default", false, "off"],
    [true, "home_screen", "granted", true, "on"],
    [true, "home_screen", "granted", false, "off"],
    [true, "browser_ok", "denied", false, "blocked"],
  ] as const)(
    "state %s %s %s %s -> %s",
    (configured, installState, permission, hasDevice, expected) => {
      expect(pushState({ configured, installState, permission, hasDevice })).toBe(expected);
    },
  );
});
