import { type QueryFilters, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  BEATS_LIST_QUERY_KEY,
  type BeatList,
  type BriefDetail,
  beatQueryKey,
  beatQueryOptions,
  deleteBeat,
  isBeatDetailTerminal,
  isNotFound,
  retryBeat,
} from "../lib/api";
import { BeatRail } from "../components/beat-rail";
import { Breadcrumbs } from "../components/breadcrumbs";
import { PRIMARY_CTA, RetryNotices, Spinner, StateCard } from "../components/state-card";
import { StandingOrders } from "../components/standing-orders";
import { useFeatureFlag } from "../lib/feature-flags";
import { makePollingRefetchInterval } from "../lib/polling";
import { useRetryGeneration } from "../lib/use-retry-generation";

export const Route = createFileRoute("/beats/$beatId")({
  component: BeatView,
});

const beatPollConfig = {
  isTerminal: isBeatDetailTerminal,
  // A deep link to a missing Beat (404) is terminal — stop polling rather
  // than spawning an endless chain of backend drains against a Beat that
  // can't resolve (the `isNotFound` precedent every other detail poll uses).
  isErrorTerminal: isNotFound,
};

// The Beat view (PRD §3, TDD §8): the path view's near-identical sibling.
// The Beat rail occupies the path rail's position and shape — flat, newest
// first, each row dated, nothing ever locked (PRD §3: "Flat, not grouped").
// It renders straight off one `GET /beats/{id}` payload, polled only while
// `research_state === "researching"` (TDD §7) — every terminal state,
// `refused` included, stops the loop the moment it is read (D3's `idle`
// steady state above all — see `isBeatResearchStateTerminal`'s own doc).
//
// A learner can land here straight off `routes/beats.new.tsx`'s navigate, or
// deep-link back later; both read the identical poll target, so there is no
// separate "just deployed" phase to track client-side. Rail entries render
// regardless of `research_state` — a run in flight, or one that failed or
// was refused, never hides the Beat's existing history.
function BeatView() {
  const { beatId } = Route.useParams();
  const analystEnabled = useFeatureFlag("analyst");

  const beatQuery = useQuery({
    ...beatQueryOptions(beatId, analystEnabled),
    refetchInterval: makePollingRefetchInterval(beatPollConfig),
  });

  const retry = useRetryGeneration({ mutationFn: retryBeat, queryKey: beatQueryKey });

  // A direct/deep link with the flag off (D10: the whole surface is a
  // router-level gate server-side, `404` on every route) — the frontend's
  // own dead end (code-review FIX 3). Without this, `beatQueryOptions`'s
  // `skipToken` means `beatQuery.data` never resolves and `beatQuery.isError`
  // never flips true, so `LoadingState` below would render "Loading your
  // Beat…" forever instead of a real dead end.
  if (!analystEnabled) {
    return (
      <main className="mx-auto w-full max-w-[480px] px-4 py-8">
        <StateCard testid="beat-unavailable">
          <h2 className="text-lg font-semibold">This Beat isn't available.</h2>
          <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
            Head back home — there's nothing to read from here.
          </p>
          <Link to="/" className="mt-5 inline-block text-sm text-teal">
            Back to your beats
          </Link>
        </StateCard>
      </main>
    );
  }

  const detail = beatQuery.data;

  return (
    <main className="mx-auto w-full max-w-[480px] px-4 py-8">
      {detail ? <Breadcrumbs current={detail.topic} root="Your beats" /> : null}

      {detail === undefined ? (
        beatQuery.isError ? (
          <UnavailableState />
        ) : (
          <LoadingState />
        )
      ) : (
        <>
          <p className="kicker">Beat</p>
          {/* min-w-0 + truncate (code-review FIX 6): the project's
              convention for user text (`paths.$pathId.tsx`, `sidebar.tsx`)
              — a pasted URL as a Topic must not overflow 390px. */}
          <h1 className="mt-2 min-w-0 truncate text-3xl font-semibold leading-tight tracking-tight">
            {detail.topic}
          </h1>
          <div className="mt-3">
            <StandingOrders beat={detail} />
          </div>

          {detail.research_state === "researching" ? (
            <ResearchingState />
          ) : detail.research_state === "refused" ? (
            <RefusedState message={detail.refusal_message ?? undefined} />
          ) : detail.research_state === "failed" ? (
            <FailedState
              onRetry={() => retry.retry(detail.id)}
              retrying={retry.retrying}
              retryRateLimited={retry.rateLimited}
              retryErrored={retry.errored}
            />
          ) : null}

          <BeatRail entries={detail.entries} />
          <DeleteBeat beatId={detail.id} topic={detail.topic} />
        </>
      )}
    </main>
  );
}

