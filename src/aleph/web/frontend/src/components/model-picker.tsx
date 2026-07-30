// The admin model picker (AL-065, TDD §5.3/D14): two slot selects — outline and
// lesson — that pin which model generates this path, so an admin can A/B models
// on real paths without a redeploy. `TutorModelPicker` at the bottom of this
// file extends the same surface to Phase 2's tutor slot, under the same two
// rules, differing only in what the choice *is*: a **per-message** override that
// is resolved per request and persisted nowhere (Phase 2 TDD §5.3), so it lives
// in the rail header rather than in a create form.
//
// Two rules the rest of the app depends on:
//  1. **Admin-only, and absent otherwise.** A non-admin (or signed-out) session
//     renders *nothing at all* — no hidden inputs, no disabled controls, no DOM
//     trace. Enforcement is server-side regardless (`403` on a non-admin
//     override, docs/api.md), but the UI must not dangle the affordance either.
//  2. **Options come from the session**, never from a list hardcoded here. The
//     ids are `MODEL_ALLOWLIST` as `GET /auth/session` exposes it
//     (`user.model_allowlist`, `[]` for everyone else), rendered raw and in the
//     order served — no display labels in Phase 1 (see `lib/api.ts`).

import { MODEL_SLOT_DEFAULT } from "../lib/onboarding";

const SELECT_CLASS =
  "mt-2 w-full appearance-none rounded-md border border-divider bg-surface px-4 py-3 text-sm text-porcelain focus:border-teal focus:outline-none";

export interface ModelPickerProps {
  /** `session.user.is_admin` — the whole surface hinges on it. */
  isAdmin: boolean;
  /** `session.user.model_allowlist` — the only source of options. */
  allowlist: readonly string[];
  outline: string;
  lesson: string;
  onOutlineChange: (value: string) => void;
  onLessonChange: (value: string) => void;
  /** Set when the server rejected a chosen id (`422`, allowlist drift). */
  error?: string;
}

/**
 * The rejection notice's DOM id — the two selects point at it with
 * `aria-describedby`, so a screen-reader user who lands on a slot hears *why*
 * their pick came back, not just an unexplained list they already chose from.
 * Same string as the testid: this component's error, named after it.
 */
const ERROR_ID = "model-picker-error";

function PickerError({ message, spacing }: { message: string; spacing: string }) {
  return (
    <p
      id={ERROR_ID}
      data-testid={ERROR_ID}
      // Announced on arrival: the notice appears well after the submit that
      // caused it, and nothing moves focus to it.
      role="alert"
      className={`${spacing} rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-danger`}
    >
      {message}
    </p>
  );
}

export function ModelPicker({
  isAdmin,
  allowlist,
  outline,
  lesson,
  onOutlineChange,
  onLessonChange,
  error,
}: ModelPickerProps) {
  // Rule 1 is absolute: a non-admin gets no trace of this surface, error or not.
  // (Unreachable with an error anyway — `error` is only set for a create the
  // picker itself put model fields on, which only an admin can do.)
  if (!isAdmin) {
    return null;
  }

  // No allowlist means nothing to pick even for an admin (the list is config,
  // and config can be emptied) — rendering two empty selects would be worse
  // than rendering nothing. The *error* still renders: the allowlist can empty
  // out between the submit and its rejection (that is exactly what a 422 here
  // reports), and a rejection with no visible reason is the worse failure.
  if (allowlist.length === 0) {
    return error ? <PickerError message={error} spacing="mt-6" /> : null;
  }

  // The two per-path model slots the API accepts, in display order. Each slot
  // carries its own value and setter, so rendering never has to look either one
  // up by field name.
  const slots = [
    {
      /** The `POST /paths` field this slot fills (docs/api.md). */
      field: "model_outline",
      id: "model-outline",
      testid: "model-picker-outline",
      label: "Outline model",
      hint: "Generates the units-and-lessons outline, once per path.",
      value: outline,
      onChange: onOutlineChange,
    },
    {
      field: "model_lesson",
      id: "model-lesson",
      testid: "model-picker-lesson",
      label: "Lesson model",
      hint: "Generates each lesson's Read passage and Quick check.",
      value: lesson,
      onChange: onLessonChange,
    },
  ];

  return (
    <fieldset data-testid="model-picker" className="mt-6">
      <legend className="kicker">Models (admin)</legend>
      <p className="mt-2 text-xs leading-5 text-slate">
        Leave a slot on the default to use the configured model.
      </p>
      {error ? <PickerError message={error} spacing="mt-3" /> : null}
      <div className="mt-3 grid gap-4">
        {slots.map((slot) => (
          <div key={slot.field}>
            <label htmlFor={slot.id} className="text-sm font-medium text-mist">
              {slot.label}
            </label>
            <select
              id={slot.id}
              data-testid={slot.testid}
              name={slot.field}
              value={slot.value}
              onChange={(event) => slot.onChange(event.target.value)}
              aria-describedby={error ? ERROR_ID : undefined}
              className={SELECT_CLASS}
            >
              {/* The unset option: the slot is omitted from the payload. */}
              <option value={MODEL_SLOT_DEFAULT}>Server default</option>
              {allowlist.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs leading-5 text-slate">{slot.hint}</p>
          </div>
        ))}
      </div>
    </fieldset>
  );
}

/**
 * The tutor slot's picker, for the rail header (Phase 2 §5.3). One compact
 * select instead of the two-slot fieldset above, because there is one slot and
 * the header has no room for a legend and hints — but the rules are identical:
 * nothing renders for a non-admin, nothing renders with an empty allowlist, and
 * the options are the session's ids verbatim.
 *
 * The difference worth knowing is semantic, not visual: Phase 1's picker pins a
 * model *on the path row*, because a background resume must route the same
 * model. A tutor reply is request-scoped, so this choice rides one message and
 * is never persisted — which is exactly why it belongs beside the conversation
 * it applies to, and why switching it mid-thread is a legitimate thing to do.
 */
export function TutorModelPicker({
  isAdmin,
  allowlist,
  value,
  onChange,
}: {
  isAdmin: boolean;
  allowlist: readonly string[];
  value: string;
  onChange: (value: string) => void;
}) {
  if (!isAdmin || allowlist.length === 0) {
    return null;
  }
  return (
    <select
      data-testid="tutor-rail-model-picker"
      aria-label="Tutor model (admin)"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="max-w-[10rem] appearance-none rounded-md border border-divider bg-surface px-2 py-1.5 text-xs text-mist focus:border-iris focus:outline-none"
    >
      {/* The unset option: the message carries no `model` key at all. */}
      <option value={MODEL_SLOT_DEFAULT}>Server default</option>
      {allowlist.map((model) => (
        <option key={model} value={model}>
          {model}
        </option>
      ))}
    </select>
  );
}
