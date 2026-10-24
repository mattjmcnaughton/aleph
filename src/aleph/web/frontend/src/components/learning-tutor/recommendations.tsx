import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useFeatureFlag } from "../../lib/feature-flags";
import {
  learningError,
  recommendationState,
  recordLearningInteraction,
} from "../../lib/learning-tutor";
import type { LearningRecommendation } from "../../lib/learning-tutor-types";
import type { LearningTutor } from "./use-learning-tutor";

export const LT_BUTTON =
  "inline-flex min-h-11 items-center justify-center rounded-md border border-divider px-3 py-2 text-sm text-mist hover:border-teal hover:text-porcelain focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal disabled:opacity-50";
export const LT_FIELD =
  "mt-2 w-full rounded-md border border-divider bg-surface px-3 py-3 text-base text-porcelain focus:border-teal focus:outline-none";

const EMPTY_COPY = {
  initial: "Find directions to explore, when you're ready. Nothing is generated just by visiting.",
  needs_context:
    "Tell your Tutor an interest, save a Learning focus, or start a path. There's not enough context yet; no model call was used.",
  empty:
    "Nothing useful to add right now. Continue your current work or clarify your Learning focus.",
  dismissed: "You've dismissed your recommendations. Refresh explicitly for new directions.",
  unavailable:
    "The previous recommendations are no longer available. Your focus or sources may have changed, or you may have accepted them. Refresh when you're ready.",
  failed:
    "Recommendations couldn't be refreshed. This is a failure, not an empty result. Try again when you're ready.",
  running: "Finding directions to explore…",
  cards: "",
};

export function LearningNotices({ tutor }: { tutor: LearningTutor }) {
  const error = tutor.send.error ?? tutor.change.error ?? tutor.operation.error;
  const failed = tutor.operation.data?.status === "failed" && !tutor.operation.data.cleared;
  return (
    <div className="space-y-3" aria-live="polite">
      {tutor.notice && <p className="text-sm leading-6 text-mist">{tutor.notice}</p>}
      {tutor.busy && (
        <output className="flex items-center gap-2.5 text-sm text-iris-300">
          <span aria-hidden="true" className="flex shrink-0 items-center gap-1">
            {["[animation-delay:0ms]", "[animation-delay:160ms]", "[animation-delay:320ms]"].map(
              (delay) => (
                <span
                  key={delay}
                  className={`h-1.5 w-1.5 animate-thinking rounded-full bg-iris motion-reduce:animate-none ${delay}`}
                />
              ),
            )}
          </span>
          <span>Your Tutor is thinking. You can return here to recover the result.</span>
        </output>
      )}
      {Boolean(error) && (
        <p role="alert" className="rounded-md border border-danger-border bg-danger-bg p-3 text-sm">
          {learningError(error)}
        </p>
      )}
      {failed && (
        <p role="alert" className="rounded-md border border-danger-border bg-danger-bg p-3 text-sm">
          {tutor.operation.data?.error_code === "context_changed"
            ? "Your context changed while the Tutor was working. No partial reply was published."
            : `Your Tutor couldn't finish (${tutor.operation.data?.error_code ?? "interrupted"}). No partial reply was published.`}{" "}
          Retry explicitly; your input is still here.
        </p>
      )}
      {tutor.uncertain && !tutor.send.isPending && (
        <button type="button" className={LT_BUTTON} onClick={() => void tutor.start()}>
          Recover submitted request
        </button>
      )}
    </div>
  );
}

