import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { sessionQueryOptions } from "../../lib/auth";
import { LEARNING_KEY, clearLearningStorage } from "../../lib/learning-tutor";

/** Scoped keys prevent flashes immediately; cancellation also fences late GETs. */
export function LearningAccountBoundary() {
  const account =
    useQuery({ ...sessionQueryOptions, refetchOnWindowFocus: "always" }).data?.user?.id ?? null;
  const previous = useRef(account);
  const client = useQueryClient();
  useEffect(() => {
    if (previous.current === account) return;
    const old = previous.current;
    previous.current = account;
    clearLearningStorage();
    const queryKey = old ? [...LEARNING_KEY, old] : LEARNING_KEY;
    void client.cancelQueries({ queryKey });
    client.removeQueries({ queryKey });
  }, [account, client]);
  return null;
}
