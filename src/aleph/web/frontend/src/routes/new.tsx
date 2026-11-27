import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  type Level,
  PATHS_LIST_QUERY_KEY,
  type PathDetail,
  type PathDraft,
  createPath,
  isPathStatusTerminal,
  isRateLimited,
  isValidationError,
  pathQueryKey,
  pathQueryOptions,
  publicPathQueryOptions,
  isNotFound,
  type MaterialMode,
  materialFailures,
  retryPath,
} from "../lib/api";
import { useFeatureFlag } from "../lib/feature-flags";
import { MaterialField } from "../components/material-field";
import { type MaterialDraft, toInput } from "../components/material-items";
import { fillEmptyFields, parseFrom } from "../lib/public-profiles";
import { AboutYouApplies } from "../components/about-you-applies";
import { ModelPicker } from "../components/model-picker";
import { announcePathCreated } from "../components/push-lifecycle";
import { Breadcrumbs } from "../components/breadcrumbs";
import { CopyCredit } from "../components/public-profile";
import { PathDraftFlow } from "../components/path-draft/path-draft-flow";
import { usePathDraft } from "../components/path-draft/use-path-draft";
import { LevelFieldset } from "../components/level-fieldset";
import { TeachingStyleField } from "../components/teaching-style-field";
import { PRIMARY_CTA, RetryNotices, Spinner, StateCard } from "../components/state-card";
import { sessionQueryOptions } from "../lib/auth";
import {
  GUIDANCE_MAX_LENGTH,
  MODEL_SLOT_DEFAULT,
  TOPIC_MAX_LENGTH,
  buildCreatePathInput,
  canSubmitTopic,
  deriveOnboardingPhase,
  isLevel,
} from "../lib/onboarding";
import { makePollingRefetchInterval } from "../lib/polling";
import { useRetryGeneration } from "../lib/use-retry-generation";

export const Route = createFileRoute("/new")({
  // `?topic=` seeds the field and nothing else (same shape as `/review?path=`
  // and `/cards?path=`). It is a *starting value*, not a lock: the learner is
  // looking at an editable input, and the Topic is only frozen once the path
  // exists. The one caller today is the path-complete card's "Go deeper" door
  // (AL-420), which hands over the finished path's Topic so the learner is not
  // retyping a subject Aleph already knows.
  // The Learning summary's suggestions also prefill `level`, `guidance` and
  // `teaching_style`, so a suggested path opens with its form filled in.
  validateSearch: (search: Record<string, unknown>): NewPathSearch => ({
    topic: typeof search.topic === "string" ? search.topic : undefined,
    level: isLevel(search.level) ? search.level : undefined,
    guidance: typeof search.guidance === "string" ? search.guidance : undefined,
    teaching_style: typeof search.teaching_style === "string" ? search.teaching_style : undefined,
    // `?from=<handle>/<pathId>`: a Copy of a Shared path (Public profiles TDD
    // D2). A reference, never the inputs themselves: the form fetches them.
    from: parseFrom(search.from) ? (search.from as string) : undefined,
  }),
  component: NewPath,
});

