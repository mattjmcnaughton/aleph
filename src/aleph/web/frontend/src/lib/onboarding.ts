// The onboarding surface's state machine, kept pure so the topic+level →
// generating → ready/refused/failed transitions are testable without a router,
// a network, or fake timers (§5.1, §5.6; W7 refusal, W8 failure+retry).

import type { CreatePathInput, Level, PathStatus } from "./api";

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

/**
 * The topic field's cap, mirroring `TopicStr` in `dtos/paths.py`
 * (`max_length=500`). The input enforces it so an over-long topic is stopped at
 * the keyboard rather than coming back as a `422` — which the client would
 * otherwise have to tell apart from the *other* `422` on this endpoint (an
 * off-allowlist model override). Keep the two numbers in step.
 */
export const TOPIC_MAX_LENGTH = 500;

/**
 * The guidance field's cap, mirroring `GuidanceStr` in `dtos/paths.py`
 * (`max_length=4000`) the way `TOPIC_MAX_LENGTH` mirrors `TopicStr` above. Keep
 * the two numbers in step.
 */
export const GUIDANCE_MAX_LENGTH = 4000;

/**
 * The path title field's cap, mirroring `PathTitleStr` in `dtos/paths.py`
 * (`max_length=200`) the way `TOPIC_MAX_LENGTH` mirrors `TopicStr` above. Stop
 * an over-long rename at the keyboard rather than round-tripping a `422` the
 * learner can't read (`routes/paths.$pathId.tsx`'s `PathTitle`). Keep the two
 * numbers in step.
 */
export const PATH_TITLE_MAX_LENGTH = 200;

/**
 * The admin model picker's "use the server default" value (AL-065, §5.3/D14).
 * The empty string, because that is what an unselected `<option>` carries — the
 * picker needs a real DOM value for "no override", and the payload builder below
 * is the single place that turns it back into an absent key.
 */
export const MODEL_SLOT_DEFAULT = "";

/**
 * Build the `POST /api/v1/paths` body from the form's state (docs/api.md).
 *
 * The one rule worth a function: an unchosen model slot is **omitted**, not sent
 * as null. A non-admin sending `model_outline` at all is `403 forbidden`, so
 * "absent" and "explicitly nothing" are different payloads to this endpoint, and
 * only absent is correct when the picker is unset or was never rendered.
 *
 * `guidance` follows the same absent-vs-empty rule: a blank or whitespace-only
 * textarea omits the key rather than sending `""`, so a learner who opens and
 * closes the field without typing anything gets the identical payload as one who
 * never saw it.
 */
export function buildCreatePathInput(input: {
  topic: string;
  level: Level;
  guidance?: string;
  modelOutline?: string;
  modelLesson?: string;
}): CreatePathInput {
  // A slot value is either `MODEL_SLOT_DEFAULT` (the empty string) or an id the
  // picker took verbatim from the session — never free text — so a falsy check
  // is the whole rule, and `undefined` (the picker never rendered) falls out of
  // it for free.
  const body: CreatePathInput = { topic: input.topic.trim(), level: input.level };
  const guidance = input.guidance?.trim();
  if (guidance) body.guidance = guidance;
  if (input.modelOutline) body.model_outline = input.modelOutline;
  if (input.modelLesson) body.model_lesson = input.modelLesson;
  return body;
}
