// The delete orchestration behind the "Your paths" switcher (§5.5/W5): the
// route renders rows; this owns confirmation, DELETE, and cache updates.
//
// Two invariants live in `forget`, together, because they only make sense
// together:
//
//  1. Non-optimistic until the server has actually said the path is gone: the
//     hook only ever calls `forget` after a real `204` or a `404`
//     folded into the same success path, never before.
//  2. Cancel-before-filter: an in-flight `GET /paths` that started before the
//     DELETE would resurrect the row when it resolves, so it is cancelled
//     first; the deleted path's *detail* query is evicted for the same
//     reason. Only then is the list invalidated, so the server's ordering
//     stays authoritative.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PATHS_LIST_QUERY_KEY, type PathList, deletePath, isNotFound, pathQueryKey } from "./api";

export interface DeletePath {
  /** The row currently mid-confirm, if any — one at a time. */
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
  // Keep errors per row and attempt without resetting an in-flight mutation.
  const [failedId, setFailedId] = useState<string | null>(null);

  /** Drop a path the server no longer has (see the invariants above). */
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