interface NewPathSearch {
  topic?: string;
  level?: Level;
  guidance?: string;
  teaching_style?: string;
  from?: string;
}

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
  const search = Route.useSearch();

  // Seeded once, as the initial value — not synced to the search param, so a
  // learner who edits the field keeps their edit.
  const [topic, setTopic] = useState(search.topic ?? "");
  const [level, setLevel] = useState<Level>(search.level ?? "new_to_it");
  // Optional creation input alongside topic (§5.1, docs/api.md): free text the
  // learner pastes to shape the outline. Preserved across the failed→editing
  // round trip exactly like topic/level, below.
  const [guidance, setGuidance] = useState(search.guidance ?? "");
  const [teachingStyle, setTeachingStyle] = useState(search.teaching_style ?? "");
  const [searchOutline, setSearchOutline] = useState(false);
  const [searchLessons, setSearchLessons] = useState(false);
  const [pathId, setPathId] = useState<string | null>(null);
  // Admin model slots (§5.3/D14). `MODEL_SLOT_DEFAULT` means "no override" —
  // `buildCreatePathInput` drops the key rather than sending an empty id.
  const [modelOutline, setModelOutline] = useState(MODEL_SLOT_DEFAULT);
  const [modelLesson, setModelLesson] = useState(MODEL_SLOT_DEFAULT);
  // Linked Material (CONTEXT.md), behind the dark `material` flag. Read by the
  // server at create; nothing is fetched while the learner edits.
  const materialEnabled = useFeatureFlag("material");
  const [material, setMaterial] = useState<MaterialDraft[]>([]);
  const [materialMode, setMaterialMode] = useState<MaterialMode>("teach");

  // A Copy (CONTEXT.md): fetch the Shared path's creation inputs once, fill
  // only the fields still empty, and send its id for credit. Never submits on
  // its own (Constitution §2).
  const copyFrom = parseFrom(search.from);
  const sourceQuery = useQuery({
    ...publicPathQueryOptions(copyFrom?.handle ?? "", copyFrom?.id ?? ""),
    enabled: copyFrom !== null,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const levelTouched = useRef(search.level !== undefined);
  const appliedSource = useRef(false);
  const source = sourceQuery.data;
  useEffect(() => {
    if (!source || appliedSource.current) return;
    appliedSource.current = true;
    const filled = fillEmptyFields(
      { topic, guidance, teaching_style: teachingStyle },
      { topic: source.topic, guidance: source.guidance, teaching_style: source.teaching_style },
    );
    if (filled.topic !== undefined) setTopic(filled.topic);
    if (filled.guidance !== undefined) setGuidance(filled.guidance);
    if (filled.teaching_style !== undefined) setTeachingStyle(filled.teaching_style);
    if (!levelTouched.current) setLevel(source.level);
  }, [source, topic, guidance, teachingStyle]);
  const sourceMissing = copyFrom !== null && isNotFound(sourceQuery.error);
  function changeLevel(value: Level) {
    levelTouched.current = true;
    setLevel(value);
  }

  // Drafting from an Idea (CONTEXT.md: *Path draft*) is where a bare `/new`
  // starts. Any prefill (a Learning summary Suggestion, Go deeper) already says
  // what the path is, so it opens straight on the form, exactly as before.
  const prefilled = Object.values(search).some((value) => value !== undefined);
  const [mode, setMode] = useState<"idea" | "form">(prefilled ? "form" : "idea");
  const [filledFromDraft, setFilledFromDraft] = useState(false);
  const draftFlow = usePathDraft();

  // "Use this draft" fills the ordinary form and nothing else: the learner
  // still edits and submits it (Constitution §2).
  function applyDraft(draft: PathDraft) {
    setTopic(draft.topic);
    setLevel(draft.level);
    setGuidance(draft.guidance);
    setTeachingStyle(draft.teaching_style);
    setFilledFromDraft(true);
    setMode("form");
    window.scrollTo(0, 0);
  }

  function showDraft() {
    setMode("idea");
    window.scrollTo(0, 0);
  }

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
      if (user) announcePathCreated(user.id);
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
      buildCreatePathInput({
        topic,
        level,
        guidance,
        teachingStyle,
        searchOutline,
        searchLessons,
        modelOutline,
        modelLesson,
        sourcePathId: source?.id,
        material: materialEnabled ? material.map(toInput) : undefined,
        materialMode,
      }),
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
  // A Material read failure is a 422 too, but it names its items: claim it for
  // the Material field before the picker can, and show it on those items.
  const failedMaterial = createMutation.isError ? materialFailures(createMutation.error) : null;
  const modelRejected =
    createMutation.isError &&
    failedMaterial === null &&
    sentModelOverride &&
    isValidationError(createMutation.error);
  // Everything else — including a 422 the picker did not cause — falls through
  // to the generic surface, so no failed create is ever silent.
  const createFailed =
    createMutation.isError && !rateLimited && !modelRejected && failedMaterial === null;

  return (
    <main className="page">
      <Breadcrumbs current="New path" />

      <p className="kicker">New path</p>
      <h1 className="mt-2 page-title">What do you want to learn?</h1>
      <p className="mt-3 text-base leading-6 text-mist">
        Name a topic or a goal and Aleph drafts a path you can work through, lesson by lesson.
      </p>

      {phase === "editing" && mode === "idea" ? (
        <PathDraftFlow
          flow={draftFlow}
          onUse={applyDraft}
          onFillMyself={() => {
            setFilledFromDraft(false);
            setMode("form");
          }}
        />
      ) : null}

      {phase === "editing" && mode === "form" && filledFromDraft ? (
        <div
          data-testid="draft-filled-notice"
          className="mt-8 flex items-start justify-between gap-3 rounded-md border border-divider bg-elevated px-4 py-3 text-sm leading-6 text-mist"
        >
          <span>Filled from Aleph's draft. Edit anything before you build.</span>
          <button
            type="button"
            className="shrink-0 font-medium text-iris-300 hover:text-iris-400"
            onClick={showDraft}
          >
            See draft
          </button>
        </div>
      ) : null}

      {phase === "editing" && mode === "form" && source ? (
        <p
          data-testid="copy-from-notice"
          className="mt-8 rounded-md border border-divider bg-elevated px-4 py-3 text-sm leading-6 text-mist"
        >
          From {source.owner.display_name}'s path. Edit anything before you build: Aleph drafts a
          new outline for you.
        </p>
      ) : null}

      {phase === "editing" && mode === "form" && sourceMissing ? (
        <p
          data-testid="copy-from-missing"
          className="mt-8 rounded-md border border-divider bg-elevated px-4 py-3 text-sm leading-6 text-mist"
        >
          That shared path isn't available anymore, so the form starts empty.
        </p>
      ) : null}

      {phase === "editing" && mode === "form" ? (
        <OnboardingForm
          compact={filledFromDraft || source !== undefined || sourceMissing}
          topic={topic}
          level={level}
          guidance={guidance}
          teachingStyle={teachingStyle}
          searchOutline={searchOutline}
          searchLessons={searchLessons}
          onTopicChange={setTopic}
          onLevelChange={changeLevel}
          onGuidanceChange={setGuidance}
          onTeachingStyleChange={setTeachingStyle}
          onSearchOutlineChange={setSearchOutline}
          onSearchLessonsChange={setSearchLessons}
          onSubmit={onSubmit}
          submitting={createMutation.isPending}
          submittingLabel={
            materialEnabled && material.length > 0 ? "Reading your Material…" : undefined
          }
          materialField={
            materialEnabled ? (
              <MaterialField
                items={material}
                onItemsChange={(items) => {
                  setMaterial(items);
                  // The reasons are by index; any edit makes them stale.
                  if (failedMaterial) createMutation.reset();
                }}
                mode={materialMode}
                onModeChange={setMaterialMode}
                failures={Object.fromEntries(
                  (failedMaterial ?? []).map((failure) => [failure.index, failure.reason]),
                )}
              />
            ) : null
          }
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

      {phase === "generating" && pathQuery.data?.source ? (
        <CopyCredit
          kind="path"
          source={{ ...pathQuery.data.source, id: pathQuery.data.source.path_id }}
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
  teachingStyle: string;
  searchOutline: boolean;
  searchLessons: boolean;
  onTopicChange: (value: string) => void;
  onLevelChange: (value: Level) => void;
  onGuidanceChange: (value: string) => void;
  onTeachingStyleChange: (value: string) => void;
  onSearchOutlineChange: (value: boolean) => void;
  onSearchLessonsChange: (value: boolean) => void;
  onSubmit: (event: React.FormEvent) => void;
  submitting: boolean;
  /** Replaces "Starting…" while the server reads Material. */
  submittingLabel?: string;
  /** The Material field, when the `material` flag is on; placed under the topic. */
  materialField?: React.ReactNode;
  rateLimited: boolean;
  errored: boolean;
  /**
   * The admin model picker (§5.3/D14), already assembled by the caller — it
   * renders nothing for everyone else, so the form places it unconditionally.
   */
  modelPicker: React.ReactNode;
  /** Sits under the "Filled from Aleph's draft" notice: less space above. */
  compact?: boolean;
}

function OnboardingForm({
  topic,
  level,
  guidance,
  teachingStyle,
  searchOutline,
  searchLessons,
  onTopicChange,
  onLevelChange,
  onGuidanceChange,
  onTeachingStyleChange,
  onSearchOutlineChange,
  onSearchLessonsChange,
  onSubmit,
  submitting,
  submittingLabel,
  materialField,
  rateLimited,
  errored,
  modelPicker,
  compact = false,
}: FormProps) {
  return (
    <form className={compact ? "mt-5" : "mt-8"} onSubmit={onSubmit} noValidate>
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

      <AboutYouApplies />

      {materialField}

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

      <div className="mt-6">
        <label htmlFor="onboarding-teaching-style" className="text-sm font-medium text-porcelain">
          Teaching style
        </label>
        <p className="mt-1 text-sm leading-6 text-mist">
          Optional. How each lesson should read. You can change this later.
        </p>
        <TeachingStyleField
          id="onboarding-teaching-style"
          value={teachingStyle}
          onChange={onTeachingStyleChange}
        />
      </div>

      <LevelFieldset level={level} onChange={onLevelChange} idPrefix="level" />

      <fieldset className="mt-6" aria-describedby="onboarding-search-help">
        <legend className="text-sm font-medium text-porcelain">Web search</legend>
        <p id="onboarding-search-help" className="mt-1 text-sm leading-6 text-mist">
          Optional. Search adds time to generation. These settings are fixed at creation.
        </p>
        <label className="mt-3 flex min-h-11 cursor-pointer items-center justify-between gap-4 text-sm">
          <span>Search the web for the outline</span>
          <input
            type="checkbox"
            name="search_outline"
            className="h-5 w-5 accent-teal"
            checked={searchOutline}
            onChange={(event) => onSearchOutlineChange(event.target.checked)}
          />
        </label>
        <label className="flex min-h-11 cursor-pointer items-center justify-between gap-4 text-sm">
          <span>Search the web for lessons</span>
          <input
            type="checkbox"
            name="search_lessons"
            className="h-5 w-5 accent-teal"
            checked={searchLessons}
            onChange={(event) => onSearchLessonsChange(event.target.checked)}
          />
        </label>
      </fieldset>

      {modelPicker}

      <button
        type="submit"
        disabled={!canSubmitTopic(topic) || submitting}
        className="mt-8 inline-flex w-full items-center justify-center rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright disabled:cursor-not-allowed disabled:opacity-50"
      >
        {submitting ? (submittingLabel ?? "Starting…") : "Build my path"}
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
