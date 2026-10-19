import { TEACHING_STYLE_MAX_LENGTH } from "../lib/onboarding";

/**
 * One-tap starters for a Teaching style (docs/CONTEXT.md). Each appends its
 * text to whatever is already typed rather than replacing it, so a learner can
 * combine them and then edit freely. Starters only; never a constraint.
 */
export const TEACHING_STYLE_STARTERS: ReadonlyArray<{ label: string; text: string }> = [
  { label: "Specific examples", text: "Give specific, concrete examples." },
  { label: "Code first", text: "Show code for every idea." },
  { label: "Analogies", text: "Use analogies to everyday things." },
  // Deliberately not "keep it short": every Read passage has a minimum length
  // the lesson agent's validator enforces, and a starter that argues with it
  // only buys failed attempts. This asks for directness instead.
  { label: "Straight to the point", text: "Skip the preamble and get straight to the point." },
];

/** Append `text` to `value` as its own sentence, unless it is already there. */
export function appendStarter(value: string, text: string): string {
  const trimmed = value.trim();
  if (trimmed.includes(text)) return value;
  return trimmed ? `${trimmed} ${text}` : text;
}

/**
 * Whether tapping a starter would push the text past the server's bound. The
 * textarea's `maxLength` only stops typing and pasting, so a starter that does
 * not fit is disabled instead of producing a value the server rejects.
 */
export function starterFits(value: string, text: string): boolean {
  return appendStarter(value, text).length <= TEACHING_STYLE_MAX_LENGTH;
}

/**
 * The Teaching style textarea plus its starters, shared by the create form
 * (`routes/new.tsx`) and the path view's edit card, so both write the same
 * thing the same way. Controlled: the caller owns the value.
 */
export function TeachingStyleField({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <>
      <textarea
        id={id}
        name="teaching_style"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="e.g. More concrete examples, compare it to Python…"
        rows={3}
        maxLength={TEACHING_STYLE_MAX_LENGTH}
        className="mt-3 w-full resize-y rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none"
      />
      <fieldset className="mt-2 flex flex-wrap gap-2">
        <legend className="sr-only">Teaching style starters</legend>
        {TEACHING_STYLE_STARTERS.map((starter) => (
          <button
            key={starter.label}
            type="button"
            onClick={() => onChange(appendStarter(value, starter.text))}
            disabled={!starterFits(value, starter.text)}
            className="rounded-full border border-faint px-3 py-1 text-xs text-porcelain transition-colors hover:border-teal disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-faint focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal"
          >
            {starter.label}
          </button>
        ))}
      </fieldset>
    </>
  );
}
