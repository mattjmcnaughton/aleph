// Extracted from routes/paths.$pathId.tsx (Turn 2/desktop shell) so the sidebar
// outline (components/sidebar.tsx) can reuse the exact same marker + label
// instead of a second, drifting copy. Behaviour for the path-view rail is
// unchanged — `size` defaults to "md", today's rail size.

import type { PathLesson } from "../lib/api";
import { CheckIcon, LockIcon, PlayIcon } from "./state-card";

/** Screen-reader label per unlock state (the marker icon is aria-hidden). */
export const UNLOCK_STATE_LABEL: Record<PathLesson["unlock_state"], string> = {
  complete: "Complete",
  available: "Available",
  locked: "Locked",
};

const MARKER_SIZE = {
  /** The sidebar outline's condensed row (mock #2a). */
  sm: "h-5 w-5",
  /** The path-view rail's row — today's only size, kept as the default. */
  md: "h-6 w-6",
} as const;

/**
 * The per-lesson unlock-state badge: check / lock / play, purely decorative
 * (aria-hidden) — `UNLOCK_STATE_LABEL` is what a screen reader announces. Both
 * the path-view rail and the desktop sidebar outline render lessons off the
 * same `unlock_state`, so this stays the one place that maps state to icon.
 */
export function LessonMarker({
  state,
  size = "md",
}: {
  state: PathLesson["unlock_state"];
  size?: keyof typeof MARKER_SIZE;
}) {
  const dimensions = MARKER_SIZE[size];
  if (state === "complete") {
    return (
      <span
        aria-hidden="true"
        className={`grid ${dimensions} shrink-0 place-items-center rounded-full border border-divider text-teal`}
      >
        <CheckIcon />
      </span>
    );
  }
  if (state === "locked") {
    return (
      <span
        aria-hidden="true"
        className={`grid ${dimensions} shrink-0 place-items-center rounded-full border border-divider text-slate`}
      >
        <LockIcon />
      </span>
    );
  }
  return (
    <span
      aria-hidden="true"
      className={`grid ${dimensions} shrink-0 place-items-center rounded-full border border-teal text-teal`}
    >
      <PlayIcon />
    </span>
  );
}
