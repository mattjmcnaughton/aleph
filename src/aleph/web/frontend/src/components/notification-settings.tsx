import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  getPushConfig,
  notificationPreferencesQueryOptions,
  updateNotificationPreferences,
} from "../lib/api";
import { installState, isStandalone } from "../lib/install";
import { pushClient, pushState } from "../lib/push";

const buttonClass =
  "min-h-11 rounded-md border border-divider px-4 py-2 text-sm text-porcelain hover:border-teal focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal disabled:opacity-50";

export function NotificationSettings({ account }: { account: string }) {
  const queryClient = useQueryClient();
  const config = useQuery({ queryKey: ["push-config", account], queryFn: getPushConfig });
  const options = notificationPreferencesQueryOptions(account);
  const preferences = useQuery({ ...options, enabled: !!config.data });
  const [hasDevice, setHasDevice] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [saving, setSaving] = useState(false);
  const [enableError, setEnableError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [permission, setPermission] = useState(pushClient.platform.permission());
  const installation = installState({
    userAgent: navigator.userAgent,
    standalone: isStandalone(),
    pushSupported:
      "serviceWorker" in navigator && "PushManager" in window && "Notification" in window,
    dismissedAt: null,
    now: Date.now(),
  });
  const key = config.data?.vapid_public_key;
  useEffect(() => {
    pushClient.resume(account);
    if (!key || (installation !== "home_screen" && installation !== "browser_ok")) return;
    let live = true;
    const refresh = () => {
      if (!live || document.visibilityState !== "visible") return;
      setPermission(pushClient.platform.permission());
      if (pushClient.platform.permission() !== "granted") {
        setHasDevice(false);
        return;
      }
      void pushClient
        .refresh(account, key)
        .then(async () => {
          const subscription = await pushClient.platform.getSubscription();
          if (live) {
            setHasDevice(!!subscription);
            setEnableError("");
          }
        })
        .catch(() => {
          if (live)
            setEnableError(
              "Couldn't check this device. Reopen Notifications settings to try again.",
            );
        });
    };
    refresh();
    document.addEventListener("visibilitychange", refresh);
    return () => {
      live = false;
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [account, key, installation]);

  if (config.isPending) return <output>Loading Notifications…</output>;
  if (config.isError)
    return (
      <div role="alert">
        Couldn't load Notifications.{" "}
        <button className={buttonClass} type="button" onClick={() => void config.refetch()}>
          Try again
        </button>
      </div>
    );
  if (!config.data) return <p className="text-mist">Notifications are not configured.</p>;
  const state = pushState({ configured: true, installState: installation, permission, hasDevice });
  return (
    <div className="space-y-6">
      <section aria-label="This device" className="rounded-lg border border-divider bg-surface p-5">
        <h2 className="text-lg font-semibold">This device</h2>
        <div className="mt-2 space-y-3 text-sm leading-6 text-mist">
          {state === "needs_home_screen" ? (
            <>
              <p className="font-medium text-porcelain">Add aleph to your Home Screen</p>
              <p>
                Tap <strong>Share</strong>, then <strong>Add to Home Screen</strong>.
              </p>
              <p>
                This browser cannot receive Notifications yet. Open aleph from your Home Screen to
                enable them.
              </p>
            </>
          ) : null}
          {state === "unsupported" ? <p>Notifications are not available in this browser.</p> : null}
          {state === "blocked" ? (
            <p>
              Notifications are off for aleph. Change this in your browser or device Settings. On
              iPhone, open Settings → Notifications → aleph.
            </p>
          ) : null}
          {state === "on" ? <p>Notifications are on for this device.</p> : null}
          {state === "off" ? (
            <>
              <p>Notifications are off for this device.</p>
              <button
                type="button"
                className={buttonClass}
                disabled={enabling}
                onClick={() => {
                  setEnabling(true);
                  setEnableError("");
                  void pushClient
                    .enable(account, key ?? "")
                    .then((enabled) => {
                      setPermission(pushClient.platform.permission());
                      setHasDevice(enabled);
                    })
                    .catch(() => setEnableError("Couldn't enable Notifications. Try again."))
                    .finally(() => setEnabling(false));
                }}
              >
                {enabling ? "Enabling…" : "Enable Notifications"}
              </button>
            </>
          ) : null}
          {enableError ? <p role="alert">{enableError}</p> : null}
        </div>
      </section>
      <section
        aria-label="What to send"
        className="rounded-lg border border-divider bg-surface p-5"
      >
        <h2 className="text-lg font-semibold">What to send</h2>
        <p className="mt-2 text-sm text-mist">
          Account preferences apply to all your enabled Devices, not just this browser.
        </p>
        {preferences.isPending ? <output>Loading preferences…</output> : null}
        {preferences.isError ? (
          <div role="alert">
            Couldn't load preferences.{" "}
            <button
              type="button"
              className={buttonClass}
              onClick={() => void preferences.refetch()}
            >
              Try again
            </button>
          </div>
        ) : null}
        {preferences.data ? (
          <div className="mt-3">
            {(
              [
                ["path_ready_enabled", "Path ready"],
                ["brief_published_enabled", "Brief published"],
              ] as const
            ).map(([field, label]) => (
              <label
                key={field}
                className="flex min-h-11 cursor-pointer items-center justify-between gap-4 text-sm"
              >
                <span>{label}</span>
                <input
                  type="checkbox"
                  className="h-5 w-5 accent-teal"
                  checked={preferences.data[field]}
                  disabled={saving}
                  onChange={(event) => {
                    if (!preferences.data) return;
                    const next = { ...preferences.data, [field]: event.target.checked };
                    setSaving(true);
                    setSaveError("");
                    void updateNotificationPreferences(next)
                      .then((saved) => queryClient.setQueryData(options.queryKey, saved))
                      .catch(() =>
                        setSaveError(
                          "Couldn't save preferences. Try again by changing the preference.",
                        ),
                      )
                      .finally(() => setSaving(false));
                  }}
                />
              </label>
            ))}
          </div>
        ) : null}
        {saving ? <output>Saving…</output> : null}
        {saveError ? (
          <p role="alert" className="mt-2 text-sm text-mist">
            {saveError}
          </p>
        ) : null}
      </section>
    </div>
  );
}
