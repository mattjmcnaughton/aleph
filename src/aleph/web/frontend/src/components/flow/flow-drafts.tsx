// The receipt's drafts batch (flow TDD D7, §5.5): every drafted set from the
// flow, shown once, at the end, instead of interrupting each lesson's own
// completion. Not a new component — `DraftList` (Phase 3) is reused verbatim
// per lesson; this is the *container* that owns the per-lesson queries and
// keep/trigger mutations the lesson route otherwise owns itself.

import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import {
  FLASHCARDS_QUERY_PREFIX,
  type FlashcardDrafts,
  flashcardDraftsQueryKey,
  flashcardDraftsQueryOptions,
  keepFlashcardDrafts,
  triggerFlashcardDrafts,
} from "../../lib/api";
import type { FlowCompletion } from "../../lib/flow";
import { DraftList } from "../review/draft-list";

/**
 * Nothing while `completed` is empty (D7 — an ended-early flow with zero
 * completions has nothing to bat). `flashcardsEnabled` off renders nothing
 * too: with the flag off there is no `flashcards` route to poll, matching
 * every other flag-gated block in this app (`skipToken`, no fetch).
 */
export function FlowDrafts({
  completed,
  flashcardsEnabled,
  autoDraft,
}: {
  completed: FlowCompletion[];
  flashcardsEnabled: boolean;
  autoDraft: boolean;
}) {
  // One `useQueries` call, not `completed.length` separate `useQuery`s — the
  // count of lessons in a flow varies call to call, which is exactly the case
  // `useQueries` exists for (a fixed-size hook list would violate the rules
  // of hooks the moment the flow length differs from the last render).
  const draftQueries = useQueries({
    queries: completed.map((c) => flashcardDraftsQueryOptions(c.lessonId, flashcardsEnabled)),
  });

  if (completed.length === 0 || !flashcardsEnabled) return null;

  const totalCards = draftQueries.reduce(
    (sum, q) => sum + (q.data?.state === "generated" ? q.data.cards.length : 0),
    0,
  );
  // With Auto-draft off, a lesson sitting at `not_started` still earns its own
  // row (the `Draft flashcards` affordance) — so the batch is not silent just
  // because nothing has been drafted yet.
  const anyAskable = draftQueries.some((q) => q.data?.state === "not_started") && !autoDraft;
  if (totalCards === 0 && !anyAskable) return null;

  return (
    <section data-testid="flow-receipt-drafts" className="mt-8">
      <p className="kicker text-iris-400">
        Aleph drafted {totalCards} {totalCards === 1 ? "card" : "cards"}
      </p>
      <div className="mt-3 flex flex-col gap-5">
        {completed.map((completion, index) => (
          <FlowDraftsLesson
            key={completion.lessonId}
            lessonId={completion.lessonId}
            title={completion.title}
            drafts={draftQueries[index]?.data}
            autoDraft={autoDraft}
          />
        ))}
      </div>
    </section>
  );
}

function FlowDraftsLesson({
  lessonId,
  title,
  drafts,
  autoDraft,
}: {
  lessonId: string;
  title: string;
  drafts: FlashcardDrafts | undefined;
  autoDraft: boolean;
}) {
  const queryClient = useQueryClient();

  // Mirrors `routes/lessons.$lessonId.tsx`'s own keep/trigger mutations —
  // same cache-write-then-invalidate shape, scoped to this one lesson's key.
  const keepMutation = useMutation({
    mutationFn: (keptIds: string[]) => keepFlashcardDrafts(lessonId, keptIds),
    onSuccess: () => {
      queryClient.setQueryData<FlashcardDrafts>(flashcardDraftsQueryKey(lessonId), (old) =>
        old ? { ...old, cards: [] } : old,
      );
      void queryClient.invalidateQueries({ queryKey: FLASHCARDS_QUERY_PREFIX });
    },
  });
  const triggerMutation = useMutation({
    mutationFn: () => triggerFlashcardDrafts(lessonId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: flashcardDraftsQueryKey(lessonId) });
    },
  });

  if (drafts === undefined) return null;
  const hasSomethingToShow =
    (drafts.state === "generated" && drafts.cards.length > 0) ||
    (drafts.state === "not_started" && !autoDraft);
  if (!hasSomethingToShow) return null;

  return (
    <div data-testid="flow-draft-lesson" data-lesson-id={lessonId}>
      <p className="truncate text-xs font-semibold uppercase tracking-kicker text-slate">{title}</p>
      <DraftList
        drafts={drafts}
        onKeep={(keptIds) => keepMutation.mutate(keptIds)}
        keeping={keepMutation.isPending}
        keepErrored={keepMutation.isError}
        onRetry={() => triggerMutation.mutate()}
        retrying={triggerMutation.isPending}
        // The receipt has no rate-limit-specific copy of its own — a capped
        // trigger reads through `triggerErrored` here, same generic notice.
        triggerRateLimited={false}
        triggerErrored={triggerMutation.isError}
        onDraft={autoDraft ? undefined : () => triggerMutation.mutate()}
        drafting={triggerMutation.isPending}
      />
    </div>
  );
}
