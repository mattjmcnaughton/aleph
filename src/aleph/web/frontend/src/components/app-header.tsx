import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { reviewSummaryQueryOptions } from "../lib/api";
import { useFeatureFlag } from "../lib/feature-flags";
import { AlephLogo } from "./aleph-logo";
import { ReviewPill } from "./review/review-pill";
import { useLogout } from "./use-logout";

/**
 * Top chrome for the signed-in shell: brand lockup, the due pill, sign-out.
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
