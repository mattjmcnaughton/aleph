// The delete orchestration behind the "Your paths" switcher (§5.5/W5): the
// route renders rows, this owns the DELETE and the cache surgery that keeps
// the list correct afterwards. The confirm/DELETE/one-row-at-a-time state
// machine itself lives in `use-row-delete.ts` — shared with `/cards`' own
// `use-delete-card.ts`, since the two used to duplicate it exactly. What is
// left here is what is genuinely path-specific: `forget`, below, is this call
// site's `settle` (`use-row-delete.ts`'s own term).
//
// Two invariants live in `forget`, together, because they only make sense
// together:
//
//  1. Non-optimistic until the server has actually said the path is gone: the
//     shared hook only ever calls `forget` after a real `204` or a `404`
//     folded into the same success path, never before.
//  2. Cancel-before-filter: an in-flight `GET /paths` that started before the
//     DELETE would resurrect the row when it resolves, so it is cancelled
//     first; the deleted path's *detail* query is evicted for the same
//     reason. Only then is the list invalidated, so the server's ordering
//     stays authoritative.

import { useQueryClient } from "@tanstack/react-query";
import { PATHS_LIST_QUERY_KEY, type PathList, deletePath, pathQueryKey } from "./api";
import { type RowDelete, useRowDelete } from "./use-row-delete";

/** Re-exported under its own name — every call site names its delete result
 *  after what it deletes, not after the shared machine behind it. */
export type DeletePath = RowDelete;

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

  /** Drop a path the server no longer has (see the invariants above). */
  async function forget(id: string): Promise<void> {
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

  return useRowDelete({ mutationFn: deletePath, settle: forget, onCancelled });
}
