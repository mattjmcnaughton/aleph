import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import {
  BEATS_LIST_QUERY_KEY,
  type Level,
  beatQueryKey,
  deployBeat,
  isRateLimited,
} from "../lib/api";
import { Breadcrumbs } from "../components/breadcrumbs";
import { LevelFieldset } from "../components/level-fieldset";
import { PRIMARY_CTA, StateCard } from "../components/state-card";
import { ANCHOR_WEEKDAYS, DEFAULT_ANCHOR_WEEKDAY } from "../lib/beats";
import { useFeatureFlag } from "../lib/feature-flags";
import { GUIDANCE_MAX_LENGTH, TOPIC_MAX_LENGTH, canSubmitTopic } from "../lib/onboarding";

export const Route = createFileRoute("/beats/new")({
  // `?topic=` seeds the field, exactly as on `/new` — the door the
  // path-complete card offers ("Follow it as a Beat", AL-420) carries the
  // finished path's Topic across. Standing orders are frozen at deployment,
  // but not until then: this is an editable initial value.
  validateSearch: (search: Record<string, unknown>): { topic?: string } => ({
    topic: typeof search.topic === "string" ? search.topic : undefined,
  }),
  component: DeployAnalyst,
});

// Deploying an analyst (PRD §3, TDD §8): `routes/new.tsx`'s grammar with one
// field added — Topic, Level (the existing three-way control, verbatim),
// **`Reports on ▾ Monday`**, optional Guidance, primary action `Deploy
// analyst`. A SEPARATE route from `routes/new.tsx`, deliberately: a shared
// component with a mode flag would make the path flow carry a branch it
// never takes.
//
// Unlike onboarding there is no generating/refused/failed holding phase
// here: the Beat exists the instant `POST /beats` returns (`202`, the first
// run already claimed — TDD D15, "researched immediately, not at the first
// Anchor day"), so this route's only job is the form and the create call.
// Whatever state that first claim landed in — researching, or occasionally
// already idle/failed/refused — is rendered on `routes/beats.$beatId.tsx`,
// which is where this route always navigates on success either way.
function DeployAnalyst() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const analystEnabled = useFeatureFlag("analyst");
  const search = Route.useSearch();

  const [topic, setTopic] = useState(search.topic ?? "");
  const [level, setLevel] = useState<Level>("new_to_it");
  const [anchorWeekday, setAnchorWeekday] = useState(DEFAULT_ANCHOR_WEEKDAY);
  const [guidance, setGuidance] = useState("");

  const createMutation = useMutation({
    mutationFn: deployBeat,
    onSuccess: async (created) => {
      // Seed the poll cache with the real `202` body — it already reflects
      // the claim (TDD D15) — so the Beat view's first render never shows a
      // stale `idle` while its own poll's first fetch is still in flight.
      queryClient.setQueryData(beatQueryKey(created.id), created);
      // The home Beats section's cached list no longer matches the server,
      // for the identical reason `routes/new.tsx` invalidates
      // `PATHS_LIST_QUERY_KEY` on a fresh path.
      await queryClient.invalidateQueries({ queryKey: BEATS_LIST_QUERY_KEY });
      navigate({ to: "/beats/$beatId", params: { beatId: created.id }, replace: true });
    },
  });

  const rateLimited = createMutation.isError && isRateLimited(createMutation.error);
  const createFailed = createMutation.isError && !rateLimited;

  // A direct/deep link with the flag off (D10: the whole surface is a
  // router-level gate server-side, `404` on every route) — the frontend's
  // own dead end, matching `routes/cards.tsx`/`routes/review.tsx`'s shape
  // (code-review FIX 3). Without this the form below renders in full, with a
  // live, enabled submit button — tapping "Deploy analyst" would silently do
  // nothing (`onSubmit`'s own `!analystEnabled` early return), which is worse
  // than never rendering the form at all.
  if (!analystEnabled) {
    return (
      <main className="mx-auto w-full max-w-[480px] px-4 py-8">
        <Breadcrumbs current="Deploy analyst" root="Your beats" />
        <StateCard testid="beats-new-unavailable">
          <h2 className="text-lg font-semibold">Deploying an analyst isn't available.</h2>
          <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
            Head back home — there's nothing to deploy from here.
          </p>
          <Link to="/" className="mt-5 inline-block text-sm text-teal">
            Back to your beats
          </Link>
        </StateCard>
      </main>
    );
  }

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!analystEnabled || !canSubmitTopic(topic)) return;
    const trimmedGuidance = guidance.trim();
    createMutation.mutate({
      topic: topic.trim(),
      level,
      anchor_weekday: anchorWeekday,
      ...(trimmedGuidance ? { guidance: trimmedGuidance } : {}),
    });
  }

  return (
    <main className="mx-auto w-full max-w-[480px] px-4 py-8">
      <Breadcrumbs current="Deploy analyst" root="Your beats" />

      <p className="kicker">New beat</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        What should Aleph keep watch on?
      </h1>
      <p className="mt-3 text-base leading-6 text-mist">
        Name a topic and Aleph reports on what changed, on the day you pick.
      </p>

      <form className="mt-8" onSubmit={onSubmit} noValidate>
        {rateLimited ? (
          <p
            data-testid="deploy-beat-ratelimit"
            role="alert"
            className="mb-5 rounded-md border border-divider bg-elevated px-4 py-3 text-sm leading-6 text-mist"
          >
            You've reached the limit for Beats. Delete one to deploy another.
          </p>
        ) : null}
        {createFailed ? (
          <p
            data-testid="deploy-beat-error"
            role="alert"
            className="mb-5 rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-danger"
          >
            Something went wrong deploying your analyst. Try again.
          </p>
        ) : null}

        <label htmlFor="beat-topic" className="sr-only">
          Topic
        </label>
        <input
          id="beat-topic"
          name="topic"
          type="text"
          value={topic}
          onChange={(event) => setTopic(event.target.value)}
          placeholder="e.g. EU AI regulation, GLP-1 drugs…"
          autoComplete="off"
          // Mirrors `TopicStr` (`dtos/paths.py`, reused verbatim by
          // `dtos/beats.py`'s `DeployBeatRequest.topic`).
          maxLength={TOPIC_MAX_LENGTH}
          className="w-full rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none"
        />

        <LevelFieldset level={level} onChange={setLevel} idPrefix="beat-level" />

        <div className="mt-6">
          <label htmlFor="beat-anchor-weekday" className="text-sm font-medium text-porcelain">
            Reports on
          </label>
          <select
            id="beat-anchor-weekday"
            name="anchor_weekday"
            value={anchorWeekday}
            onChange={(event) => setAnchorWeekday(Number(event.target.value))}
            className="mt-3 w-full rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain focus:border-teal focus:outline-none"
          >
            {ANCHOR_WEEKDAYS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="mt-6">
          <label htmlFor="beat-guidance" className="text-sm font-medium text-porcelain">
            Guidance
          </label>
          <p className="mt-1 text-sm leading-6 text-mist">
            Optional. What the analyst should focus on — e.g. "policy and enforcement, not stock
            moves".
          </p>
          <textarea
            id="beat-guidance"
            name="guidance"
            value={guidance}
            onChange={(event) => setGuidance(event.target.value)}
            placeholder="e.g. Policy and enforcement, not stock moves…"
            rows={4}
            // Mirrors `GuidanceStr` (`dtos/paths.py`, reused verbatim by
            // `dtos/beats.py`'s `DeployBeatRequest.guidance`).
            maxLength={GUIDANCE_MAX_LENGTH}
            className="mt-3 w-full resize-y rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none"
          />
        </div>

        <button
          type="submit"
          disabled={!canSubmitTopic(topic) || createMutation.isPending}
          className={`mt-8 ${PRIMARY_CTA}`}
        >
          {createMutation.isPending ? "Deploying…" : "Deploy analyst"}
        </button>
      </form>
    </main>
  );
}
