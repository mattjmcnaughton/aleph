import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { Breadcrumbs } from "../components/breadcrumbs";
import { Spinner } from "../components/state-card";
import { Workspace } from "../components/workspace";
import {
  LEARNING_SUMMARY_QUERY_KEY,
  type LearningSummary,
  type Suggestion,
  getLearningSummary,
  isRateLimited,
  triggerLearningSummary,
} from "../lib/api";
import { useFeatureFlag } from "../lib/feature-flags";
import { levelLabel } from "../lib/onboarding";
import { makePollingRefetchInterval } from "../lib/polling";

export const Route = createFileRoute("/learning")({ component: LearningPage });

const pollConfig = {
  isTerminal: (data: LearningSummary | undefined) => data?.state !== "generating",
};

const DATE = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

/**
 * The Learning summary (docs/CONTEXT.md): counted facts from progress, then
 * the model-written areas and suggestions with the date they were written.
 * A suggestion opens New path (or New beat) with its fields prefilled; nothing
 * is created until the learner submits that form.
 */
function LearningPage() {
  const enabled = useFeatureFlag("learning_summary");
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: LEARNING_SUMMARY_QUERY_KEY,
    queryFn: getLearningSummary,
    enabled,
    refetchInterval: makePollingRefetchInterval(pollConfig),
  });
  const trigger = useMutation({
    mutationFn: triggerLearningSummary,
    onSuccess: (data) => queryClient.setQueryData(LEARNING_SUMMARY_QUERY_KEY, data),
  });

  if (!enabled) {
    return (
      <Workspace testid="learning-summary" width="lesson">
        <Breadcrumbs current="Your learning" />
        <p className="text-mist">This page isn't available.</p>
      </Workspace>
    );
  }

  const data = query.data;
  const generating = data?.state === "generating" || trigger.isPending;

  return (
    <Workspace testid="learning-summary" width="lesson">
      <Breadcrumbs current="Your learning" />
      <p className="kicker">Your learning</p>
      <h1 className="mt-2 page-title">Where you've got to</h1>

      {query.isError ? (
        <p role="alert" className="mt-6 text-sm text-danger">
          Couldn't load your summary. Reload to try again.
        </p>
      ) : null}

      {data ? (
        <>
          <Facts facts={data.facts} />

          {data.state === "too_early" ? (
            <p data-testid="summary-too-early" className="mt-6 text-base leading-7 text-mist">
              Finish a few more lessons first. After {data.min_lessons_complete}, Aleph can say
              where you've got to and what to try next.
            </p>
          ) : null}

          {data.state === "none" && !generating ? (
            <div className="mt-6">
              <p className="text-base leading-7 text-mist">
                Aleph reads your paths and Beats, says where you stand in each, and suggests what to
                learn next.
              </p>
              <button
                type="button"
                data-testid="summary-write"
                onClick={() => trigger.mutate()}
                className="mt-5 inline-flex w-full items-center justify-center rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright"
              >
                Write my summary
              </button>
            </div>
          ) : null}

          {generating ? (
            <div
              data-testid="summary-generating"
              aria-live="polite"
              className="mt-6 flex items-center gap-3 text-sm text-mist"
            >
              <Spinner /> Reading your paths and Beats. This takes a moment.
            </div>
          ) : null}

          {data.state === "failed" && !generating ? (
            <div
              data-testid="summary-failed"
              role="alert"
              className="mt-6 rounded-lg border border-danger-border bg-danger-bg px-4 py-3"
            >
              <p className="font-medium text-danger">Couldn't write your summary</p>
              <p className="mt-1 text-sm text-mist">
                Your progress is safe. This is a problem on our side.
              </p>
              <button
                type="button"
                onClick={() => trigger.mutate()}
                className="mt-3 rounded-md border border-faint px-3 py-2 text-sm text-porcelain hover:border-teal"
              >
                Try again
              </button>
            </div>
          ) : null}

          {trigger.isError ? (
            <p role="alert" className="mt-4 text-sm text-mist">
              {isRateLimited(trigger.error)
                ? "Your summary was just written. Try again in a few minutes."
                : "Couldn't start your summary. Try again."}
            </p>
          ) : null}

          {data.summary ? (
            <Summary data={data} refreshing={generating} onRefresh={() => trigger.mutate()} />
          ) : null}
        </>
      ) : null}
    </Workspace>
  );
}

