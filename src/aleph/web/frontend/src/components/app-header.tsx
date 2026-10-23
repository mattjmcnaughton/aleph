import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { getPushConfig } from "../lib/api";
import { useFeatureFlag } from "../lib/feature-flags";
import { AlephLogo } from "./aleph-logo";
import { useLogout } from "./use-logout";

/** Top chrome for the signed-in shell: brand lockup and sign-out. */
export function AppHeader() {
  const { signOut, pending } = useLogout();
  const config = useQuery({ queryKey: ["push-config-menu"], queryFn: getPushConfig });
  const learningTutor = useFeatureFlag("learning_tutor");

  return (
    <header className="sticky top-0 z-10 box-border h-[var(--app-header-h)] border-b border-divider bg-night/85 pt-[env(safe-area-inset-top)] backdrop-blur">
      <div className="mx-auto flex h-full w-full max-w-[480px] items-center justify-between gap-3 px-4 lg:max-w-none lg:px-6">
        <Link to="/" aria-label="Aleph home" className="inline-flex shrink-0">
          <AlephLogo />
        </Link>
        <div className="flex items-center gap-1 lg:gap-3">
          {learningTutor && (
            <Link
              to="/tutor"
              className="inline-flex min-h-11 items-center rounded-md px-2 text-sm text-mist hover:text-porcelain"
            >
              Tutor
            </Link>
          )}
          {config.data ? (
            <Link
              to="/settings/notifications"
              className="inline-flex min-h-11 items-center rounded-md px-2 text-sm text-mist hover:text-porcelain"
            >
              Notifications
            </Link>
          ) : null}
          <button
            type="button"
            onClick={signOut}
            disabled={pending}
            className="rounded-md border border-divider px-3 py-1.5 text-sm text-mist transition-colors hover:border-teal/50 hover:text-porcelain disabled:opacity-50"
          >
            {pending ? "Signing out..." : "Sign out"}
          </button>
        </div>
      </div>
    </header>
  );
}
