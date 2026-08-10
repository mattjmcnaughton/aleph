import { Link } from "@tanstack/react-router";
import type { BeatResearchState, BeatSummary } from "../lib/api";
import { formatElapsed } from "../lib/beats";

/** Card treatment per research state — refusal is iris, failure is danger
 *  (CONTEXT.md), the identical mapping `routes/index.tsx`'s `PathRow` uses
 *  for a path's own `ROW_VARIANT`. */
const BEAT_ROW_VARIANT: Partial<Record<BeatResearchState, "refusal" | "error">> = {
  refused: "refusal",
  failed: "error",
};

const BEAT_ROW_TONE = {
  neutral: "border-divider bg-surface",
  refusal: "border-iris-700 bg-iris-900",
  error: "border-danger-border/60 bg-danger-bg",
} as const;

const BEAT_ROW_STATUS_TONE = {
  neutral: "text-mist",
  refusal: "text-iris-300",
  error: "text-danger",
} as const;

/**
 * The learner-facing one-liner (PRD §3's own examples): `3 new briefs ·
 * weekly` is the steady state, `Researching… · started 30s ago` while a run
 * is in flight. Mirrors `routes/index.tsx`'s own `statusLabel(path)` for the
 * "same card grammar, different verb" (PRD §3).
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
        ? `${beat.unread_count} new ${beat.unread_count === 1 ? "brief" : "briefs"} · ${beat.cadence}`
        : `Up to date · ${beat.cadence}`;
    default: {
      // Exhaustive: a state added to `BeatResearchState` fails the build
      // here rather than silently reading as "Up to date" on a shipped row.
      const unhandled: never = beat.research_state;
      return unhandled;
    }
  }
}

/**
 * One row of the home Beats section (PRD §3: "the same card grammar (title,
 * a line of state) with a different verb" as a path row). `now` is
 * injectable so a test can pin the elapsed-time readout.
 */
export function BeatCard({ beat, now = new Date() }: { beat: BeatSummary; now?: Date }) {
  const variant = BEAT_ROW_VARIANT[beat.research_state] ?? "neutral";
  return (
    <Link
      to="/beats/$beatId"
      params={{ beatId: beat.id }}
      data-testid="beat-list-item"
      data-beat-id={beat.id}
      data-research-state={beat.research_state}
      data-variant={BEAT_ROW_VARIANT[beat.research_state]}
      className={`block min-h-[44px] rounded-lg border p-4 shadow-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal ${BEAT_ROW_TONE[variant]}`}
    >
      {/* min-w-0 + truncate (code-review FIX 6): the project's convention
          for user text (`paths.$pathId.tsx`, `sidebar.tsx`) — a pasted URL
          or an over-long Topic must not overflow the card at 390px. */}
      <p
        data-testid="beat-item-topic"
        className="min-w-0 truncate text-base font-semibold leading-snug"
      >
        {beat.topic}
      </p>
      <p data-testid="beat-item-status" className={`mt-1 text-sm ${BEAT_ROW_STATUS_TONE[variant]}`}>
        {beatStatusLabel(beat, now)}
      </p>
    </Link>
  );
}
