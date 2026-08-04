// The delete orchestration behind `/cards` (AL-410 plan §6). The confirm/
// DELETE/one-row-at-a-time state machine lives in `use-row-delete.ts` — shared
// with the switcher's own `use-delete-path.ts`, since the two used to
// duplicate it exactly (same `confirmingId`/`failedId` fields, same
// `ask`/`cancel`/`confirm`/`isDeleting`/`isErrored` shape, same
// `isNotFound`→success folding). What is left here is what is genuinely
// card-specific: `settled`, below, is this call site's `settle`
// (`use-row-delete.ts`'s own term).
//
// `settled` differs from `use-delete-path.ts`'s `forget` in exactly one way,
// and it is the reason a card delete does not just reuse `forget` outright:
// there is no single cached list to filter in place here. `/cards` reads
// through `useInfiniteQuery` (`cardsQueryOptions`), page count unknown to this
// hook, so "the card is gone" is written the same way every other flashcards
// mutation writes it — `invalidateQueries({queryKey: FLASHCARDS_QUERY_PREFIX})`
// (AL-410 plan §6) — rather than a hand-rolled walk across however many pages
// happen to be loaded.

import { useQueryClient } from "@tanstack/react-query";
import { FLASHCARDS_QUERY_PREFIX, deleteCard } from "./api";
import { type RowDelete, useRowDelete } from "./use-row-delete";

/** Re-exported under its own name — every call site names its delete result
 *  after what it deletes, not after the shared machine behind it. */
export type DeleteCard = RowDelete;

export interface UseDeleteCardOptions {
  /** Called once the card is gone server-side — `routes/cards.tsx` uses it to
   *  move focus off the row that just vanished. */
  onDeleted?: (id: string) => void;
  /** Called with the row whose confirm was abandoned — its own Delete button
   *  is about to replace the "Keep it" button the learner is standing on. */
  onCancelled?: (id: string) => void;
}

export function useDeleteCard({ onDeleted, onCancelled }: UseDeleteCardOptions = {}): DeleteCard {
  const queryClient = useQueryClient();

  async function settled(id: string): Promise<void> {
    onDeleted?.(id);
    // The one invalidation every flashcards mutation makes (plan §6): it is
    // what keeps the Daily queue's cached copy of this same card (if it was
    // due today) and the header pill's due count from silently disagreeing
    // with a delete that already landed here.
    await queryClient.invalidateQueries({ queryKey: FLASHCARDS_QUERY_PREFIX });
  }

  return useRowDelete({ mutationFn: deleteCard, settle: settled, onCancelled });
}
