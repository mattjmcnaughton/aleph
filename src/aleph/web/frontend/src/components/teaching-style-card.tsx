import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type PathDetail, pathQueryKey, updateTeachingStyle } from "../lib/api";
import { TeachingStyleField } from "./teaching-style-field";

/**
 * How many unfinished lessons are already written, or being written right now:
 * the ones a style edit will **not** reach, because the change is forward-only
 * (docs/CONTEXT.md: Teaching style). A `generating` lesson counts because it
 * loaded the style when it started. Completed lessons are left out; they are
 * behind the learner.
 */
export function writtenAheadCount(detail: Pick<PathDetail, "units">): number {
  return detail.units
    .flatMap((unit) => unit.lessons)
    .filter(
      (lesson) =>
        (lesson.generation_state === "generated" || lesson.generation_state === "generating") &&
        lesson.unlock_state !== "complete",
    ).length;
}

/**
 * The path's Teaching style, shown under the title and editable in place.
 *
 * Non-optimistic like `PathTitle`: the card shows the cached value until the
 * `PUT` succeeds, then writes the returned detail into the poll cache. Saving
 * a blank box clears the style (`null` on the wire). The edit form says which
 * lessons keep their current style, because the server regenerates nothing.
 *
 * The caller keys this on the path id so a switch between paths resets any
 * in-progress draft (the same hazard `PathTitle`'s key comment documents).
 */
export function TeachingStyleCard({ detail }: { detail: PathDetail }) {
  const queryClient = useQueryClient();
  const current = detail.teaching_style;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(current ?? "");

  const mutation = useMutation({
    mutationFn: updateTeachingStyle,
    onSuccess: (updated) => {
      queryClient.setQueryData(pathQueryKey(detail.id), updated);
      setEditing(false);
    },
  });

  function startEditing() {
    mutation.reset();
    setDraft(current ?? "");
    setEditing(true);
  }

  function save() {
    if (mutation.isPending) return;
    const trimmed = draft.trim();
    mutation.mutate({ pathId: detail.id, teachingStyle: trimmed ? trimmed : null });
  }

  if (!editing) {
    return (
      <section
        data-testid="teaching-style"
        aria-labelledby="teaching-style-heading"
        className="mt-5 rounded-lg bg-surface px-4 py-3 shadow-sm"
      >
        <div className="flex items-center justify-between gap-4">
          <h2 id="teaching-style-heading" className="kicker">
            Teaching style
          </h2>
          <button
            type="button"
            onClick={startEditing}
            data-testid="teaching-style-edit"
            className="text-sm font-medium text-teal transition-colors hover:text-teal-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal"
          >
            {current ? "Edit" : "Add"}
          </button>
        </div>
        <p
          data-testid="teaching-style-text"
          className={`mt-1.5 whitespace-pre-line text-sm leading-6 ${current ? "text-porcelain" : "text-slate"}`}
        >
          {current ?? "None yet. Tell Aleph how you like lessons taught."}
        </p>
      </section>
    );
  }

  const ahead = writtenAheadCount(detail);
  return (
    <form
      data-testid="teaching-style-form"
      className="mt-5 rounded-lg bg-surface px-4 py-4 shadow-sm"
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
    >
      <label htmlFor="teaching-style-input" className="kicker">
        Teaching style
      </label>
      <TeachingStyleField id="teaching-style-input" value={draft} onChange={setDraft} />
      <p data-testid="teaching-style-scope" className="mt-3 text-sm leading-6 text-mist">
        Applies to lessons written from now on.
        {ahead > 0
          ? ` ${ahead === 1 ? "1 lesson is" : `${ahead} lessons are`} already written and ${
              ahead === 1 ? "keeps its" : "keep their"
            } current style.`
          : null}
      </p>
      <div className="mt-3 flex gap-2">
        <button
          type="submit"
          disabled={mutation.isPending}
          data-testid="teaching-style-save"
          className="rounded-md bg-teal px-4 py-1.5 text-sm font-semibold text-night transition-colors hover:bg-teal-bright disabled:cursor-not-allowed disabled:opacity-50"
        >
          {mutation.isPending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={() => {
            mutation.reset();
            setEditing(false);
          }}
          data-testid="teaching-style-cancel"
          className="rounded-md border border-divider px-4 py-1.5 text-sm font-semibold text-mist transition-colors hover:text-porcelain"
        >
          Cancel
        </button>
      </div>
      {mutation.isError ? (
        <p
          role="alert"
          data-testid="teaching-style-error"
          className="mt-2 text-sm leading-6 text-danger"
        >
          Couldn't save your teaching style. Check your connection and try again.
        </p>
      ) : null}
    </form>
  );
}
