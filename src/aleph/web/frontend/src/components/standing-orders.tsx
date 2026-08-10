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
    <p data-testid="standing-orders" className="text-sm leading-6 text-mist">
      {parts.join(" · ")}
    </p>
  );
}
