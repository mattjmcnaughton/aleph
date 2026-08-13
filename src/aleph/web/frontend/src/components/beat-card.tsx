import { Link } from "@tanstack/react-router";
import type { BeatResearchState, BeatSummary } from "../lib/api";
import { formatElapsed } from "../lib/beats";
import { ListRow, type RowVariant, RowTitle } from "./list-row";

/** Card treatment per research state — refusal is iris, failure is danger
 *  (CONTEXT.md), the identical mapping `routes/index.tsx`'s `PathRow` uses
 *  for a path's own `ROW_VARIANT`. */
const BEAT_ROW_VARIANT: Partial<Record<BeatResearchState, Exclude<RowVariant, "neutral">>> = {
  refused: "refusal",
  failed: "error",
};

/**
 * The learner-facing one-liner (PRD §3's own examples): `3 new briefs` is the
 * steady state, `Researching… · started 30s ago` while a run is in flight.
 * Mirrors `routes/index.tsx`'s own `statusLabel(path)` for the "same card
 * grammar, different verb" (PRD §3).
 *
 * The cadence clause moved out of this string and into the row's own meta
 * cell, where a path's progress readout sits — it is the Beat's answer to
 * "how much of this is there", which is the column's question, and leaving it
 * inside the status sentence was what made a Beat's one line read as prose
 * where a path's read as data.
 */
function beatStatusLabel(beat: BeatSummary, now: Date): string {
  switch (beat.research_state) {
    case "refused":
      return "This topic is out of scope.";
    case "failed":
      return "Research didn't finish. Open to retry.";
    case "researching":
      return beat.research_started_at
        ? `Researching… · started ${formatElapsed(beat.research_started_at, now)}`
        : "Researching…";
    case "idle":
      return beat.unread_count > 0
        ? `${beat.unread_count} new ${beat.unread_count === 1 ? "brief" : "briefs"}`
        : "Up to date";
    default: {
      // Exhaustive: a state added to `BeatResearchState` fails the build
      // here rather than silently reading as "Up to date" on a shipped row.
      const unhandled: never = beat.research_state;
      return unhandled;
    }
  }
}

/**
 * One row of the home Beats section — the **same** `ListRow` a path renders
 * through (PRD §3: "the same card grammar (title, a line of state) with a
 * different verb"). It used to be a bordered `rounded-lg` card with its own
 * shadow while a path was a bare table line, so at `lg` the two sections read
 * as unrelated kinds of object; now the only difference between them is which
 * cells they fill. A Beat leaves `chip` empty and puts its cadence where a
 * path puts progress.
 *
 * `now` is injectable so a test can pin the elapsed-time readout.
 */
export function BeatCard({ beat, now = new Date() }: { beat: BeatSummary; now?: Date }) {
  const variant = BEAT_ROW_VARIANT[beat.research_state] ?? "neutral";

  return (
    <ListRow
      testid="beat-list-item"
      variant={variant}
      dataAttrs={{
        "data-beat-id": beat.id,
        "data-research-state": beat.research_state,
        "data-variant": BEAT_ROW_VARIANT[beat.research_state],
      }}
      main={
        <Link
          to="/beats/$beatId"
          params={{ beatId: beat.id }}
          data-testid="beat-item-open"
          className="block min-w-0 rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal"
        >
          <RowTitle title={beat.topic} titleTestid="beat-item-topic" />
        </Link>
      }
      meta={
        <p data-testid="beat-item-cadence" className="mt-1 text-sm text-mist lg:mt-0">
          {beat.cadence}
        </p>
      }
      status={<span data-testid="beat-item-status">{beatStatusLabel(beat, now)}</span>}
      actions={
        <span aria-hidden="true" className="hidden shrink-0 text-slate lg:block">
          ›
        </span>
      }
    />
  );
}
