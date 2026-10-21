import { registerDevice, removeDevice } from "./api";
import type { installState } from "./install";

export interface PushPlatform {
  permission(): NotificationPermission;
  requestPermission(): Promise<NotificationPermission>;
  getSubscription(): Promise<PushSubscriptionJSON | null>;
  subscribe(vapidPublicKey: string): Promise<PushSubscriptionJSON>;
  unsubscribe(): Promise<void>;
}

export function pushState(input: {
  configured: boolean;
  installState: ReturnType<typeof installState>;
  permission: NotificationPermission;
  hasDevice: boolean;
}) {
  if (!input.configured) return "hidden";
  if (input.installState === "needs_home_screen" || input.installState === "unsupported")
    return input.installState;
  if (input.permission === "denied") return "blocked";
  return input.permission === "granted" && input.hasDevice ? "on" : "off";
}

async function workerRegistration(): Promise<ServiceWorkerRegistration> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      navigator.serviceWorker.ready,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("Service worker unavailable")), 10000);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export const browserPushPlatform: PushPlatform = {
  permission: () => ("Notification" in window ? Notification.permission : "denied"),
  requestPermission: () => Notification.requestPermission(),
  async getSubscription() {
    return (await (await workerRegistration()).pushManager.getSubscription())?.toJSON() ?? null;
  },
  async subscribe(key) {
    const bytes = Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/")), (c) =>
      c.charCodeAt(0),
    );
    return (
      await (
        await workerRegistration()
      ).pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes })
    ).toJSON();
  },
  async unsubscribe() {
    const registration = await navigator.serviceWorker.getRegistration();
    await (await registration?.pushManager.getSubscription())?.unsubscribe();
  },
};

const deviceKey = (account: string) => `aleph:push-device:${account}`;
const ignore = () => {};

/** Account-scoped convenience storage is never evidence of server ownership. */
export class PushClient {
  private epoch = 0;
  private last = new Map<string, number>();
  private pending: Promise<void> | null = null;
  private abort: AbortController | null = null;
  private suspended = new Set<string>();
  private account: string | null = null;
  private nativePending: Promise<unknown> = Promise.resolve();

  private native<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.nativePending.then(operation);
    this.nativePending = result.catch(ignore);
    return result;
  }

  resume(account: string) {
    if (this.account !== account) {
      this.epoch++;
      this.abort?.abort();
      this.pending = null;
      this.last.clear();
      this.account = account;
    }
    this.suspended.delete(account);
  }

  constructor(
    readonly platform: PushPlatform,
    private readonly api: { register: typeof registerDevice; remove: typeof removeDevice },
    private readonly storage: Pick<Storage, "getItem" | "setItem" | "removeItem">,
  ) {}

  private store(account: string, id: string | null) {
    try {
      if (id) this.storage.setItem(deviceKey(account), id);
      else this.storage.removeItem(deviceKey(account));
    } catch {
      /* Storage is optional; registration remains server-owned. */
    }
  }

  refresh(account: string, key: string, now = Date.now()): Promise<void> {
    if (this.suspended.has(account)) return Promise.resolve();
    if (this.account !== account) this.resume(account);
    if (this.platform.permission() !== "granted") return Promise.resolve();
    if (this.pending) return this.pending;
    if (now - (this.last.get(account) ?? Number.NEGATIVE_INFINITY) < 600000)
      return Promise.resolve();
    const epoch = this.epoch;
    const abort = new AbortController();
    this.abort = abort;
    this.pending = (async () => {
      const subscription = await this.native(async () => {
        if (epoch !== this.epoch) return null;
        const existing = await this.platform.getSubscription();
        if (epoch !== this.epoch) return null;
        if (existing) return existing;
        const created = await this.platform.subscribe(key);
        if (epoch !== this.epoch) {
          // No later account can use this subscription before disposal finishes.
          await this.platform.unsubscribe();
          return null;
        }
        return created;
      });
      if (!subscription || epoch !== this.epoch) return;
      const device = await this.api.register(subscription, abort.signal);
      if (epoch !== this.epoch) return;
      this.store(account, device.id);
      this.last.set(account, now);
    })().finally(() => {
      if (this.abort === abort) this.pending = null;
    });
    return this.pending;
  }

  async enable(account: string, key: string): Promise<boolean> {
    const epoch = this.epoch;
    // Invoked synchronously by the tap handler, before any asynchronous work.
    const permission =
      this.platform.permission() === "default"
        ? await this.platform.requestPermission()
        : this.platform.permission();
    if (permission !== "granted" || epoch !== this.epoch || this.suspended.has(account))
      return false;
    this.last.delete(account);
    await this.refresh(account, key);
    return true;
  }

  async cleanup(account: string): Promise<void> {
    this.suspended.add(account);
    this.epoch++;
    this.abort?.abort();
    this.abort = null;
    this.pending = null;
    this.last.delete(account);
    let id: string | null = null;
    try {
      id = this.storage.getItem(deviceKey(account));
    } catch {
      /* optional */
    }
    this.store(account, null);
    // Both attempts run even when either fails; neither can hold sign-out hostage.
    await Promise.all(
      [
        id ? this.api.remove(id).catch(ignore) : Promise.resolve(),
        this.native(() => this.platform.unsubscribe()).catch(ignore),
      ].map((attempt) =>
        Promise.race([attempt, new Promise<void>((resolve) => setTimeout(resolve, 3000))]),
      ),
    );
  }
}

export const pushClient = new PushClient(
  browserPushPlatform,
  { register: registerDevice, remove: removeDevice },
  {
    getItem: (key) => localStorage.getItem(key),
    setItem: (key, value) => localStorage.setItem(key, value),
    removeItem: (key) => localStorage.removeItem(key),
  },
);
