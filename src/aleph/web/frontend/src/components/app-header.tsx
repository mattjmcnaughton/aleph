import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { getPushConfig } from "../lib/api";
import { sessionQueryOptions } from "../lib/auth";
import { useFeatureFlag } from "../lib/feature-flags";
import { AlephLogo } from "./aleph-logo";
import { useLogout } from "./use-logout";

/** Top chrome for the signed-in shell: the brand lockup and the account menu. */
export function AppHeader() {
  return (
    <header className="sticky top-0 z-10 box-border h-[var(--app-header-h)] border-b border-divider bg-night/90 pt-[env(safe-area-inset-top)] backdrop-blur">
      <div className="mx-auto flex h-full w-full max-w-[560px] items-center justify-between gap-3 px-5 lg:max-w-none lg:px-6">
        <Link to="/" aria-label="Aleph home" className="inline-flex shrink-0 rounded-md">
          <AlephLogo />
        </Link>
        <AccountMenu />
      </div>
    </header>
  );
}

/**
 * Everything about the account, behind one control: who is signed in,
 * Notifications, and Sign out.
 *
 * Sign out used to be a bordered button in the header of every screen, which
 * made leaving the most prominent action in the chrome. A `<details>` menu is
 * the same pattern the tutor rail's options use, so it needs no menu library
 * and works without JavaScript state; the effect below only adds the two
 * dismissals a native disclosure lacks (outside press and Escape).
 */
function AccountMenu() {
  const { signOut, pending } = useLogout();
  const session = useQuery(sessionQueryOptions);
  const config = useQuery({ queryKey: ["push-config-menu"], queryFn: getPushConfig });
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const summaryEnabled = useFeatureFlag("learning_summary");
  const publicProfilesEnabled = useFeatureFlag("public_profiles");

  const user = session.data?.authenticated ? session.data.user : null;
  const name = user?.display_name ?? "";
  const initial = name.trim().charAt(0).toUpperCase() || "A";

  function close() {
    detailsRef.current?.removeAttribute("open");
  }

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      const details = detailsRef.current;
      if (details?.open && !details.contains(event.target as Node)) close();
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  });

  return (
    <details
      ref={detailsRef}
      className="relative"
      onKeyDown={(event) => {
        if (event.key === "Escape" && event.currentTarget.open) {
          event.currentTarget.open = false;
          event.currentTarget.querySelector("summary")?.focus();
        }
      }}
    >
      <summary
        data-testid="account-menu"
        aria-label="Account"
        title="Account"
        className="grid h-11 w-11 cursor-pointer list-none place-items-center rounded-full [&::-webkit-details-marker]:hidden"
      >
        <span
          aria-hidden="true"
          className="grid h-8 w-8 place-items-center rounded-full bg-elevated text-sm font-semibold text-porcelain ring-1 ring-divider transition-colors hover:bg-faint/60"
        >
          {initial}
        </span>
      </summary>
      <div className="absolute right-0 top-full z-20 mt-1 w-60 rounded-lg border border-divider bg-surface p-1.5 shadow-lg">
        {name ? (
          <p className="truncate border-b border-divider px-3 pb-2.5 pt-2 text-sm text-mist">
            Signed in as <span className="font-medium text-porcelain">{name}</span>
          </p>
        ) : null}
        <div className={name ? "pt-1.5" : undefined}>
          <Link
            to="/settings/about-you"
            onClick={close}
            data-testid="account-menu-about-you"
            className="flex min-h-11 items-center rounded-md px-3 text-sm text-porcelain transition-colors hover:bg-elevated"
          >
            About you
          </Link>
          {summaryEnabled ? (
            <Link
              to="/learning"
              onClick={close}
              className="flex min-h-11 items-center rounded-md px-3 text-sm text-porcelain transition-colors hover:bg-elevated"
            >
              Your learning
            </Link>
          ) : null}
          {publicProfilesEnabled ? (
            <Link
              to="/settings/public-profile"
              onClick={close}
              data-testid="account-menu-public-profile"
              className="flex min-h-11 items-center rounded-md px-3 text-sm text-porcelain transition-colors hover:bg-elevated"
            >
              Public profile
            </Link>
          ) : null}
          {config.data ? (
            <Link
              to="/settings/notifications"
              onClick={close}
              className="flex min-h-11 items-center rounded-md px-3 text-sm text-porcelain transition-colors hover:bg-elevated"
            >
              Notifications
            </Link>
          ) : null}
          <button
            type="button"
            onClick={signOut}
            disabled={pending}
            className="flex min-h-11 w-full items-center rounded-md px-3 text-left text-sm text-porcelain transition-colors hover:bg-elevated disabled:opacity-50"
          >
            {pending ? "Signing out..." : "Sign out"}
          </button>
        </div>
      </div>
    </details>
  );
}
