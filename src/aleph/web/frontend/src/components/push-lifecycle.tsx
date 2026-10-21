import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getPushConfig, listPaths } from "../lib/api";
import { sessionQueryOptions } from "../lib/auth";
import { installState, isStandalone } from "../lib/install";
import { pushClient } from "../lib/push";

export function announcePathCreated(account: string) {
  window.dispatchEvent(new CustomEvent("aleph:path-created", { detail: account }));
}

/** Persistent authenticated shell: neither navigation nor fast generation loses the prompt. */
export function PushLifecycle() {
  const session = useQuery(sessionQueryOptions).data;
  const account = session?.authenticated ? session.user?.id : undefined;
  return account ? <AccountPush key={account} account={account} /> : null;
}

function AccountPush({ account }: { account: string }) {
  const config = useQuery({ queryKey: ["push-config", account], queryFn: getPushConfig });
  const [prompt, setPrompt] = useState(false);
  const [created, setCreated] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const key = config.data?.vapid_public_key;
  const supported =
    "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const state = installState({
    userAgent: navigator.userAgent,
    standalone: isStandalone(),
    pushSupported: supported,
    dismissedAt: null,
    now: Date.now(),
  });

  useEffect(() => {
    pushClient.resume(account);
    const remember = (event: Event) => {
      if ((event as CustomEvent<string>).detail === account) setCreated(true);
    };
    window.addEventListener("aleph:path-created", remember);
    return () => window.removeEventListener("aleph:path-created", remember);
  }, [account]);

  useEffect(() => {
    if (!key || (state !== "home_screen" && state !== "browser_ok")) return;
    let live = true;
    const refresh = () => {
      if (live && document.visibilityState === "visible")
        void pushClient.refresh(account, key).catch(() => {});
    };
    refresh();
    document.addEventListener("visibilitychange", refresh);
    return () => {
      live = false;
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [account, key, state]);

  useEffect(() => {
    if (!created || !key || (state !== "home_screen" && state !== "browser_ok")) return;
    let live = true;
    const offer = async () => {
      if (pushClient.platform.permission() !== "default") return;
      try {
        if (localStorage.getItem("aleph:first-path-push-shown")) return;
      } catch {
        /* The mounted prompt still prevents a repeat this visit. */
      }
      try {
        const paths = await listPaths();
        if (!live || paths.paths.length !== 1) return;
        try {
          localStorage.setItem("aleph:first-path-push-shown", "1");
        } catch {
          /* optional */
        }
        setPrompt(true);
      } catch {
        /* Creating a path must not depend on Notifications. */
      }
    };
    void offer();
    return () => {
      live = false;
    };
  }, [created, key, state]);

  if (!prompt || !key) return null;
  return (
    <section
      aria-label="Path ready Notifications"
      className="mx-auto my-4 max-w-[480px] rounded-lg border border-teal/40 bg-surface p-4 text-porcelain"
    >
      <p className="font-medium">Want to know the moment a path is ready?</p>
      <p className="mt-1 text-sm text-mist">Allow Notifications on this device.</p>
      {message ? <output className="mt-2 block text-sm text-mist">{message}</output> : null}
      <div className="mt-3 flex gap-3">
        <button
          type="button"
          disabled={pending}
          className="min-h-11 rounded-md bg-teal px-4 py-2 font-semibold text-night disabled:opacity-50"
          onClick={() => {
            setPending(true);
            setMessage("");
            void pushClient
              .enable(account, key)
              .then((enabled) => {
                if (enabled) setPrompt(false);
                else
                  setMessage(
                    "Notifications are off for aleph. You can change this in your browser or device Settings.",
                  );
              })
              .catch(() => setMessage("Couldn't enable Notifications. Try again."))
              .finally(() => setPending(false));
          }}
        >
          Yes
        </button>
        <button
          type="button"
          disabled={pending}
          className="min-h-11 rounded-md px-4 py-2 text-mist hover:text-porcelain"
          onClick={() => setPrompt(false)}
        >
          Not now
        </button>
      </div>
    </section>
  );
}
