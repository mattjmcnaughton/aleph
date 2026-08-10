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
  isValidationError,
  pathQueryKey,
  pathQueryOptions,
  retryPath,
} from "../lib/api";
import { ModelPicker } from "../components/model-picker";
import { Breadcrumbs } from "../components/breadcrumbs";
import { LevelFieldset } from "../components/level-fieldset";
import { PRIMARY_CTA, RetryNotices, Spinner, StateCard } from "../components/state-card";
import { sessionQueryOptions } from "../lib/auth";
import {
  GUIDANCE_MAX_LENGTH,
  MODEL_SLOT_DEFAULT,
  TOPIC_MAX_LENGTH,
  buildCreatePathInput,
  canSubmitTopic,
  deriveOnboardingPhase,
} from "../lib/onboarding";
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
  // Optional creation input alongside topic (§5.1, docs/api.md): free text the
  // learner pastes to shape the outline. Preserved across the failed→editing
  // round trip exactly like topic/level, below.
  const [guidance, setGuidance] = useState("");
  const [pathId, setPathId] = useState<string | null>(null);
  // Admin model slots (§5.3/D14). `MODEL_SLOT_DEFAULT` means "no override" —
  // `buildCreatePathInput` drops the key rather than sending an empty id.
  const [modelOutline, setModelOutline] = useState(MODEL_SLOT_DEFAULT);
  const [modelLesson, setModelLesson] = useState(MODEL_SLOT_DEFAULT);

  // The session is already resolved (the root route's `beforeLoad` awaits it),
  // so this is a cache read: is this learner an admin, and which model ids may
  // they pin? Both answers come from the server — the picker hardcodes neither.
  const session = useQuery(sessionQueryOptions).data;
  const user = session?.authenticated ? session.user : null;
  const isAdmin = user?.is_admin ?? false;
  const modelAllowlist = user?.model_allowlist ?? [];

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
    onError: (error, variables) => {
      // A rejected model id says the cached session's allowlist is stale — it
      // is the *only* thing that can produce this error. Refetch it so the
      // picker re-offers what the server will actually accept, instead of
      // leaving the admin to choose from the same dead list.
      const sentModel = "model_outline" in variables || "model_lesson" in variables;
      if (sentModel && isValidationError(error)) {
        queryClient.invalidateQueries({ queryKey: sessionQueryOptions.queryKey });
      }
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
    createMutation.mutate(
      buildCreatePathInput({ topic, level, guidance, modelOutline, modelLesson }),
    );
  }

  const rateLimited = createMutation.isError && isRateLimited(createMutation.error);
  // Which `422` is this? `POST /paths` validates the topic, the level enum *and*
  // the model overrides, so the status alone can't say — and blaming the picker
  // for, say, an over-long topic would show an admin allowlist copy about an
  // error they cannot fix that way. So attribute it structurally: claim the 422
  // for the picker only when the payload that was actually rejected carried a
  // model field. `variables` is that payload (the failed mutation's argument),
  // which also makes the admin check redundant — a body with a model key could
  // only have come from a rendered picker.
  const sentModelOverride =
    createMutation.variables !== undefined &&
    ("model_outline" in createMutation.variables || "model_lesson" in createMutation.variables);
  const modelRejected =
    createMutation.isError && sentModelOverride && isValidationError(createMutation.error);
  // Everything else — including a 422 the picker did not cause — falls through
  // to the generic surface, so no failed create is ever silent.
  const createFailed = createMutation.isError && !rateLimited && !modelRejected;

  return (
    <main className="mx-auto w-full max-w-[480px] px-4 py-8">
      <Breadcrumbs current="New path" />

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
          guidance={guidance}
          onTopicChange={setTopic}
          onLevelChange={setLevel}
          onGuidanceChange={setGuidance}
          onSubmit={onSubmit}
          submitting={createMutation.isPending}
          rateLimited={rateLimited}
          errored={createFailed}
          // Assembled here rather than relayed as eight props the form only
          // forwards: the picker's state and its error live in this component,
          // and the form's only stake in it is where it sits between the level
          // fieldset and the submit button.
          modelPicker={
            <ModelPicker
              isAdmin={isAdmin}
              allowlist={modelAllowlist}
              outline={modelOutline}
              lesson={modelLesson}
              onOutlineChange={setModelOutline}
              onLessonChange={setModelLesson}
              error={
                modelRejected
                  ? "That model isn't in the allowlist anymore. Pick another (or the server default) and try again."
                  : undefined
              }
            />
          }
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
  guidance: string;
  onTopicChange: (value: string) => void;
  onLevelChange: (value: Level) => void;
  onGuidanceChange: (value: string) => void;
  onSubmit: (event: React.FormEvent) => void;
  submitting: boolean;
  rateLimited: boolean;
  errored: boolean;
  /**
   * The admin model picker (§5.3/D14), already assembled by the caller — it
   * renders nothing for everyone else, so the form places it unconditionally.
   */
  modelPicker: React.ReactNode;
}

function OnboardingForm({
  topic,
  level,
  guidance,
  onTopicChange,
  onLevelChange,
  onGuidanceChange,
  onSubmit,
  submitting,
  rateLimited,
  errored,
  modelPicker,
}: FormProps) {
  return (
    <form className="mt-8" onSubmit={onSubmit} noValidate>
      {rateLimited ? (
        <p
          data-testid="onboarding-ratelimit"
          role="alert"
          className="mb-5 rounded-md border border-divider bg-elevated px-4 py-3 text-sm leading-6 text-mist"
        >
          You've reached today's limit for new paths. Your topic is saved — try again tomorrow.
        </p>
      ) : null}
      {errored ? (
        <p
          data-testid="onboarding-error"
          role="alert"
          className="mb-5 rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-danger"
        >
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
        // Mirrors `TopicStr` (`dtos/paths.py`): stop an over-long topic here
        // rather than round-tripping it into a 422 the learner can't read.
        maxLength={TOPIC_MAX_LENGTH}
        className="w-full rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none"
      />

      <div className="mt-6">
        <label htmlFor="onboarding-guidance" className="text-sm font-medium text-porcelain">
          Additional guidance
        </label>
        <p className="mt-1 text-sm leading-6 text-mist">
          Optional. Anything about how you want the path shaped — the stages to cover, what to
          emphasise or skip, how deep to go.
        </p>
        <textarea
          id="onboarding-guidance"
          name="guidance"
          value={guidance}
          onChange={(event) => onGuidanceChange(event.target.value)}
          placeholder="e.g. Cover X before Y, skip the history, go deep on the tooling…"
          rows={6}
          // Mirrors `GuidanceStr` (`dtos/paths.py`) the way the topic input mirrors
          // `TopicStr` above — same reasoning, same cap-at-the-keyboard rule.
          maxLength={GUIDANCE_MAX_LENGTH}
          className="mt-3 w-full resize-y rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none"
        />
      </div>

      <LevelFieldset level={level} onChange={onLevelChange} idPrefix="level" />

      {modelPicker}

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
