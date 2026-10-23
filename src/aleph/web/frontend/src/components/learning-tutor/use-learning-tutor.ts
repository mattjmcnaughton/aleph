import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import { sessionQueryOptions } from "../../lib/auth";
import {
  LEARNING_KEY,
  clearLearningConversation,
  decideLearningRecommendation,
  getDismissed,
  getLearningOperation,
  learningError,
  learningStateOptions,
  readLearningRequest,
  recoveryKey,
  saveLearningFocus,
  startLearningOperation,
} from "../../lib/learning-tutor";
import type {
  LearningOperationRequest,
  LearningTutorOperation,
  LearningTutorState,
} from "../../lib/learning-tutor-types";
import { makePollingRefetchInterval } from "../../lib/polling";

export function useLearningTutor(account: string) {
  const client = useQueryClient();
  const state = useQuery(learningStateOptions(account));
  const [uncertain, setUncertain] = useState(() => readLearningRequest(account));
  const [operationId, setOperationId] = useState<string | null>(() =>
    sessionStorage.getItem(`learning-tutor-operation:${account}`),
  );
  const [notice, setNotice] = useState("");
  const settled = useRef<string | null>(null);
  const admitting = useRef(false);
  const currentAccount = () =>
    client.getQueryData(sessionQueryOptions.queryKey)?.user?.id === account;
  const invalidate = () => client.invalidateQueries({ queryKey: [...LEARNING_KEY, account] });
  const dismissed = useInfiniteQuery({
    queryKey: [...LEARNING_KEY, account, "dismissed", state.data?.epoch],
    queryFn: ({ pageParam, signal }) => getDismissed(pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.before ?? undefined,
    refetchOnWindowFocus: "always",
  });
  const id = state.data?.current_operation_id ?? operationId;
  const operation = useQuery({
    queryKey: [...LEARNING_KEY, account, "operation", id],
    queryFn: ({ signal }) => getLearningOperation(id as string, signal),
    enabled: Boolean(id),
    retry: false,
    refetchInterval: makePollingRefetchInterval<LearningTutorOperation>({
      isTerminal: (value) => value !== undefined && value.status !== "running",
      isErrorTerminal: (error) => error instanceof ApiError && [404, 410].includes(error.status),
    }),
  });
  useEffect(() => {
    const row = operation.data;
    if (!row || row.status === "running" || settled.current === row.id) return;
    settled.current = row.id;
    void client.invalidateQueries({ queryKey: [...LEARNING_KEY, account] });
  }, [operation.data, account, client]);

  const send = useMutation({
    mutationFn: startLearningOperation,
    onSuccess: (row) => {
      if (
        !currentAccount() ||
        row.epoch !== client.getQueryData(learningStateOptions(account).queryKey)?.epoch
      )
        return;
      sessionStorage.removeItem(recoveryKey(account));
      setUncertain(null);
      setOperationId(row.id);
      sessionStorage.setItem(`learning-tutor-operation:${account}`, row.id);
      client.setQueryData([...LEARNING_KEY, account, "operation", row.id], row);
      void invalidate();
    },
    onError: (error) => {
      if (!currentAccount()) return;
      if (error instanceof ApiError && error.status < 500) {
        sessionStorage.removeItem(recoveryKey(account));
        setUncertain(null);
        if (typeof error.details === "object" && error.details && "operation_id" in error.details) {
          setOperationId(String(error.details.operation_id));
        }
      }
      void invalidate();
    },
  });
  const change = useMutation({
    mutationFn: async (
      action:
        | { kind: "focus"; text: string }
        | { kind: "clear" }
        | { kind: "decision"; id: string; decision: "dismiss" | "restore" },
    ) => {
      const fresh = await client.fetchQuery(learningStateOptions(account));
      if (!currentAccount()) throw new ApiError("", 404, "unavailable");
      // Fetch before action, but do not silently reinterpret an action against a
      // newer revision than the one the learner actually saw.
      if (fresh.revision !== state.data?.revision)
        throw new ApiError("", 409, "conflict", undefined, { reason: "stale_state" });
      if (action.kind === "focus") return saveLearningFocus(action.text, fresh.revision);
      if (action.kind === "clear") return clearLearningConversation(fresh.revision);
      return decideLearningRecommendation(action.id, action.decision, fresh.revision);
    },
    onSuccess: async (next: LearningTutorState, action) => {
      if (!currentAccount()) return;
      await client.cancelQueries({ queryKey: [...LEARNING_KEY, account] });
      if (!currentAccount()) return;
      client.setQueryData(learningStateOptions(account).queryKey, next);
      if (action.kind === "clear") {
        sessionStorage.removeItem(recoveryKey(account));
        sessionStorage.removeItem(`learning-tutor-operation:${account}`);
        setUncertain(null);
        setOperationId(null);
        void client.cancelQueries({ queryKey: [...LEARNING_KEY, account, "operation"] });
        client.removeQueries({ queryKey: [...LEARNING_KEY, account, "operation"] });
        setNotice(
          "Learning conversation cleared. Your Learning focus and created paths and Beats are unchanged.",
        );
      }
      if (action.kind === "focus")
        setNotice(
          "Learning focus saved. Previous recommendations are no longer current; refresh when you're ready.",
        );
      void invalidate();
    },
    onError: () => {
      if (currentAccount()) void invalidate();
    },
  });
  async function start(content?: string, recommendationId?: string) {
    if (
      admitting.current ||
      send.isPending ||
      state.data?.current_operation_id ||
      operation.data?.status === "running"
    )
      return;
    if (uncertain) {
      send.mutate(uncertain);
      return;
    }
    admitting.current = true;
    try {
      const fresh = await client.fetchQuery(learningStateOptions(account));
      if (!currentAccount()) return;
      if (fresh.revision !== state.data?.revision) {
        setNotice(
          "Your learning context changed. Review the latest state, then send again. Your input is still here.",
        );
        return;
      }
      const body: LearningOperationRequest = {
        request_id: crypto.randomUUID(),
        revision: fresh.revision,
        epoch: fresh.epoch,
        ...(content === undefined
          ? { kind: "refresh" as const }
          : {
              kind: "turn" as const,
              content,
              ...(recommendationId ? { recommendation_id: recommendationId } : {}),
            }),
      };
      sessionStorage.setItem(recoveryKey(account), JSON.stringify(body));
      setUncertain(body);
      send.mutate(body);
    } catch (error) {
      if (currentAccount()) setNotice(learningError(error));
    } finally {
      admitting.current = false;
    }
  }
  return {
    account,
    state,
    dismissed,
    operation,
    send,
    change,
    start,
    uncertain,
    notice,
    busy:
      send.isPending ||
      Boolean(state.data?.current_operation_id) ||
      operation.data?.status === "running",
  };
}
export type LearningTutor = ReturnType<typeof useLearningTutor>;
