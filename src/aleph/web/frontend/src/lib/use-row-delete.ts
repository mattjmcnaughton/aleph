// Shared state machine for a "confirm → DELETE → gone" row. It owns the
// confirmation and failure state and treats a 404 as success. The caller's
// `settle` callback owns cache updates after deletion is confirmed.

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { isNotFound } from "./api";

export interface RowDelete {
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

export interface UseRowDeleteOptions<T> {
  /** Fires the DELETE for one row's id. */
  mutationFn: (id: string) => Promise<T>;
  /**
   * Runs once the server has confirmed the row is gone — a real `204`, or a
   * `404` folded into the same success path (another tab, or a repeat tap,
   * got there first — the outcome the learner asked for either way). Owns
   * whatever cache surgery and `onDeleted` callback this row type needs, in
   * whatever order its own invariants require (see the module header) — the
   * hook itself never reaches into the query client.
   */
  settle: (id: string) => Promise<void> | void;
  /** Called with the row whose confirm step was abandoned, before its own
   *  Delete button reappears (C3: focus has somewhere to land). */
  onCancelled?: (id: string) => void;
}

export function useRowDelete<T>({
  mutationFn,
  settle,
  onCancelled,
}: UseRowDeleteOptions<T>): RowDelete {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  // The failure belongs to one row *and* one attempt: re-arming the confirm
  // or retrying clears it. Tracking it here rather than reading
  // `mutation.isError` means nothing ever has to `reset()` a mutation that
  // may still be in flight.
  const [failedId, setFailedId] = useState<string | null>(null);

  async function finish(id: string): Promise<void> {
    setConfirmingId(null);
    setFailedId(null);
    await settle(id);
  }

  const mutation = useMutation({
    mutationFn,
    onSuccess: (_result, id) => finish(id),
    onError: async (error, id) => {
      if (isNotFound(error)) {
        await finish(id);
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
