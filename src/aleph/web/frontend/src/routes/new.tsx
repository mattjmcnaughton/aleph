import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  type Level,
  PATHS_LIST_QUERY_KEY,
  type PathDetail,
  createPath,
  isPathStatusTerminal,
  isRateLimited,
  pathQueryKey,
  pathQueryOptions,
  retryPath,
} from "../lib/api";
import { PRIMARY_CTA, RetryNotices, Spinner, StateCard } from "../components/state-card";
import { LEVELS, canSubmitTopic, deriveOnboardingPhase } from "../lib/onboarding";
import { makePollingRefetchInterval } from "../lib/polling";
import { useRetryGeneration } from "../lib/use-retry-generation";

export const Route = createFileRoute("/new")({
  component: NewPath,
});

const pathPollConfig = {
  isTerminal: (data: PathDetail | undefined) => isPathStatusTerminal(data?.status),
};

// Onboarding (§5.1, §5.6): capture topic + level → POST → poll the outline via
// the shared polling helper → land on ready (navigate) / refused (W7, graceful)
// / failed (W8, one-tap retry). Server state is poll-driven; the phase is the
// pure `deriveOnboardingPhase` state machine.
//
// Scope (AL-061): free-text topic + level only. The mock's popular-topic
// suggestion chips are deliberately out of scope for this ticket and land later.
function NewPath() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [topic, setTopic] = useState("");
  const [level, setLevel] = useState<Level>("new_to_it");
  const [pathId, setPathId] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: createPath,
    onSuccess: async (created) => {
      setPathId(created.id);
      // The switcher's cached list no longer matches the server. Nothing else
      // would correct it: the list is fresh for `staleTime` (30s) so a remount
      // serves it straight from cache, and its poll only runs while a row is
      // non-terminal — a list that predates this path has no such row. So a
      // learner who backs out of onboarding would land on a home screen missing
      // the path they just started.
      await queryClient.invalidateQueries({ queryKey: PATHS_LIST_QUERY_KEY });
    },
  });

  const pathQuery = useQuery({
    ...pathQueryOptions(pathId),
    refetchInterval: makePollingRefetchInterval(pathPollConfig),
  });

  const retry = useRetryGeneration({ mutationFn: retryPath, queryKey: pathQueryKey });

  const status = pathQuery.data?.status;

  // Ready is a side effect, not a rendered phase: hand off to the path view.
  // The ref guards against StrictMode's double-invoked effect firing two navs;
  // `replace` keeps the transient /new generating screen out of history.
  const navigatedRef = useRef(false);
  useEffect(() => {
    if (pathId && status === "ready" && !navigatedRef.current) {
      navigatedRef.current = true;
      navigate({ to: "/paths/$pathId", params: { pathId }, replace: true });
    }
  }, [pathId, status, navigate]);

  const phase = deriveOnboardingPhase({ pathId, status });

  function backToEditing() {
    setPathId(null);
    navigatedRef.current = false;
    createMutation.reset();
    retry.reset();
  }

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmitTopic(topic)) return;
    createMutation.mutate({ topic: topic.trim(), level });
  }

  const rateLimited = createMutation.isError && isRateLimited(createMutation.error);
  const createFailed = createMutation.isError && !rateLimited;

  return (
    <main className="mx-auto w-full max-w-[480px] px-4 py-8">
      <p className="kicker">New path</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        What do you want to learn?
      </h1>
      <p className="mt-3 text-base leading-6 text-mist">
        Name a topic or a goal and Aleph drafts a path you can work through, lesson by lesson.
      </p>

      {phase === "editing" ? (
        <OnboardingForm
          topic={topic}
          level={level}
          onTopicChange={setTopic}
          onLevelChange={setLevel}
          onSubmit={onSubmit}
          submitting={createMutation.isPending}
          rateLimited={rateLimited}
          errored={createFailed}
        />
      ) : null}

      {phase === "generating" ? <GeneratingState /> : null}

      {phase === "refused" ? (
        <RefusedState
          message={pathQuery.data?.refusal_message ?? undefined}
          onTryDifferent={backToEditing}
        />
      ) : null}

      {phase === "failed" ? (
        <FailedState
          onRetry={() => pathId && retry.retry(pathId)}
          retrying={retry.retrying}
          retryRateLimited={retry.rateLimited}
          retryErrored={retry.errored}
          onEdit={backToEditing}
        />
      ) : null}
    </main>
  );
}

