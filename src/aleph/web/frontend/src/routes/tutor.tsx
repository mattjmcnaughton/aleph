import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Breadcrumbs } from "../components/breadcrumbs";
import { Markdown } from "../components/markdown";
import { LT_BUTTON, LT_FIELD, Recommendations } from "../components/learning-tutor/recommendations";
import {
  type LearningTutor,
  useLearningTutor,
} from "../components/learning-tutor/use-learning-tutor";
import { sessionQueryOptions } from "../lib/auth";
import { handleComposerKeyDown } from "../lib/composer-keys";
import { useFeatureFlag } from "../lib/feature-flags";
import { LEARNING_KEY, getLearningHistory } from "../lib/learning-tutor";
import type { LearningTutorOperation } from "../lib/learning-tutor-types";

export const Route = createFileRoute("/tutor")({
  validateSearch: (search: Record<string, unknown>): { recommendation?: string } => ({
    recommendation: typeof search.recommendation === "string" ? search.recommendation : undefined,
  }),
  component: TutorRoute,
});

function TutorRoute() {
  const user = useQuery(sessionQueryOptions).data?.user;
  if (!user?.feature_flags?.learning_tutor)
    return (
      <main className="mx-auto max-w-[480px] px-4 py-8">
        <h1 className="text-xl">Your account-wide Tutor isn't available.</h1>
        <Link to="/" className={LT_BUTTON}>
          Back home
        </Link>
      </main>
    );
  return <TutorPage key={user.id} account={user.id} />;
}

function TutorPage({ account }: { account: string }) {
  const tutor = useLearningTutor(account);
  const { recommendation } = Route.useSearch();
  const state = tutor.state.data;
  return (
    <main data-testid="learning-tutor" className="mx-auto w-full max-w-[1200px] px-4 py-8 lg:px-8">
      <Breadcrumbs current="Tutor" />
      <p className="kicker">Your Tutor</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">See the bigger picture.</h1>
      <p className="mt-3 max-w-[680px] text-mist">
        Connect your interests, continue what matters, or explore something new. You decide what to
        create.
      </p>
      {state && (
        <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
          <LearningConversation key={state.epoch} tutor={tutor} recommendation={recommendation} />
          <FocusEditor tutor={tutor} />
        </div>
      )}
      <Recommendations tutor={tutor} surface="tutor" />
    </main>
  );
}

function FocusEditor({ tutor }: { tutor: LearningTutor }) {
  const saved = tutor.state.data?.focus ?? "";
  const [draft, setDraft] = useState<string | null>(null);
  const editVersion = useRef(0);
  return (
    <aside className="rounded-lg border border-divider bg-surface p-5">
      <label htmlFor="learning-focus" className="kicker">
        Learning focus
      </label>
      <p className="mt-3 text-sm leading-6 text-mist">
        Optional. Save what you want to understand. Your conversation never changes this
        automatically. Clear the field and save to remove it.
      </p>
      <textarea
        id="learning-focus"
        className={LT_FIELD}
        rows={5}
        maxLength={2000}
        value={draft ?? saved}
        onChange={(event) => {
          editVersion.current++;
          setDraft(event.target.value);
        }}
      />
      <button
        type="button"
        className={`mt-3 ${LT_BUTTON}`}
        disabled={tutor.change.isPending}
        onClick={async () => {
          const submittedVersion = editVersion.current;
          try {
            await tutor.change.mutateAsync({ kind: "focus", text: draft ?? saved });
            if (editVersion.current === submittedVersion) setDraft(null);
          } catch {
            /* Keep the unsaved edit. */
          }
        }}
      >
        Save focus
      </button>
      <p className="mt-3 text-xs leading-5 text-slate">
        Changing focus invalidates previous recommendations. Deleting a source removes it from
        future context; old conversation mentions remain until you clear the conversation.
      </p>
    </aside>
  );
}

