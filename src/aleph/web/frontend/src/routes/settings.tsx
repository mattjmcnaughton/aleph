// `/settings` (CONTEXT.md: Settings): the learner's own controls over their
// experience. One section per surface that has a setting; the first — and for
// now the only — is Flashcards' **Auto-draft**. Reached from the app header's
// gear, on every route, because a setting is something a learner goes looking
// for rather than something a surface should push at them.
//
// Each switch writes straight through (`useUpdateSettings`): there is no Save
// button and no form state to reconcile, because a setting is a single
// independent value, and "tap, done" is the phone-first shape. The switch
// reads back from the cached session the mutation updates, so what it shows
// is always what the server holds — never an optimistic guess that a failed
// PATCH would have to walk back.

import { createFileRoute } from "@tanstack/react-router";
import { Workspace } from "../components/workspace";
import { useFeatureFlag } from "../lib/feature-flags";
import { useSettings, useUpdateSettings } from "../lib/settings";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

function SettingsPage() {
  const flashcardsEnabled = useFeatureFlag("flashcards");
  const settings = useSettings();
  const update = useUpdateSettings();

  return (
    <Workspace testid="settings-page" width="lesson">
      <p className="kicker">Settings</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">Your experience.</h1>
      <p className="mt-3 text-base leading-6 text-mist">
        Aleph's defaults suit most learners. Change what doesn't suit you — each switch saves as you
        flip it.
      </p>

      {flashcardsEnabled ? (
        <section className="mt-10" aria-labelledby="settings-flashcards-heading">
          <h2
            id="settings-flashcards-heading"
            className="font-mono text-[11px] font-medium uppercase tracking-kicker text-slate"
          >
            Flashcards
          </h2>
          <div className="mt-3 rounded-lg border border-divider bg-surface">
            <SettingSwitch
              testid="setting-auto-draft-flashcards"
              label="Draft flashcards automatically"
              description="Aleph drafts a few cards from each lesson as you open it and offers them when you finish. Off, it drafts only when you ask from a finished lesson."
              checked={settings.auto_draft_flashcards}
              disabled={update.isPending}
              onToggle={(next) => update.mutate({ auto_draft_flashcards: next })}
            />
          </div>
          {update.isError ? (
            <p
              data-testid="settings-save-error"
              aria-live="assertive"
              className="mt-3 text-sm leading-6 text-danger"
            >
              That didn't save. Check your connection and try again.
            </p>
          ) : null}
        </section>
      ) : (
        // The one setting so far belongs to a surface the `flashcards` flag
        // can gate off entirely (Phase 3 TDD D10); a switch for a surface the
        // learner cannot see would be a promise about nothing.
        <p data-testid="settings-empty" className="mt-10 text-sm leading-6 text-mist">
          Nothing to change right now.
        </p>
      )}
    </Workspace>
  );
}

/**
 * One setting row: label + description on the left, a switch on the right.
 * A real `role="switch"` button — `aria-checked` carries the state, the label
 * is its accessible name — so a screen reader announces "Draft flashcards
 * automatically, switch, on", and a test can find it the same way.
 */
function SettingSwitch({
  testid,
  label,
  description,
  checked,
  disabled,
  onToggle,
}: {
  testid: string;
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onToggle: (next: boolean) => void;
}) {
  const labelId = `${testid}-label`;
  const descriptionId = `${testid}-description`;
  return (
    <div className="flex items-start justify-between gap-4 p-4">
      <div className="min-w-0">
        <p id={labelId} className="text-sm font-semibold leading-snug text-porcelain">
          {label}
        </p>
        <p id={descriptionId} className="mt-1 text-xs leading-5 text-mist">
          {description}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        data-testid={testid}
        aria-checked={checked}
        aria-labelledby={labelId}
        aria-describedby={descriptionId}
        disabled={disabled}
        onClick={() => onToggle(!checked)}
        className={`relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal disabled:cursor-not-allowed disabled:opacity-50 ${
          checked ? "bg-teal" : "bg-porcelain/15"
        }`}
      >
        <span
          aria-hidden="true"
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-night shadow-sm transition-transform ${
            checked ? "translate-x-[1.375rem]" : "translate-x-0.5"
          }`}
          style={{ left: 0 }}
        />
      </button>
    </div>
  );
}
