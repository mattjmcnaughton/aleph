import { fireEvent, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import {
  FAILED_BEAT_TOPIC_SENTINEL,
  REFUSED_BEAT_TOPIC_SENTINEL,
  beatDetailPollRequestCount,
  configureBeats,
  createBeatBodies,
} from "../mocks/beats";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { App } from "./app";

// Deploying an analyst (PRD §3, TDD §8, AL-530): `routes/beats.new.tsx`'s
// form — Topic, Level, `Reports on ▾ Monday`, optional Guidance, primary
// action `Deploy analyst` — driven end to end through the real router,
// TanStack Query, and MSW, the same seam `onboarding.test.tsx` uses for
// `/new`.

const analystSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { analyst: true } },
};

function useAnalystSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(analystSession)));
}

async function gotoDeployAnalyst() {
  useAnalystSession();
  window.history.pushState({}, "", "/beats/new");
  render(<App />);
  return (await screen.findByRole("textbox", { name: /topic/i })) as HTMLInputElement;
}

function pickLevel(name: RegExp) {
  fireEvent.click(screen.getByRole("radio", { name }));
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: /deploy analyst/i }));
}

describe("Deploy an analyst — /beats/new", () => {
  it("[FIX 3, flag off] renders a dead end, not the enabled form — no request", async () => {
    // Default fake session ships `analyst: false` (no `useAnalystSession()`
    // override) — the D10 dead end, matching `routes/cards.tsx`/
    // `routes/review.tsx`'s own flag-off shape. Before this fix the whole
    // form rendered anyway, submit button included, and tapping "Deploy
    // analyst" silently did nothing (`onSubmit`'s own `!analystEnabled`
    // early return).
    window.history.pushState({}, "", "/beats/new");
    render(<App />);

    await screen.findByTestId("beats-new-unavailable");
    expect(screen.queryByRole("textbox", { name: /topic/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /deploy analyst/i })).toBeNull();
    expect(createBeatBodies()).toEqual([]);
  });

  it("[AL-530] shows the deploy form: topic, level, Reports on, guidance", async () => {
    await gotoDeployAnalyst();

    screen.getByRole("textbox", { name: /topic/i });
    screen.getByRole("radio", { name: /new to it/i });
    screen.getByRole("radio", { name: /some experience/i });
    screen.getByRole("radio", { name: /i work in it/i });
    const anchor = screen.getByLabelText(/reports on/i) as HTMLSelectElement;
    expect(anchor.value).toBe("0");
    screen.getByLabelText(/guidance/i);
    screen.getByRole("button", { name: /deploy analyst/i });
  });

  it("[AL-530] deploys and navigates straight to the Beat view", async () => {
    const topic = await gotoDeployAnalyst();

    fireEvent.change(topic, { target: { value: "EU AI regulation" } });
    pickLevel(/some experience/i);
    fireEvent.change(screen.getByLabelText(/guidance/i), {
      target: { value: "  policy and enforcement, not stock moves  " },
    });
    submit();

    await screen.findByTestId("standing-orders");
    const bodies = createBeatBodies();
    const sent = bodies[bodies.length - 1] ?? {};
    expect(sent.topic).toBe("EU AI regulation");
    expect(sent.level).toBe("some_experience");
    expect(sent.anchor_weekday).toBe(0);
    expect(sent.guidance).toBe("policy and enforcement, not stock moves");
  });

  it("[AL-530] sends the chosen Anchor day", async () => {
    const topic = await gotoDeployAnalyst();

    fireEvent.change(topic, { target: { value: "GLP-1 drugs" } });
    pickLevel(/new to it/i);
    fireEvent.change(screen.getByLabelText(/reports on/i), { target: { value: "3" } });
    submit();

    await screen.findByTestId("standing-orders");
    const bodies = createBeatBodies();
    expect(bodies[bodies.length - 1]?.anchor_weekday).toBe(3);
  });

  it("guidance is optional — submitting without it omits the field", async () => {
    const topic = await gotoDeployAnalyst();

    fireEvent.change(topic, { target: { value: "The Rust release train" } });
    pickLevel(/work in it/i);
    submit();

    await screen.findByTestId("standing-orders");
    const bodies = createBeatBodies();
    expect("guidance" in (bodies[bodies.length - 1] ?? {})).toBe(false);
  });

  it("[FIX 4] the seed-then-poll handoff: the 202 body seeds a non-terminal cache entry, so the Beat view's own detail poll actually runs and the rail arrives", async () => {
    // The shipped route's `entries=[]` is unconditional — a fresh claim is
    // spawned, never awaited, so the 202 body can never carry a published
    // Brief. This is the coverage the original mock hid entirely: with
    // `settle()` running inside the `POST` handler itself, every deploy test
    // got a terminal 202 body and `routes/beats.new.tsx`'s `setQueryData`
    // seed meant the Beat view never issued a single `GET /beats/{id}` —
    // this test is the one that would have caught that regression.
    const topic = await gotoDeployAnalyst();

    fireEvent.change(topic, { target: { value: "EU AI regulation" } });
    pickLevel(/some experience/i);
    submit();

    await screen.findByTestId("standing-orders");
    const beatId = window.location.pathname.split("/").pop() as string;

    // The rail can only arrive through a real poll settling the Beat — the
    // seeded 202 body carried no entries at all.
    const rail = await screen.findByTestId("beat-rail");
    expect(rail.querySelector('[data-testid="beat-rail-published"]')).not.toBeNull();
    expect(beatDetailPollRequestCount(beatId)).toBeGreaterThan(0);
  });

  it("[AL-530] a Beat that refuses on research still navigates — the refusal renders on the Beat view", async () => {
    const topic = await gotoDeployAnalyst();

    fireEvent.change(topic, { target: { value: `how to ${REFUSED_BEAT_TOPIC_SENTINEL} weapons` } });
    pickLevel(/new to it/i);
    submit();

    const refused = await screen.findByTestId("beat-refused");
    expect(refused.getAttribute("data-variant")).toBe("refusal");
    expect(screen.queryByTestId("beat-failed")).toBeNull();
  });

  it("[AL-530] handles the 429 Beat-cap envelope gracefully", async () => {
    configureBeats({ rateLimited: true });
    const topic = await gotoDeployAnalyst();

    fireEvent.change(topic, { target: { value: "Anything at all" } });
    pickLevel(/new to it/i);
    submit();

    await screen.findByTestId("deploy-beat-ratelimit");
    // Inputs are preserved so the learner can try again later.
    expect(topic.value).toBe("Anything at all");
  });

  it("[AL-530] a generic create failure surfaces an error, not a silent dead-end", async () => {
    server.use(
      http.post(`${API_V1_BASE}/beats`, () =>
        HttpResponse.json(
          { error: { code: "internal_error", message: "Something went wrong." } },
          { status: 500 },
        ),
      ),
    );
    const topic = await gotoDeployAnalyst();

    fireEvent.change(topic, { target: { value: `${FAILED_BEAT_TOPIC_SENTINEL} anything` } });
    pickLevel(/new to it/i);
    submit();

    await screen.findByTestId("deploy-beat-error");
  });
});