function DeleteBeat({ beatId, topic }: { beatId: string; topic: string }) {
  const [confirming, setConfirming] = useState(false);
  const deleteRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: async () => {
      try {
        await deleteBeat(beatId);
      } catch (error) {
        // A lost acknowledgement or another tab may have already deleted it.
        if (!isNotFound(error)) throw error;
      }
    },
    onSuccess: async () => {
      // Cancel stale reads before filtering, as in useDeletePath. The list
      // key is also the prefix of Beat detail keys, so filter exact only.
      await queryClient.cancelQueries({ queryKey: BEATS_LIST_QUERY_KEY });
      const deletedBriefs: QueryFilters = {
        queryKey: ["briefs"],
        predicate: (query) => (query.state.data as BriefDetail | undefined)?.beat_id === beatId,
      };
      await queryClient.cancelQueries(deletedBriefs);
      queryClient.removeQueries(deletedBriefs);
      queryClient.setQueryData<BeatList>(BEATS_LIST_QUERY_KEY, (old) =>
        old ? { beats: old.beats.filter((beat) => beat.id !== beatId) } : old,
      );
      await navigate({ to: "/" });
      queryClient.removeQueries({ queryKey: beatQueryKey(beatId) });
      await queryClient.invalidateQueries({ queryKey: BEATS_LIST_QUERY_KEY, exact: true });
    },
  });
  useEffect(() => {
    if (confirming) cancelRef.current?.focus();
  }, [confirming]);

  return (
    <section className="mt-6">
      {confirming ? (
        <div className="rounded-md border border-danger-border/60 bg-danger-bg p-3">
          <p className="text-sm leading-6 text-porcelain">
            Delete this Beat and all its Briefs? This can't be undone.
          </p>
          <div aria-live="assertive">
            {mutation.isError ? (
              <p className="mt-2 text-sm leading-6 text-danger">
                We couldn't delete this Beat. Check your connection and try again.
              </p>
            ) : null}
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              aria-label={`Confirm deleting ${topic}`}
              disabled={mutation.isPending}
              onClick={() => {
                if (!mutation.isPending) mutation.mutate();
              }}
              className="min-h-11 flex-1 rounded-md bg-danger px-3 py-2 text-sm font-semibold text-night transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {mutation.isPending ? "Deleting…" : "Delete"}
            </button>
            <button
              type="button"
              ref={cancelRef}
              disabled={mutation.isPending}
              onClick={() => {
                setConfirming(false);
                mutation.reset();
                requestAnimationFrame(() => deleteRef.current?.focus());
              }}
              className="min-h-11 flex-1 rounded-md border border-divider px-3 py-2 text-sm font-semibold text-mist transition-colors hover:text-porcelain disabled:opacity-50"
            >
              Keep it
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          ref={deleteRef}
          onClick={() => setConfirming(true)}
          className="min-h-11 rounded-md px-3 py-2 text-sm text-danger transition-colors hover:bg-danger-bg"
        >
          Delete Beat
        </button>
      )}
    </section>
  );
}

function ResearchingState() {
  return (
    <StateCard testid="beat-researching" ariaLive="polite" spacing="mt-6">
      <Spinner />
      <h2 className="mt-4 text-lg font-semibold">Researching…</h2>
      <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
        Aleph is gathering and reading sources since the last Brief. This can take a few minutes —
        feel free to keep using the app while it works.
      </p>
    </StateCard>
  );
}

/**
 * Terminal and graceful (PRD §4.2, CONTEXT.md: Beat) — no retry affordance
 * here, ever. A refusal is a scope decision, not a failure to recover from;
 * the only way forward is deleting and redeploying with a different topic
 * (PRD §4.11), using the confirmed deletion below the rail.
 */
function RefusedState({ message }: { message?: string }) {
  return (
    <StateCard
      testid="beat-refused"
      variant="refusal"
      dataVariant="refusal"
      ariaLive="polite"
      spacing="mt-6"
    >
      <h2 className="text-lg font-semibold text-iris-300">This topic is out of scope.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-porcelain">
        {message ?? "Aleph can't research this topic. Delete this Beat and try a different one."}
      </p>
    </StateCard>
  );
}

function FailedState({
  onRetry,
  retrying,
  retryRateLimited,
  retryErrored,
}: {
  onRetry: () => void;
  retrying: boolean;
  retryRateLimited: boolean;
  retryErrored: boolean;
}) {
  return (
    <StateCard
      testid="beat-failed"
      variant="error"
      dataVariant="error"
      ariaLive="assertive"
      spacing="mt-6"
    >
      <h2 className="text-lg font-semibold text-danger">Research didn't finish.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-porcelain">
        We couldn't finish this run. Retry when you're ready.
      </p>
      <RetryNotices
        testidPrefix="beat"
        rateLimited={retryRateLimited}
        errored={retryErrored}
        rateLimitMessage="You've reached today's limit for research runs. Try again tomorrow."
      />
      <button type="button" onClick={onRetry} disabled={retrying} className={`mt-6 ${PRIMARY_CTA}`}>
        {retrying ? "Retrying…" : "Try again"}
      </button>
    </StateCard>
  );
}

function LoadingState() {
  return (
    <>
      <p className="kicker">Beat</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        Loading your Beat…
      </h1>
    </>
  );
}

function UnavailableState() {
  return (
    <StateCard testid="beat-unavailable" spacing="mt-6">
      <h2 className="text-lg font-semibold">We couldn't load this Beat.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        It may have been deleted, or something went wrong. Head back to your Beats and try again.
      </p>
    </StateCard>
  );
}
