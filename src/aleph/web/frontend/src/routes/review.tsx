import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import {
  FLASHCARDS_QUERY_PREFIX,
  PROGRESS_QUERY_PREFIX,
  type ProgressSummary,
  type ReviewQueue,
  type ReviewSummary,
  clientTimezoneOffsetMinutes,
  gradeCard,
  pathQueryOptions,
  progressSummaryQueryOptions,
  reviewQueueQueryOptions,
  reviewSummaryQueryOptions,
} from "../lib/api";
import { ReviewCard } from "../components/review/review-card";
import { SessionComplete } from "../components/review/session-complete";
import { StateCard } from "../components/state-card";
import { Workspace } from "../components/workspace";
import { useFeatureFlag } from "../lib/feature-flags";

export const Route = createFileRoute("/review")({
  // A path filters the one global queue for display (PRD §4.3/§4.10) — never a
  // second queue, and there is no in-session switcher (§4.10): scope is chosen
  // at the door, here, by which link the learner tapped.
  validateSearch: (search: Record<string, unknown>): { path?: string } => ({
    path: typeof search.path === "string" ? search.path : undefined,
  }),
  component: ReviewSession,
});

// The review session (PRD §3/§4, Phase 3 TDD §8): one card at a time, front,
// reveal, grade, next — until the day's selected set is exhausted. Renders
// straight off `GET /reviews/queue`, the same derived-not-stored payload the
// pill and the *Due today* card summarize (§5.3): `total`/`completed` are
// always the **global** set's numbers, even in a filtered session, so the
// counter's denominator never shrinks under a learner mid-session.
function ReviewSession() {
  const search = Route.useSearch();
  const pathId = search.path ?? null;
  const queryClient = useQueryClient();

  const flashcardsEnabled = useFeatureFlag("flashcards");
  const queueQuery = useQuery(reviewQueueQueryOptions(flashcardsEnabled, pathId));
  // Only for the scope chip's title (ReviewQueueResponse carries `scope_path_id`
  // as a bare id, never a title — TDD §6); `pathQueryOptions(null)` is already
  // `skipToken` for the "All paths" case, so this never over-fetches.
  const scopePathQuery = useQuery(pathQueryOptions(pathId));

  const [revealed, setRevealed] = useState(false);

  // Read back the keys from the same factories that mint them (the
  // `progressSummaryQueryOptions` house rule, Streaks TDD §8) — never a second
  // `clientTimezoneOffsetMinutes()`-backed key hand-spelled here.
  const queueKey = reviewQueueQueryOptions(true, pathId).queryKey;
  const summaryKey = reviewSummaryQueryOptions(true).queryKey;
  const progressSummaryKey = progressSummaryQueryOptions(true).queryKey;

  const gradeMutation = useMutation({
    mutationFn: gradeCard,
    onSuccess: (_result, variables) => {
      setRevealed(false);

      // 1. Advance the local queue. `got_it` satisfies the card and it drops
      // out entirely. `again` (D8: demoted, re-served later the same session)
      // also drops it here — never re-queued with a locally patched `rung` —
      // because the card carries its own `got_it_interval_days` preview, and
      // recomputing that after a demotion would need a second copy of the
      // ladder client-side, which §8 forbids (the server is the one place the
      // ladder lives). Dropping it instead means the demoted card is briefly
      // absent from `cards` until the authoritative refetch below re-inserts
      // it with the server's own recomputed preview — never a stale one.
      queryClient.setQueryData<ReviewQueue>(queueKey, (old) => {
        if (!old) return old;
        const card = old.cards.find((c) => c.card_id === variables.card_id);
        if (!card) return old;
        const rest = old.cards.filter((c) => c.card_id !== variables.card_id);
        if (variables.grade === "got_it") {
          return { ...old, completed: old.completed + 1, cards: rest };
        }
        return { ...old, cards: rest };
      });

      // 2. The pill/`Due today` count only drops once a card is actually
      // satisfied — an `again` leaves it part of today's business.
      if (variables.grade === "got_it") {
        queryClient.setQueryData<ReviewSummary>(summaryKey, (old) =>
          old ? { ...old, due_count: Math.max(0, old.due_count - 1) } : old,
        );
      }
      void queryClient.invalidateQueries({ queryKey: FLASHCARDS_QUERY_PREFIX });

      // 3. Phase 3 TDD §8: the day's first review advances the streak line —
      // the same "Day 7 🔥" beat the day's first completion gets (Streaks
      // D10), from a **flashcards** mutation reaching into the **progress**
      // cache. `completed_today` is the wrong "already counted today" guard
      // here (docs/api.md: it counts lesson completions only, and a review
      // never moves it) — so this keys off **today's activity cell** instead
      // (oldest-first, ending at today): if it is already `> 0` the day is
      // already active, from an earlier review today or a lesson completion,
      // and the patch must no-op.
      queryClient.setQueryData<ProgressSummary>(progressSummaryKey, (old) => {
        if (!old) return old;
        const lastDay = old.activity.length - 1;
        if (lastDay < 0 || old.activity[lastDay].count > 0) return old;
        const current = old.current_streak + 1;
        return {
          ...old,
          current_streak: current,
          best_streak: Math.max(old.best_streak, current),
          activity: old.activity.map((cell, index) =>
            index === lastDay ? { ...cell, count: cell.count + 1 } : cell,
          ),
        };
      });
      void queryClient.invalidateQueries({ queryKey: PROGRESS_QUERY_PREFIX });
    },
  });

  // A direct/deep link with the flag off (D10: the whole surface is a router-
  // level gate server-side, `404` on every route) — this is the frontend's
  // equivalent dead end, distinct from `UnavailableState` below (a genuine
  // fetch failure): there is nothing transient to retry here.
  if (!flashcardsEnabled) {
    return (
      <Workspace testid="review-session" width="lesson">
        <StateCard testid="review-unavailable">
          <h1 className="text-lg font-semibold">Review isn't available.</h1>
          <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
            Head back home — there's nothing to review from here.
          </p>
          <Link to="/" className="mt-5 inline-block text-sm text-teal">
            Back to your paths
          </Link>
        </StateCard>
      </Workspace>
    );
  }

  const queue = queueQuery.data;
  const scopeLabel = pathId === null ? "All paths" : (scopePathQuery.data?.title ?? "This path");
  const currentCard = queue?.cards[0];

  return (
    <Workspace testid="review-session" width="lesson">
      <div className="flex items-center justify-between gap-3">
        <p className="kicker">
          {queue !== undefined && queue.total > 0
            ? `Card ${Math.min(queue.completed + 1, queue.total)} of ${queue.total}`
            : "Review"}
        </p>
        <span
          data-testid="review-scope-chip"
          className="rounded-full bg-elevated px-2.5 py-1 text-xs font-medium text-mist"
        >
          {scopeLabel}
        </span>
      </div>

      <div className="mt-5">
        {queue === undefined ? (
          queueQuery.isError ? (
            <UnavailableState />
          ) : (
            <LoadingState />
          )
        ) : queue.total === 0 ? (
          <NothingDueState />
        ) : currentCard === undefined ? (
          <SessionComplete
            scopePathId={queue.scope_path_id}
            otherDueCount={queue.other_due_count}
          />
        ) : (
          <ReviewCard
            card={currentCard}
            revealed={revealed}
            onReveal={() => setRevealed(true)}
            onGrade={(grade) =>
              gradeMutation.mutate({
                card_id: currentCard.card_id,
                grade,
                rung_before: currentCard.rung,
                tz_offset_minutes: clientTimezoneOffsetMinutes(),
              })
            }
            grading={gradeMutation.isPending}
          />
        )}
      </div>

      <Link
        to="/"
        data-testid="review-done-for-now"
        className="mt-6 block text-center text-sm text-mist transition-colors hover:text-porcelain"
      >
        Done for now
      </Link>
    </Workspace>
  );
}

function LoadingState() {
  return <p className="text-sm text-mist">Loading today's review…</p>;
}

function NothingDueState() {
  // An invitation, never a debt (PRD §4.8/§3): the true backlog is never
  // displayed, and an empty day reads as "nothing to do," not "you're behind."
  return (
    <StateCard testid="review-nothing-due">
      <h1 className="text-lg font-semibold">Nothing due today.</h1>
      <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
        Come back tomorrow, or finish a lesson to draft a few more cards.
      </p>
    </StateCard>
  );
}

function UnavailableState() {
  return (
    <StateCard testid="review-unavailable">
      <h1 className="text-lg font-semibold">We couldn't load your review.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        Something went wrong reaching Aleph. Head back home and try again.
      </p>
      <Link
        to="/"
        data-testid="review-unavailable-back"
        className="mt-5 inline-block text-sm text-teal"
      >
        Back to your paths
      </Link>
    </StateCard>
  );
}
