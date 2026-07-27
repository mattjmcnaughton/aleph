import { Link } from "@tanstack/react-router";
import { AlephLogo } from "./aleph-logo";
import { useLogout } from "./use-logout";

/** Top chrome for the signed-in shell: brand lockup + sign-out. */
export function AppHeader() {
  const { signOut, pending } = useLogout();

  return (
    <header className="sticky top-0 z-10 border-b border-divider bg-night/85 backdrop-blur">
      <div className="mx-auto flex w-full max-w-[480px] items-center justify-between px-4 py-3 lg:max-w-none lg:px-6">
        <Link to="/" aria-label="Aleph home" className="inline-flex">
          <AlephLogo />
        </Link>
        <button
          type="button"
          onClick={signOut}
          disabled={pending}
          className="rounded-md border border-divider px-3 py-1.5 text-sm text-mist transition-colors hover:border-teal/50 hover:text-porcelain disabled:opacity-50"
        >
          {pending ? "Signing out..." : "Sign out"}
        </button>
      </div>
    </header>
  );
}
