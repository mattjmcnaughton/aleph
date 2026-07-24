// The onboarding surface's state machine, kept pure so the topic+level →
// generating → ready/refused/failed transitions are testable without a router,
// a network, or fake timers (§5.1, §5.6; W7 refusal, W8 failure+retry).

import type { Level, PathStatus } from "./api";

/** The three self-assessed levels offered at onboarding, in display order. */
export const LEVELS: ReadonlyArray<{ value: Level; label: string }> = [
  { value: "new_to_it", label: "New to it" },
  { value: "some_experience", label: "Some experience" },
  { value: "work_in_it", label: "I work in it" },
] as const;

/** The display label for a level, e.g. on a "Your paths" row (§5.5). */
export function levelLabel(level: Level): string {
  return LEVELS.find((option) => option.value === level)?.label ?? level;
}

/**
 * What the onboarding surface is showing right now:
 * - `editing`     — the topic + level form (initial, and where a refusal or a
 *   create error returns the learner, inputs intact).
 * - `generating`  — the visible loading state covering outline generation.
 * - `refused`     — a graceful, non-error safety message (W7), distinct from
 *   `failed`; the learner tries a different topic.
 * - `failed`      — an error state; topic + level are preserved for one-tap
 *   retry (W8).
 *
 * `ready` is not a phase: it is a side effect (navigate to the path view), so
 * the machine stays on `generating` for the instant before the route changes.
 */
export type OnboardingPhase = "editing" | "generating" | "refused" | "failed";

/**
 * Pure phase derivation. `pathId === null` means nothing has been created yet
 * (the form). Once a path exists, the polled `status` drives the phase; an
 * unresolved/undefined status (pending, generating, ready-before-nav, or a
 * poll still in flight) all read as `generating`.
 */
export function deriveOnboardingPhase(input: {
  pathId: string | null;
  status: PathStatus | undefined;
}): OnboardingPhase {
  if (input.pathId === null) {
    return "editing";
  }
  switch (input.status) {
    case "refused":
      return "refused";
    case "failed":
      return "failed";
    default:
      // pending | generating | ready (transient, navigating away) | undefined
      return "generating";
  }
}

/** Whether the submit button should be enabled: a non-blank topic is required. */
export function canSubmitTopic(topic: string): boolean {
  return topic.trim().length > 0;
}
