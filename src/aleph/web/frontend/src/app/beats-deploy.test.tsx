import { fireEvent, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import {
  FAILED_BEAT_TOPIC_SENTINEL,
  REFUSED_BEAT_TOPIC_SENTINEL,
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