function LearningConversation({
  tutor,
  recommendation,
}: { tutor: LearningTutor; recommendation?: string }) {
  const state = tutor.state.data;
  const analyst = useFeatureFlag("analyst");
  const initialInput = tutor.uncertain?.kind === "turn" ? tutor.uncertain.content : "";
  const [text, setText] = useState(initialInput);
  const editVersion = useRef(0);
  const submitted = useRef<{ version: number; content: string; previousId?: string } | null>(null);
  const [confirm, setConfirm] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const clear = useRef<HTMLButtonElement>(null);
  const restored = useRef<string | null>(null);
  const history = useInfiniteQuery({
    queryKey: [...LEARNING_KEY, tutor.account, "history", state?.epoch, state?.history.before],
    queryFn: ({ pageParam, signal }) => getLearningHistory(pageParam as string, signal),
    initialPageParam: state?.history.before,
    getNextPageParam: (page) => page.before ?? undefined,
    enabled: false,
  });
  useEffect(() => {
    if (recommendation) input.current?.focus();
  }, [recommendation]);
  useEffect(() => {
    if (confirm) keep.current?.focus();
  }, [confirm]);
  useEffect(() => {
    const row = tutor.operation.data;
    const sent = submitted.current;
    if (
      row?.status === "succeeded" &&
      row.kind === "turn" &&
      sent &&
      row.id !== sent.previousId &&
      row.id === tutor.send.data?.id &&
      row.input?.content === sent.content
    ) {
      if (editVersion.current === sent.version) setText("");
      submitted.current = null;
    }
  }, [tutor.operation.data, tutor.send.data]);
  useEffect(() => {
    const row = tutor.operation.data;
    if (
      row?.kind === "turn" &&
      row.status === "failed" &&
      row.id !== restored.current &&
      typeof row.input?.content === "string"
    ) {
      restored.current = row.id;
      setText((current) => current || String(row.input?.content));
    }
  }, [tutor.operation.data]);
  if (!state) return null;
  const selected = state.recommendations.find(
    (card) => card.id === recommendation && (analyst || card.kind !== "beat"),
  );
  const turns = [
    ...(history.data?.pages.flatMap((page) => page.items) ?? []),
    ...state.history.items,
  ];
  const unique = [...new Map(turns.map((turn) => [turn.id, turn])).values()].sort((a, b) =>
    a.started_at.localeCompare(b.started_at),
  );
  const latest =
    tutor.operation.data?.output ?? state.history.items[0]?.output ?? state.latest_refresh?.output;
  const omitted = latest?.omitted;
  function send() {
    if (text.trim() && !tutor.busy && !tutor.uncertain && (!recommendation || selected)) {
      submitted.current = {
        version: editVersion.current,
        content: text.trim(),
        previousId: tutor.send.data?.id,
      };
      void tutor.start(text.trim(), selected?.id);
    }
  }
  return (
    <section className="min-w-0" aria-label="Learning conversation">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Learning conversation</h2>
        <button ref={clear} type="button" className={LT_BUTTON} onClick={() => setConfirm(true)}>
          Clear Learning conversation
        </button>
      </div>
      {confirm && (
        <div className="mt-4 rounded-md border border-danger-border bg-danger-bg p-4">
          <p className="text-sm leading-6">
            Clear messages and unaccepted recommendations, including dismissals? Your Learning focus
            and created paths and Beats stay. Running replies cannot return.
          </p>
          <div className="mt-3 flex gap-3">
            <button
              ref={keep}
              type="button"
              className={LT_BUTTON}
              onClick={() => {
                setConfirm(false);
                clear.current?.focus();
              }}
            >
              Keep conversation
            </button>
            <button
              type="button"
              className={LT_BUTTON}
              disabled={tutor.change.isPending}
              onClick={async () => {
                try {
                  await tutor.change.mutateAsync({ kind: "clear" });
                  setConfirm(false);
                  clear.current?.focus();
                } catch {
                  /* Keep confirmation open. */
                }
              }}
            >
              Confirm clear
            </button>
          </div>
        </div>
      )}
      {omitted && Object.values(omitted).some((n) => n > 0) && (
        <p className="mt-3 text-xs text-slate">
          Sampled context; omitted {omitted.paths} paths
          {analyst ? `, ${omitted.beats} Beats, ${omitted.briefs} Brief metadata records` : ""},{" "}
          {omitted.turns} older turns and {omitted.recommendations} recommendations.
        </p>
      )}
      {state.history.before && (!history.data || history.hasNextPage) && (
        <button
          type="button"
          className={`mt-3 ${LT_BUTTON}`}
          disabled={history.isFetching}
          onClick={() => void history.fetchNextPage()}
        >
          Older conversation
        </button>
      )}
      {history.isError && <p role="alert">Couldn't load older conversation. Try again.</p>}
      <div className="mt-4 space-y-6">
        {unique.length === 0 && (
          <p className="rounded-lg border border-divider p-5 text-mist">
            What would you like to understand? Ask freely; a Learning focus is optional.
          </p>
        )}
        {unique.map((turn) => (
          <Turn key={turn.id} turn={turn} analyst={analyst} />
        ))}
      </div>
      {selected && (
        <p className="mt-5 rounded-md border border-iris/40 p-3 text-sm">
          Discussing: <strong>{selected.topic}</strong>. Nothing is created by discussing it.
        </p>
      )}
      {recommendation && !selected && (
        <p role="alert" className="mt-5 text-sm text-danger">
          That recommendation is no longer active.{" "}
          <Link to="/tutor" search={{}} className="underline">
            Continue without it
          </Link>
        </p>
      )}
      <form
        className="mt-5"
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
      >
        <label htmlFor="learning-message" className="text-sm font-medium">
          Message your Tutor
        </label>
        <textarea
          ref={input}
          id="learning-message"
          rows={4}
          maxLength={2000}
          className={LT_FIELD}
          value={text}
          onChange={(event) => {
            editVersion.current++;
            setText(event.target.value);
          }}
          onKeyDown={(event) => handleComposerKeyDown(event, send)}
        />
        <div className="mt-2 flex items-center justify-between gap-3">
          <span className="text-xs text-slate">Enter for a new line · Shift+Enter to send</span>
          <button
            type="submit"
            className={`${LT_BUTTON} text-teal`}
            disabled={
              !text.trim() ||
              tutor.busy ||
              Boolean(tutor.uncertain) ||
              Boolean(recommendation && !selected)
            }
          >
            Send
          </button>
        </div>
      </form>
    </section>
  );
}

function Turn({ turn, analyst }: { turn: LearningTutorOperation; analyst: boolean }) {
  return (
    <article data-testid="learning-turn" className="space-y-3">
      <p className="whitespace-pre-wrap rounded-lg bg-elevated p-4 text-sm">
        {String(turn.input?.content ?? "")}
      </p>
      <Markdown>{turn.output?.reply ?? ""}</Markdown>
      <div className="flex flex-wrap gap-2">
        {turn.output?.existing_work?.map((work) => {
          const [kind, id] = work.supplied_target_key.split(":");
          if (
            kind !== work.kind ||
            !/^[0-9a-f-]{36}$/i.test(id ?? "") ||
            (!analyst && kind !== "path")
          )
            return null;
          if (kind === "path")
            return (
              <Link key={id} className={LT_BUTTON} to="/paths/$pathId" params={{ pathId: id }}>
                Continue path
              </Link>
            );
          if (kind === "beat")
            return (
              <Link key={id} className={LT_BUTTON} to="/beats/$beatId" params={{ beatId: id }}>
                Open Beat
              </Link>
            );
          if (kind === "brief")
            return (
              <Link key={id} className={LT_BUTTON} to="/briefs/$briefId" params={{ briefId: id }}>
                Read Brief
              </Link>
            );
          return null;
        })}
      </div>
    </article>
  );
}
