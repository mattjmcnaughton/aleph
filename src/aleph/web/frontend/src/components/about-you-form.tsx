import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ABOUT_YOU_QUERY_KEY, type AboutYou, getAboutYou, putAboutYou } from "../lib/api";
import { appendStarter } from "./teaching-style-field";

export const ABOUT_YOU_MAX_LENGTH = 2000;

const STARTERS = ["My field or job: ", "Examples I relate to: ", "How I learn best: ", "Skip: "];

/**
 * The About you editor (docs/CONTEXT.md: About you). Non-optimistic: the
 * cache is written from the `PUT` response. Saving a blank box clears it.
 * Forward-only, so the confirmation says what does not change.
 */
export function AboutYouForm() {
  const query = useQuery({ queryKey: ABOUT_YOU_QUERY_KEY, queryFn: getAboutYou });
  if (query.isError) {
    return (
      <p role="alert" className="text-sm text-danger">
        Couldn't load About you. Reload to try again.
      </p>
    );
  }
  if (!query.data) return null;
  return <Editor initial={query.data.about_you ?? ""} />;
}

function Editor({ initial }: { initial: string }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(initial);
  const [saved, setSaved] = useState(initial);
  const mutation = useMutation({
    mutationFn: putAboutYou,
    onSuccess: (data: AboutYou) => {
      queryClient.setQueryData(ABOUT_YOU_QUERY_KEY, data);
      setSaved(data.about_you ?? "");
      setDraft(data.about_you ?? "");
    },
  });
  const dirty = draft.trim() !== saved.trim();

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!dirty || mutation.isPending) return;
        const trimmed = draft.trim();
        mutation.mutate(trimmed ? trimmed : null);
      }}
    >
      <label htmlFor="about-you" className="sr-only">
        About you
      </label>
      <textarea
        id="about-you"
        data-testid="about-you-input"
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          mutation.reset();
        }}
        rows={6}
        maxLength={ABOUT_YOU_MAX_LENGTH}
        placeholder="e.g. I work in clinical research. Use medical examples where they fit."
        className="w-full resize-y rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none"
      />
      <div className="mt-1 flex justify-between text-xs text-slate">
        <span>Private to your account</span>
        <span>
          {draft.length} / {ABOUT_YOU_MAX_LENGTH}
        </span>
      </div>
      {draft.trim() ? null : (
        <fieldset className="mt-3 flex flex-wrap gap-2">
          <legend className="sr-only">About you starters</legend>
          {STARTERS.map((starter) => (
            <button
              key={starter}
              type="button"
              onClick={() => setDraft(`${appendStarter(draft, starter).trimEnd()} `)}
              className="rounded-full border border-faint px-3 py-1 text-xs text-porcelain transition-colors hover:border-teal"
            >
              {starter.replace(/: $/, "")}
            </button>
          ))}
        </fieldset>
      )}
      <p className="mt-5 text-sm leading-6 text-mist">
        Read by new path outlines, lessons written from now on, the tutor, and Shape your path.
        Briefs leave it out so they stay anchored to their Sources.
      </p>
      {mutation.isSuccess ? (
        <output
          data-testid="about-you-saved"
          className="mt-4 block border-l-2 border-teal pl-3 text-sm leading-6 text-mist"
        >
          <span className="font-medium text-porcelain">Saved.</span> Lessons written from now on use
          it. Lessons already written keep their text.
        </output>
      ) : null}
      {mutation.isError ? (
        <p role="alert" className="mt-4 text-sm text-danger">
          Couldn't save. Try again.
        </p>
      ) : null}
      <button
        type="submit"
        disabled={!dirty || mutation.isPending}
        className="mt-6 inline-flex w-full items-center justify-center rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright disabled:cursor-not-allowed disabled:opacity-50"
      >
        {mutation.isPending ? "Saving…" : draft.trim() || !saved ? "Save" : "Clear"}
      </button>
    </form>
  );
}
