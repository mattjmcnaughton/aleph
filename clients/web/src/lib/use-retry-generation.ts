// The retry/regenerate orchestration shared by every trigger+poll surface
// (onboarding, path view, lesson view). Each of them re-triggers generation for
// one id, then RESETS (not invalidates) the poll query so the backoff restarts
// at the 2s cadence rather than resuming at the 5s ceiling — the invariant
// documented in `./polling` (`dataUpdateCount` never resets, so an in-place
// resume would keep polling at the ceiling). This hook is the single home for
// that invariant plus the rate-limited-vs-generic error split (§5.6/F1), so the
// three surfaces don't each re-derive it.

import { type QueryKey, useMutation, useQueryClient } from "@tanstack/react-query";
import { isRateLimited } from "./api";

export interface UseRetryGenerationOptions {
  /** Fires the retry/regenerate POST for one id (e.g. `retryPath`, `generateLesson`). */
  mutationFn: (id: string) => Promise<unknown>;
  /** The poll query key to reset on success, for that id. */
  queryKey: (id: string) => QueryKey;
}

export interface RetryGeneration {
  /** Trigger the retry for one id. */
  retry: (id: string) => void;
  /** The retry POST is in flight. */
  retrying: boolean;
  /** The retry hit the daily cap (`429`) — surface the daily-cap notice. */
  rateLimited: boolean;
  /** The retry failed for any other reason — surface a generic notice. */
  errored: boolean;
  /** Clear the mutation state (e.g. when the learner edits/leaves the surface). */
  reset: () => void;
}

export function useRetryGeneration({
  mutationFn,
  queryKey,
}: UseRetryGenerationOptions): RetryGeneration {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn,
    // Reset (not invalidate) so the poll restarts at the 2s cadence — see the
    // module comment and `./polling`.
    onSuccess: (_result, id) => queryClient.resetQueries({ queryKey: queryKey(id) }),
  });
  const rateLimited = mutation.isError && isRateLimited(mutation.error);
  return {
    retry: (id) => mutation.mutate(id),
    retrying: mutation.isPending,
    rateLimited,
    errored: mutation.isError && !rateLimited,
    reset: () => mutation.reset(),
  };
}
