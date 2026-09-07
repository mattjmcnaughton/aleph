import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { reviewSummaryQueryOptions } from "../lib/api";
import { useFeatureFlag } from "../lib/feature-flags";
import { AlephLogo } from "./aleph-logo";
import { ReviewPill } from "./review/review-pill";
import { useLogout } from "./use-logout";

function GearIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="2.25" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M8 1.75v1.5M8 12.75v1.5M1.75 8h1.5M12.75 8h1.5M3.58 3.58l1.06 1.06M11.36 11.36l1.06 1.06M3.58 12.42l1.06-1.06M11.36 4.64l1.06-1.06"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * Top chrome for the signed-in shell: brand lockup, the due pill, the
 * Settings gear, sign-out.
 *
 * The gear (CONTEXT.md: Settings) sits here, on every route, because a
 * setting is something a learner goes looking for rather than something a
 * surface pushes at them — one persistent door, icon-only to keep the phone
 * header's width for the pill and the sign-out it already carries.
 *
 * The pill (Phase 3 TDD §8) is mounted here rather than per-route because it
 * is "the one piece of persistent navigation this phase adds" (PRD §3) — on
 * every route, which is also why its failure posture matters more here than
 * anywhere else it could live: `reviewSummaryQueryOptions`'s `enabled` is
 * `useFeatureFlag("flashcards")` (no flag, no fetch), and `ReviewPill` renders
 * nothing for `undefined` — loading, flag off, or a failed `GET
 * /reviews/summary` all look identical to this header, which never branches
 * on `isError` to get there (TDD §5.6's last row, holding on every route).
 */
export function AppHeader() {
  const { signOut, pending } = useLogout();
  const flashcardsEnabled = useFeatureFlag("flashcards");
  const summaryQuery = useQuery(reviewSummaryQueryOptions(flashcardsEnabled));

  return (
    <header className="sticky top-0 z-10 box-border h-[var(--app-header-h)] border-b border-divider bg-night/85 backdrop-blur">
      <div className="mx-auto flex h-full w-full max-w-[480px] items-center justify-between gap-3 px-4 lg:max-w-none lg:px-6">
        <Link to="/" aria-label="Aleph home" className="inline-flex shrink-0">
          <AlephLogo />
        </Link>
        <div className="flex items-center gap-3">
          <ReviewPill summary={summaryQuery.data} />
          <Link
            to="/settings"
            aria-label="Settings"
            title="Settings"
            data-testid="app-header-settings"
            className="grid h-8 w-8 place-items-center rounded-md border border-divider text-mist transition-colors hover:border-teal/50 hover:text-porcelain"
          >
            <GearIcon />
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
      </div>
    </header>
  );
}
