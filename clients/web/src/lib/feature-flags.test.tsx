import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { authenticatedSession } from "../mocks/handlers";
import { server } from "../mocks/server";
import { API_V1_BASE } from "./api";
import { useFeatureFlag } from "./feature-flags";

// Flags ride the session payload the SPA already fetches (`user.feature_flags`,
// resolved per learner on the backend), so gating a surface costs no request.
// AL-230 gates the tutor rail's entry point on `useFeatureFlag("tutor")`; these
// tests pin the three cases that gate depends on: on, off, and a key the
// backend never sent.

const TUTOR_FLAG = "tutor";

function Probe({ flagKey = TUTOR_FLAG }: { flagKey?: string }) {
  const enabled = useFeatureFlag(flagKey);
  return <span>{enabled ? "flag-on" : "flag-off"}</span>;
}

function renderProbe(flagKey?: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Probe flagKey={flagKey} />
    </QueryClientProvider>,
  );
}

function serveSessionFlags(feature_flags: Record<string, boolean>) {
  server.use(
    http.get(`${API_V1_BASE}/auth/session`, () =>
      HttpResponse.json({
        ...authenticatedSession,
        user: { ...authenticatedSession.user, feature_flags },
      }),
    ),
  );
}

describe("useFeatureFlag", () => {
  it("resolves to off when the session reports the flag off (the dark default)", async () => {
    serveSessionFlags({ [TUTOR_FLAG]: false });
    renderProbe();
    expect(await screen.findByText("flag-off")).toBeTruthy();
  });

  it("resolves to on when the session enables the flag (e.g. an admin)", async () => {
    serveSessionFlags({ [TUTOR_FLAG]: true });
    renderProbe();
    expect(await screen.findByText("flag-on")).toBeTruthy();
  });

  it("resolves to off for a key the session never sent", async () => {
    serveSessionFlags({ [TUTOR_FLAG]: true });
    renderProbe("not_a_flag");
    expect(await screen.findByText("flag-off")).toBeTruthy();
  });

  it("resolves to off before the session query has settled", () => {
    serveSessionFlags({ [TUTOR_FLAG]: true });
    renderProbe();
    // Synchronous first paint: no session data yet, so the gate stays closed
    // rather than flashing the gated surface open.
    expect(screen.getByText("flag-off")).toBeTruthy();
  });
});
