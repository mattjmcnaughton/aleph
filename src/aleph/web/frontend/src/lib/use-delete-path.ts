// The delete orchestration behind the "Your paths" switcher (§5.5/W5), factored
// out of the route the same way `use-retry-generation` factors out the retry
// POST: the route renders rows, this hook owns the confirm step, the DELETE, and
// the cache surgery that keeps the list correct afterwards.
//
// Three invariants live here, together, because they only make sense together:
//
//  1. Non-optimistic: nothing leaves the cached list until the server has said
//     the path is gone. A failed delete must leave the row exactly where it was.
//  2. Cancel-before-filter: an in-flight `GET /paths` that started before the
//     DELETE would resurrect the row when it resolves, so it is cancelled first;
//     the deleted path's *detail* query is evicted for the same reason. Only
//     then is the list invalidated, so the server's ordering stays authoritative.
//  3. Row-scoped state: `pending`/`errored` belong to one row, not to the hook.
//     A shared `isPending` renders "Deleting…" on whichever row happens to be
//     mid-confirm, which is the wrong row as soon as two are in play.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PATHS_LIST_QUERY_KEY, type PathList, deletePath, isNotFound, pathQueryKey } from "./api";

export interface DeletePath {
  /** The row currently mid-confirm, if any — one at a time (§5.5). */
  confirmingId: string | null;
  /** Arm the confirm step for one row, closing any other row's. */
  ask: (id: string) => void;
  /** Abandon the confirm step without deleting anything. */
  cancel: () => void;
  /** Fire the DELETE for one row (only ever from the confirm step). */
  confirm: (id: string) => void;
  /** True while THIS row's DELETE is in flight — never a sibling's. */
  isDeleting: (id: string) => boolean;
  /** True once THIS row's DELETE failed in a way the learner can retry. */
  isErrored: (id: string) => boolean;
}

export interface UseDeletePathOptions {
  /**
   * Called once the path is gone server-side and out of the cached list — the
   * switcher uses it to move focus off the row it just destroyed (C3).
   */
  onDeleted?: (id: string) => void;
  /**
   * Called with the row whose confirm was abandoned — same reason: the buttons
   * the learner was on are about to be replaced by that row's Delete button.
   */
  onCancelled?: (id: string) => void;
}

export function useDeletePath({ onDeleted, onCancelled }: UseDeletePathOptions = {}): DeletePath {
  const queryClient = useQueryClient();

  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  // The failure belongs to one row *and* one attempt: re-arming the confirm or
  // retrying clears it. Tracking it here rather than reading `mutation.isError`
  // means nothing ever has to `reset()` a mutation that may still be in flight.
  const [failedId, setFailedId] = useState<string | null>(null);

  /** Drop a path the server no longer has (see invariants 1-2 above). */
  async function forget(id: string): Promise<void> {
    setConfirmingId(null);
    setFailedId(null);
    await queryClient.cancelQueries({ queryKey: PATHS_LIST_QUERY_KEY });
    queryClient.setQueryData<PathList>(PATHS_LIST_QUERY_KEY, (old) =>
      old ? { paths: old.paths.filter((path) => path.id !== id) } : old,
    );
    queryClient.removeQueries({ queryKey: pathQueryKey(id) });
    // Not evicting `["lessons", …]` for this path: the list payload carries no
    // lesson ids, so there is nothing to target from here. A stale lesson query
    // is harmless — the lesson view 404s on its next fetch and recovers there.
    onDeleted?.(id);
    await queryClient.invalidateQueries({ queryKey: PATHS_LIST_QUERY_KEY });
  }

  const mutation = useMutation({
    mutationFn: deletePath,
    onSuccess: (_result, id) => forget(id),
    onError: async (error, id) => {
      // A `404` means the path is already gone — another tab deleted it, or a
      // first DELETE landed and its `204` never made it back. That is exactly
      // the outcome the learner asked for, so it takes the same removal path.
      // Reporting "check your connection" instead would strand the row forever:
      // every retry re-404s, and no poll can ever remove it.
      if (isNotFound(error)) {
        await forget(id);
        return;
      }
      setFailedId(id);
    },
  });

  return {
    confirmingId,
    ask: (id) => {
      setConfirmingId(id);
      setFailedId(null);
    },
    cancel: () => {
      setConfirmingId(null);
      setFailedId(null);
      if (confirmingId) onCancelled?.(confirmingId);
    },
    confirm: (id) => {
      setFailedId(null);
      mutation.mutate(id);
    },
    isDeleting: (id) => mutation.isPending && mutation.variables === id,
    isErrored: (id) => failedId === id,
  };
}
