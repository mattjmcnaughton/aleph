import type { Level } from "../lib/api";
import { LEVELS } from "../lib/onboarding";

export interface LevelFieldsetProps {
  level: Level;
  onChange: (level: Level) => void;
  /**
   * Prefix for each radio's `id` (and its `<label htmlFor>` pair) — the two
   * callers need distinct ids so `document.getElementById` / a stray browser
   * autofill can never conflate `/new`'s and `/beats/new`'s controls, even
   * though only one of the two routes is ever mounted at a time.
   */
  idPrefix: string;
}

/**
 * The Level control (CONTEXT.md: Level) — "How much do you know already?",
 * the three-way `new_to_it` / `some_experience` / `work_in_it` radio group —
 * extracted out of `routes/new.tsx` so `routes/beats.new.tsx` shares this
 * implementation instead of a second, ~30-line copy (code-review FIX 10 on
 * AL-530). TDD §8 calls this "the existing three-way control **verbatim**",
 * which reads as reuse, not duplication — and the two forms' tests already
 * passed independently, so a fix applied to one copy (an a11y tweak, a copy
 * change) could silently miss the other. Markup, classes, and behaviour are
 * unchanged from `routes/new.tsx`'s original inline fieldset — only the
 * radio `id`s move behind `idPrefix` so the two mount sites never collide.
 */
export function LevelFieldset({ level, onChange, idPrefix }: LevelFieldsetProps) {
  return (
    <fieldset className="mt-6">
      <legend className="kicker">How much do you know already?</legend>
      <div className="mt-3 grid gap-2">
        {LEVELS.map((option) => {
          const id = `${idPrefix}-${option.value}`;
          const selected = level === option.value;
          return (
            <label
              key={option.value}
              htmlFor={id}
              className={`flex cursor-pointer items-center rounded-md border px-4 py-3 text-sm font-medium transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-teal has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-night ${
                selected
                  ? "border-teal bg-teal/10 text-porcelain"
                  : "border-divider bg-surface text-mist hover:text-porcelain"
              }`}
            >
              <input
                id={id}
                type="radio"
                name="level"
                value={option.value}
                checked={selected}
                onChange={() => onChange(option.value)}
                className="sr-only"
              />
              {option.label}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
