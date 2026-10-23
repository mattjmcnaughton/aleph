import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { makeQueryClient } from "../../app/app";
import { API_V1_BASE } from "../../lib/api";
import { sessionQueryOptions } from "../../lib/auth";
import { learningStateOptions, readLearningRequest } from "../../lib/learning-tutor";
import type { LearningOperationRequest } from "../../lib/learning-tutor-types";
import { learnerUser } from "../../mocks/handlers";
import { learningFake, learningReceipt } from "../../mocks/learning-tutor";
import { server } from "../../mocks/server";
import { LearningAccountBoundary } from "./account-boundary";
import { useLearningTutor } from "./use-learning-tutor";

function Harness({ account }: { account: string }) {
  const tutor = useLearningTutor(account);
  if (!tutor.state.data) return <p>Loading</p>;
  return (
    <>
      <p>{tutor.state.data?.focus}</p>
      <p>{tutor.state.data?.history.items.length ?? 0} turns</p>
      <button type="button" onClick={() => void tutor.start("My interest")}>
        Send test
      </button>
      <button type="button" onClick={() => void tutor.start()}>
        Recover test
      </button>
      <button type="button" onClick={() => tutor.change.mutate({ kind: "clear" })}>
        Clear test
      </button>
    </>
  );
}
function AccountHarness() {
  const account = useQuery(sessionQueryOptions).data?.user?.id;
  return (
    <>
      <LearningAccountBoundary />
      {account && <Harness key={account} account={account} />}
    </>
  );
}
function mount() {
  const client = makeQueryClient();
  client.setQueryData(sessionQueryOptions.queryKey, {
    authenticated: true,
    provider: "keycloak",
    user: learnerUser,
  });
  render(
    <QueryClientProvider client={client}>
      <AccountHarness />
    </QueryClientProvider>,
  );
  return client;
}

describe("Learning operation and account lifecycle", () => {
  it("keeps the original UUID/body on response loss and uses a new UUID for a later turn", async () => {
    const fake = learningFake();
    server.use(...fake.handlers);
    const seen: LearningOperationRequest[] = [];
    server.use(
      http.post(`${API_V1_BASE}/learning-tutor/operations`, async ({ request }) => {
        const body = (await request.json()) as LearningOperationRequest;
        seen.push(body);
        if (seen.length === 1) return HttpResponse.error();
        const receipt = learningReceipt(body);
        fake.receipts.set(body.request_id, receipt);
        return HttpResponse.json(receipt);
      }),
    );
    mount();
    await screen.findByText("0 turns");
    fireEvent.click(screen.getByText("Send test"));
    await waitFor(() => expect(seen).toHaveLength(1));
    await waitFor(() => expect(readLearningRequest(learnerUser.id)).not.toBeNull());
    fireEvent.click(screen.getByText("Recover test"));
    await waitFor(() => expect(seen).toHaveLength(2));
    expect(seen[1]).toEqual(seen[0]);
    await waitFor(() => expect(readLearningRequest(learnerUser.id)).toBeNull());
    fireEvent.click(screen.getByText("Send test"));
    await waitFor(() => expect(seen).toHaveLength(3));
    expect(seen[2].request_id).not.toBe(seen[0].request_id);
  });
  it("does not publish a prior account's late mutation into cache or storage", async () => {
    const fake = learningFake();
    server.use(...fake.handlers);
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let started = false;
    server.use(
      http.post(`${API_V1_BASE}/learning-tutor/operations`, async ({ request }) => {
        const body = (await request.json()) as LearningOperationRequest;
        started = true;
        await gate;
        return HttpResponse.json(learningReceipt(body));
      }),
    );
    const client = mount();
    fireEvent.click(await screen.findByText("Send test"));
    await waitFor(() => expect(started).toBe(true));
    act(() =>
      client.setQueryData(sessionQueryOptions.queryKey, {
        authenticated: true,
        provider: "keycloak",
        user: { ...learnerUser, id: "second" },
      }),
    );
    await waitFor(() => expect(readLearningRequest(learnerUser.id)).toBeNull());
    await act(async () => release?.());
    await waitFor(() =>
      expect(client.getQueriesData({ queryKey: ["learning-tutor", learnerUser.id] })).toEqual([]),
    );
    expect(sessionStorage.getItem(`learning-tutor-operation:${learnerUser.id}`)).toBeNull();
  });
  it("cancels late reads on account change and cannot show the old focus", async () => {
    const fake = learningFake();
    server.use(...fake.handlers);
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let reads = 0;
    server.use(
      http.get(`${API_V1_BASE}/learning-tutor/`, async () => {
        reads++;
        if (reads === 1) {
          await gate;
          return HttpResponse.json({ ...fake.state, focus: "Private old focus" });
        }
        return HttpResponse.json({ ...fake.state, focus: "New focus" });
      }),
    );
    const client = mount();
    await waitFor(() => expect(reads).toBe(1));
    act(() =>
      client.setQueryData(sessionQueryOptions.queryKey, {
        authenticated: true,
        provider: "keycloak",
        user: { ...learnerUser, id: "second" },
      }),
    );
    await screen.findByText("New focus");
    await act(async () => release?.());
    expect(screen.queryByText("Private old focus")).toBeNull();
    expect(client.getQueryData(learningStateOptions(learnerUser.id).queryKey)).toBeUndefined();
  });
});
