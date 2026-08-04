// The end of the day's queue (PRD §3/§4.4, Phase 3 TDD §8): the session ends,
// full stop. No "study more" button — the cap is the point — and the widen
// offer appears only at the end of a **filtered** session with cards due
// elsewhere (PRD §4.10's one exception to "scope is chosen at the door").

import { Link } from "@tanstack/react-router";
import { PRIMARY_CTA_BASE } from "../state-card";

export function SessionComplete({
  scopePathId,
  otherDueCount,
}: {
  /** `ReviewQueueResponse.scope_path_id` — `null` in an "All paths" session,
   *  where there is nothing left to widen to. */
  scopePathId: string | null;
  /** Non-zero only when `scopePathId` is set (docs/api.md's own invariant). */
  otherDueCount: number;
}) {
  const showWiden = scopePathId !== null && otherDueCount > 0;

  return (
    <div data-testid="session-complete" className="flex flex-col gap-4 text-center">
      <p className="text-lg font-semibold">That's today's queue.</p>
      <p className="text-sm leading-6 text-mist">
        Nice work. Ten a day is the whole idea, so that's it until tomorrow.
      </p>

      {showWiden ? (
        <div
          data-testid="session-widen-offer"
          className="rounded-lg bg-elevated p-4 text-left ring-1 ring-inset ring-white/10"
        >
          <p className="kicker text-[10px]">At the end of a filtered session</p>
          <p className="mt-2 text-sm leading-6">
            Nothing left on this path.{" "}
            <span className="text-mist">
              {otherDueCount} {otherDueCount === 1 ? "card is" : "cards are"} due on your other
              paths.
            </span>
          </p>
          <Link
            to="/review"
            data-testid="session-widen-action"
            className={`mt-3 ${PRIMARY_CTA_BASE}`}
          >
            Review those too
          </Link>
        </div>
      ) : null}
    </div>
  );
}
