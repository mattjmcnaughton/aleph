import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useFeatureFlag } from "../../lib/feature-flags";
import { learningError, recordLearningInteraction } from "../../lib/learning-tutor";
import type { LearningRecommendation } from "../../lib/learning-tutor-types";
import type { LearningTutor } from "./use-learning-tutor";

export const LT_BUTTON =
  "inline-flex min-h-11 items-center justify-center rounded-md border border-divider px-3 py-2 text-sm text-mist hover:border-teal hover:text-porcelain focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal disabled:opacity-50";
export const LT_FIELD =
  "mt-2 w-full rounded-md border border-divider bg-surface px-3 py-3 text-base text-porcelain focus:border-teal focus:outline-none";

export function LearningNotices({ tutor }: { tutor: LearningTutor }) {
  const error = tutor.send.error ?? tutor.change.error ?? tutor.operation.error;
  const failed = tutor.operation.data?.status === "failed" && !tutor.operation.data.cleared;
  const refresh = tutor.state.data?.latest_refresh;
  const lastTurn = tutor.state.data?.history.items[0];
  const legacyFailure =
    refresh?.status === "failed" &&
    !refresh.cleared &&
    (!lastTurn ||
      Date.parse(lastTurn.started_at) <= Date.parse(refresh.finished_at ?? refresh.started_at));
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
      {!failed && !tutor.busy && legacyFailure && (
        <p role="alert" className="rounded-md border border-danger-border bg-danger-bg p-3 text-sm">
          An earlier recommendation request didn't finish. Continue the conversation when you're
          ready.
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
  ids,
}: { tutor: LearningTutor; surface: "home" | "tutor"; ids?: string[] }) {
  const analyst = useFeatureFlag("analyst");
  const [undo, setUndo] = useState<LearningRecommendation | null>(null);
  const undoRef = useRef<HTMLButtonElement>(null);
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
  const cards = state.recommendations.filter(
    (card) => (analyst || card.kind !== "beat") && (!ids || ids.includes(card.id)),
  );
  async function decide(card: LearningRecommendation, decision: "dismiss" | "restore") {
    try {
      await tutor.change.mutateAsync({ kind: "decision", id: card.id, decision });
      setUndo(decision === "dismiss" ? card : null);
    } catch {
      /* The shared notice retains the actionable error. */
    }
  }
  if (surface === "tutor" && cards.length === 0 && !undo) return null;
  return (
    <section data-testid="learning-recommendations" className="mt-4">
      {surface === "home" && (
        <div className="mt-8 border-t border-divider pt-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="kicker">From your Tutor</h2>
            <Link to="/tutor" search={{}} className={LT_BUTTON}>
              Chat with your Tutor
            </Link>
          </div>
          {cards.length === 0 && (
            <p className="mt-3 text-sm text-mist">
              Ask a question, explore an interest, or discover something new.
            </p>
          )}
          <LearningNotices tutor={tutor} />
        </div>
      )}
      <div className={`mt-4 grid gap-4 ${surface === "home" ? "lg:grid-cols-3" : ""}`}>
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