function Facts({ facts }: { facts: LearningSummary["facts"] }) {
  const items = [
    [facts.lessons_complete, facts.lessons_complete === 1 ? "lesson complete" : "lessons complete"],
    [facts.paths, facts.paths === 1 ? "path" : "paths"],
    [facts.beats, facts.beats === 1 ? "Beat followed" : "Beats followed"],
  ] as const;
  return (
    <dl
      data-testid="summary-facts"
      className="mt-6 grid grid-cols-3 gap-3 border-y border-divider py-3"
    >
      {items.map(([value, label]) => (
        <div key={label}>
          <dt className="sr-only">{label}</dt>
          <dd className="font-serif text-2xl tabular-nums">{value}</dd>
          <dd className="text-xs text-mist">{label}</dd>
        </div>
      ))}
    </dl>
  );
}

function Summary({
  data,
  refreshing,
  onRefresh,
}: {
  data: LearningSummary;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const summary = data.summary;
  if (!summary) return null;
  return (
    <section className="mt-6">
      <div className="flex items-center justify-between gap-3 text-sm text-slate">
        <span data-testid="summary-written">
          {data.written_at ? `Written ${DATE.format(new Date(data.written_at))}` : null}
        </span>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="min-h-11 text-teal-bright hover:underline disabled:opacity-50"
        >
          Refresh
        </button>
      </div>
      <ul className="mt-2 border-t border-divider">
        {summary.areas.map((area) => (
          <li key={area.title} className="border-b border-divider py-3">
            <p className="text-[15px] font-semibold">{area.title}</p>
            <p className="mt-0.5 text-sm leading-6 text-mist">{area.note}</p>
          </li>
        ))}
      </ul>
      <h2 className="mt-8 flex items-center gap-2 text-[15px] font-semibold">
        Next to learn
        <span className="rounded-full border border-iris/40 px-2 text-xs font-normal text-iris">
          Suggested
        </span>
      </h2>
      <ul className="mt-2 border-t border-divider">
        {summary.suggestions.map((suggestion) => (
          <SuggestionRow key={suggestion.topic} suggestion={suggestion} />
        ))}
      </ul>
    </section>
  );
}

function SuggestionRow({ suggestion }: { suggestion: Suggestion }) {
  const beat = suggestion.kind === "beat";
  return (
    <li data-testid="summary-suggestion" className="border-b border-divider py-4">
      <p className="flex items-center gap-1.5 text-sm text-mist">
        <span
          aria-hidden="true"
          className={`inline-block h-1.5 w-1.5 rounded-full ${beat ? "bg-iris" : "bg-teal"}`}
        />
        {beat ? "Beat, weekly" : "Path"} · {levelLabel(suggestion.level)}
      </p>
      <p className="mt-1 font-serif text-lg leading-snug">{suggestion.topic}</p>
      <p className="mt-1 text-sm leading-6 text-mist">{suggestion.why}</p>
      {beat ? (
        <Link
          to="/beats/new"
          search={{ topic: suggestion.topic, level: suggestion.level }}
          className="mt-3 inline-flex min-h-11 items-center rounded-md border border-iris px-4 text-sm font-semibold text-iris-300 hover:bg-iris-900"
        >
          Deploy analyst
        </Link>
      ) : (
        <Link
          to="/new"
          search={{
            topic: suggestion.topic,
            level: suggestion.level,
            guidance: suggestion.guidance || undefined,
            teaching_style: suggestion.teaching_style || undefined,
          }}
          data-testid="summary-start"
          className="mt-3 inline-flex min-h-11 items-center rounded-md bg-teal px-4 text-sm font-semibold text-night hover:bg-teal-bright"
        >
          Start this path
        </Link>
      )}
    </li>
  );
}
