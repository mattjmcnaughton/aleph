import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useEffect, useState } from "react";
import { ApiError, type Level } from "../../lib/api";
import { sessionQueryOptions } from "../../lib/auth";
import {
  learningDraftOptions,
  learningError,
  learningStateOptions,
} from "../../lib/learning-tutor";
import { LT_BUTTON } from "./recommendations";

export interface RecommendationSeed {
  id: string;
  topic: string;
  level: Level;
  guidance: string;
  revision: number;
  account: string;
  beginCreation: () => void;
  rejectCreation: () => void;
}

/** Ordinary onboarding never queries the Tutor. A supplied ID never falls back. */
export function CreationSeed({
  id,
  kind,
  children,
}: { id?: string; kind: "path" | "beat"; children: (seed?: RecommendationSeed) => ReactNode }) {
  const user = useQuery(sessionQueryOptions).data?.user;
  if (id === undefined) return <>{children()}</>;
  if (!user?.feature_flags?.learning_tutor || (kind === "beat" && !user.feature_flags.analyst))
    return <SeedError message="This recommendation isn't available." />;
  return (
    <OwnedSeed key={`${user.id}:${id}`} account={user.id} id={id} kind={kind}>
      {children}
    </OwnedSeed>
  );
}

function OwnedSeed({
  account,
  id,
  kind,
  children,
}: {
  account: string;
  id: string;
  kind: "path" | "beat";
  children: (seed?: RecommendationSeed) => ReactNode;
}) {
  const draft = useQuery(learningDraftOptions(account, id));
  const state = useQuery(learningStateOptions(account));
  const [admitted, setAdmitted] = useState<RecommendationSeed | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const card = draft.data;
  const valid =
    card?.status === "active" &&
    card.kind === kind &&
    Boolean(card.topic) &&
    ["new_to_it", "some_experience", "work_in_it"].includes(card.level ?? "");
  useEffect(() => {
    if (
      admitted ||
      !valid ||
      !card ||
      !state.data ||
      draft.isError ||
      state.isError ||
      !draft.isFetchedAfterMount ||
      !state.isFetchedAfterMount
    )
      return;
    setAdmitted({
      id,
      account,
      revision: state.data.revision,
      topic: card.topic as string,
      level: card.level as Level,
      guidance: card.guidance ?? "",
      beginCreation: () => setSubmitted(true),
      rejectCreation: () => setSubmitted(false),
    });
  }, [
    admitted,
    valid,
    card,
    state.data,
    draft.isError,
    state.isError,
    draft.isFetchedAfterMount,
    state.isFetchedAfterMount,
    id,
    account,
  ]);
  const error = draft.error ?? state.error;
  // A background transport failure must not destroy the form/mutation owning
  // edits or a response-loss receipt. Authoritative rejection still closes it.
  if (error && (!admitted || !uncertainCreation(error)))
    return <SeedError message={learningError(error)} />;
  // Acceptance is the expected result of this form's POST, not a new admission.
  // Keep its uncertain receipt or path-generation poll alive until it navigates.
  if (admitted && submitted && card?.status === "accepted" && card.target_available)
    return <>{children(admitted)}</>;
  if (!draft.data || !state.data || !draft.isFetchedAfterMount || !state.isFetchedAfterMount)
    return (
      <main className="mx-auto max-w-[480px] px-4 py-8" aria-live="polite">
        Loading recommendation…
      </main>
    );
  if (!card) return null;
  if (card.status === "accepted")
    return (
      <main className="mx-auto max-w-[480px] px-4 py-8">
        <h1 className="text-xl">Already accepted</h1>
        {card.target_available && card.target_id ? (
          <>
            {card.kind === "path" ? (
              <Link className={LT_BUTTON} to="/paths/$pathId" params={{ pathId: card.target_id }}>
                Open created path
              </Link>
            ) : (
              <Link className={LT_BUTTON} to="/beats/$beatId" params={{ beatId: card.target_id }}>
                Open created Beat
              </Link>
            )}
          </>
        ) : (
          <p>The accepted target was deleted. This recommendation cannot create it again.</p>
        )}
        <Link to="/tutor" className={`mt-4 ${LT_BUTTON}`}>
          Back to Tutor
        </Link>
      </main>
    );
  if (
    card.status !== "active" ||
    card.kind !== kind ||
    !card.topic ||
    !["new_to_it", "some_experience", "work_in_it"].includes(card.level ?? "")
  )
    return <SeedError message="This recommendation is no longer available for creation." />;
  return admitted ? <>{children({ ...admitted, revision: state.data.revision })}</> : null;
}

function SeedError({ message }: { message: string }) {
  return (
    <main className="mx-auto max-w-[480px] px-4 py-8">
      <h1 className="text-xl">Recommendation unavailable</h1>
      <p role="alert" className="mt-3 text-sm leading-6 text-mist">
        {message}
      </p>
      <Link to="/tutor" className={`mt-4 ${LT_BUTTON}`}>
        Back to Tutor
      </Link>
    </main>
  );
}

export function uncertainCreation(error: unknown) {
  return error != null && (!(error instanceof ApiError) || error.status >= 500);
}

export function CreationNotice({
  error,
  recovering,
  recover,
}: { error: unknown; recovering: boolean; recover: () => void }) {
  if (!error) return null;
  return (
    <div className="mt-4 rounded-md border border-danger-border bg-danger-bg p-3" role="alert">
      <p className="text-sm">{learningError(error)}</p>
      {uncertainCreation(error) && (
        <>
          <p className="mt-2 text-sm">
            Creation may already have succeeded. Recover the original submitted details before
            making another submission.
          </p>
          <button
            className={`mt-3 ${LT_BUTTON}`}
            type="button"
            disabled={recovering}
            onClick={recover}
          >
            Recover creation
          </button>
        </>
      )}
    </div>
  );
}