export function Recommendations({
  tutor,
  surface,
}: { tutor: LearningTutor; surface: "home" | "tutor" }) {
  const analyst = useFeatureFlag("analyst");
  const [undo, setUndo] = useState<LearningRecommendation | null>(null);
  const undoRef = useRef<HTMLButtonElement>(null);
  const refreshRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (undo) undoRef.current?.focus();
  }, [undo]);
  const state = tutor.state.data;
  if (!state)
    return (
      <section className="mt-8" aria-live="polite">
        <h2 className="kicker">From your Tutor</h2>
        {tutor.state.isError ? (
          <>
            <p className="mt-3">We couldn't load your Tutor.</p>
            <button type="button" className={LT_BUTTON} onClick={() => void tutor.state.refetch()}>
              Try loading again
            </button>
          </>
        ) : (
          <p className="mt-3">Loading your Tutor…</p>
        )}
      </section>
    );
  const dismissed =
    tutor.dismissed.data?.pages
      .flatMap((page) => page.items)
      .filter((card) => analyst || card.kind !== "beat") ?? [];
  const cards = state.recommendations.filter((card) => analyst || card.kind !== "beat");
  const kind = recommendationState({ ...state, recommendations: cards }, dismissed.length);
  const refresh = state.latest_refresh;
  const refreshFinishedAt = refresh?.finished_at;
  // A later turn can publish cards without replacing the last refresh receipt.
  const newerCards =
    refreshFinishedAt &&
    cards.some((card) => Date.parse(card.created_at) > Date.parse(refreshFinishedAt));
  async function decide(card: LearningRecommendation, decision: "dismiss" | "restore") {
    try {
      await tutor.change.mutateAsync({ kind: "decision", id: card.id, decision });
      setUndo(decision === "dismiss" ? card : null);
      if (decision === "restore") refreshRef.current?.focus();
    } catch {
      /* The shared notice retains the actionable error. */
    }
  }
  return (
    <section data-testid="learning-recommendations" className="mt-8 border-t border-divider pt-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="kicker">From your Tutor</h2>
        <button
          ref={refreshRef}
          type="button"
          className={LT_BUTTON}
          disabled={tutor.busy || Boolean(tutor.uncertain)}
          onClick={() => void tutor.start()}
        >
          {state.latest_refresh ? "Refresh recommendations" : "Find directions to explore"}
        </button>
      </div>
      <p className="mt-3 text-sm leading-6 text-mist">
        Uses your explicit Learning focus and sampled activity metadata from paths
        {analyst ? " and Beats" : ""}, not lesson conversations or Read passages
        {analyst ? " or Brief bodies" : ""}.
      </p>
      {state.latest_refresh?.finished_at && (
        <p className="mt-2 text-xs text-slate">
          Last refresh: {new Date(state.latest_refresh.finished_at).toLocaleString()}. Not
          automatically updated with later activity.
        </p>
      )}
      {surface === "home" && (
        <div className="mt-3">
          <LearningNotices tutor={tutor} />
        </div>
      )}
      {cards.length === 0 && (
        <p data-testid={`learning-${kind}`} className="mt-4 text-sm leading-6 text-mist">
          {EMPTY_COPY[kind]}
        </p>
      )}
      {refresh?.status === "failed" && !newerCards && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {cards.length > 0
            ? "Refresh failed. Your previous recommendations are still here."
            : "Refresh failed. Try again explicitly when you're ready."}
        </p>
      )}
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        {cards.map((card) => (
          <RecommendationCard
            key={card.id}
            card={card}
            tutor={tutor}
            surface={surface}
            dismiss={() => void decide(card, "dismiss")}
          />
        ))}
      </div>
      {undo && (
        <div className="mt-3 flex items-center gap-3" aria-live="polite">
          <p className="text-sm">Recommendation dismissed.</p>
          <button
            ref={undoRef}
            type="button"
            className={LT_BUTTON}
            disabled={tutor.change.isPending}
            onClick={() => void decide(undo, "restore")}
          >
            Undo
          </button>
        </div>
      )}
    </section>
  );
}

function RecommendationCard({
  card,
  tutor,
  surface,
  dismiss,
}: {
  card: LearningRecommendation;
  tutor: LearningTutor;
  surface: "home" | "tutor";
  dismiss: () => void;
}) {
  useEffect(() => {
    recordLearningInteraction(tutor.account, card.id, "shown", surface);
  }, [tutor.account, card.id, surface]);
  return (
    <article
      data-testid="learning-recommendation"
      className="min-w-0 rounded-lg border border-divider bg-surface p-5"
    >
      <p className="kicker">Suggested {card.kind === "beat" ? "Beat" : "path"}</p>
      <h3 className="mt-2 text-xl font-semibold">{card.topic}</h3>
      <p className="mt-3 text-sm leading-6 text-mist">{card.description}</p>
      <h4 className="mt-4 text-sm font-semibold">Why this?</h4>
      <p className="mt-1 text-sm leading-6 text-mist">{card.rationale}</p>
      {card.evidence?.map((fact, index) => (
        <p key={`${fact.kind}-${fact.id}-${index}`} className="mt-2 text-xs text-slate">
          {fact.kind === "focus" ? (
            "Based on your saved Learning focus."
          ) : fact.kind === "message" ? (
            "Based on your expressed interest."
          ) : (
            <>
              {fact.completed_lessons !== null && `${fact.completed_lessons} lessons complete. `}
              {fact.read_briefs !== null && `${fact.read_briefs} Briefs opened. `}
              {fact.published_briefs !== null && `${fact.published_briefs} Briefs published. `}
              Observed{" "}
              {fact.observed_at
                ? new Date(fact.observed_at).toLocaleDateString()
                : "when generated"}
              .
            </>
          )}
        </p>
      ))}
      <p className="mt-3 text-xs text-slate">
        Suggested {new Date(card.created_at).toLocaleDateString()}
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Link
          className={`${LT_BUTTON} text-teal`}
          to={card.kind === "beat" ? "/beats/new" : "/new"}
          search={{ recommendation: card.id }}
          onClick={() => recordLearningInteraction(tutor.account, card.id, "explore", surface)}
        >
          Explore {card.kind === "beat" ? "Beat" : "path"}
        </Link>
        <Link
          className={LT_BUTTON}
          to="/tutor"
          search={{ recommendation: card.id }}
          onClick={() => recordLearningInteraction(tutor.account, card.id, "discuss", surface)}
        >
          Discuss
        </Link>
        <button
          type="button"
          className={LT_BUTTON}
          disabled={tutor.change.isPending}
          onClick={dismiss}
        >
          Not interested
        </button>
      </div>
    </article>
  );
}
