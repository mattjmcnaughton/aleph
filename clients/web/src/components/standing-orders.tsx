import type { BeatDetail } from "../lib/api";
import { cadenceLabel } from "../lib/beats";

/**
 * The Beat rail's one-liner head (PRD §3: `Weekly · EU AI regulation ·
 * policy and enforcement`) — cadence, Topic, and Guidance when there is any.
 * These are the Beat's frozen standing orders (CONTEXT.md: Beat) — changing
 * them means delete and redeploy (PRD §4.11), so this is display-only, never
 * editable in place.
 */
export function StandingOrders({
  beat,
}: {
  beat: Pick<BeatDetail, "cadence" | "topic" | "guidance">;
}) {
  const parts = [cadenceLabel(beat.cadence), beat.topic, beat.guidance ?? undefined].filter(
    (part): part is string => Boolean(part),
  );
  return (
    <p
      data-testid="standing-orders"
      // Clamped to one line (code-review FIX 6): PRD §3 calls this a
      // "one line" head, but `GUIDANCE_MAX_LENGTH` is 4000 and
      // `TOPIC_MAX_LENGTH` is 500 — a learner pasting a couple of paragraphs
      // of Guidance would otherwise push the whole rail below the fold. The
      // project's `truncate` idiom for user text (`paths.$pathId.tsx`,
      // `sidebar.tsx`), plus `min-w-0` since this sits in the route's own
      // block flow rather than a flex row today, kept defensively for the
      // same reason those two call sites keep it.
      className="min-w-0 truncate text-sm leading-6 text-mist"
    >
      {parts.join(" · ")}
    </p>
  );
}