interface FormProps {
  topic: string;
  level: Level;
  onTopicChange: (value: string) => void;
  onLevelChange: (value: Level) => void;
  onSubmit: (event: React.FormEvent) => void;
  submitting: boolean;
  rateLimited: boolean;
  errored: boolean;
}

function OnboardingForm({
  topic,
  level,
  onTopicChange,
  onLevelChange,
  onSubmit,
  submitting,
  rateLimited,
  errored,
}: FormProps) {
  return (
    <form className="mt-8" onSubmit={onSubmit} noValidate>
      {rateLimited ? (
        <p
          data-testid="onboarding-ratelimit"
          className="mb-5 rounded-md border border-divider bg-elevated px-4 py-3 text-sm leading-6 text-mist"
        >
          You've reached today's limit for new paths. Your topic is saved — try again tomorrow.
        </p>
      ) : null}
      {errored ? (
        <p className="mb-5 rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-danger">
          Something went wrong starting your path. Try again.
        </p>
      ) : null}

      <label htmlFor="onboarding-topic" className="sr-only">
        Topic
      </label>
      <input
        id="onboarding-topic"
        name="topic"
        type="text"
        value={topic}
        onChange={(event) => onTopicChange(event.target.value)}
        placeholder="e.g. TypeScript generics, Rust ownership…"
        autoComplete="off"
        className="w-full rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none"
      />

      <fieldset className="mt-6">
        <legend className="kicker">How much do you know already?</legend>
        <div className="mt-3 grid gap-2">
          {LEVELS.map((option) => {
            const id = `level-${option.value}`;
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
                  onChange={() => onLevelChange(option.value)}
                  className="sr-only"
                />
                {option.label}
              </label>
            );
          })}
        </div>
      </fieldset>

      <button
        type="submit"
        disabled={!canSubmitTopic(topic) || submitting}
        className="mt-8 inline-flex w-full items-center justify-center rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright disabled:cursor-not-allowed disabled:opacity-50"
      >
        {submitting ? "Starting…" : "Build my path"}
      </button>
    </form>
  );
}

function GeneratingState() {
  return (
    <StateCard testid="onboarding-generating" ariaLive="polite" spacing="mt-8">
      <Spinner />
      <h2 className="mt-4 text-lg font-semibold">Drafting your path…</h2>
      <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
        Aleph is generating an outline of units and lessons. This takes a moment.
      </p>
    </StateCard>
  );
}

function RefusedState({
  message,
  onTryDifferent,
}: { message?: string; onTryDifferent: () => void }) {
  return (
    <StateCard
      testid="onboarding-refused"
      variant="refusal"
      dataVariant="refusal"
      ariaLive="polite"
      spacing="mt-8"
    >
      <h2 className="text-lg font-semibold text-iris-300">This topic is out of scope.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-porcelain">
        {message ??
          "Aleph can't build a path on this topic. Try a different topic and we'll draft one."}
      </p>
      <button
        type="button"
        onClick={onTryDifferent}
        className="mt-6 inline-flex w-full items-center justify-center rounded-md border border-iris-500 px-4 py-3 text-sm font-semibold text-iris-300 transition-colors hover:bg-iris-700/40"
      >
        Try a different topic
      </button>
    </StateCard>
  );
}

function FailedState({
  onRetry,
  retrying,
  retryRateLimited,
  retryErrored,
  onEdit,
}: {
  onRetry: () => void;
  retrying: boolean;
  retryRateLimited: boolean;
  retryErrored: boolean;
  onEdit: () => void;
}) {
  return (
    <StateCard
      testid="onboarding-failed"
      variant="error"
      dataVariant="error"
      ariaLive="assertive"
      spacing="mt-8"
    >
      <h2 className="text-lg font-semibold text-danger">Generation didn't finish.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-porcelain">
        We couldn't draft your path this time. Your topic and level are saved — retry when you're
        ready.
      </p>
      <RetryNotices
        testidPrefix="onboarding"
        rateLimited={retryRateLimited}
        errored={retryErrored}
        rateLimitMessage="You've reached today's limit for new paths. Your topic is saved — try again tomorrow."
      />
      <button type="button" onClick={onRetry} disabled={retrying} className={`mt-6 ${PRIMARY_CTA}`}>
        {retrying ? "Retrying…" : "Try again"}
      </button>
      <button
        type="button"
        onClick={onEdit}
        className="mt-3 inline-flex w-full items-center justify-center rounded-md border border-divider px-4 py-3 text-sm font-semibold text-mist transition-colors hover:text-porcelain"
      >
        Edit topic
      </button>
    </StateCard>
  );
}
