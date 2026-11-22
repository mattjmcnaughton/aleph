// Contract-shaped fakes for About you and the Learning summary
// (`GET`/`PUT /about-you`, `GET`/`POST /learning-summary`). Same shape as the
// other fakes: `configureAccount({...})` dials in a state, `resetAccount()`
// restores the defaults between tests and stories.

import { HttpResponse, http } from "msw";
import { API_V1_BASE, type LearningSummary } from "../lib/api";

/** An example written summary, on the Storybook world's paths and Beats. */
export const EXAMPLE_SUMMARY: NonNullable<LearningSummary["summary"]> = {
  areas: [
    {
      title: "Rust ownership & lifetimes",
      note: "4 of 11 lessons. Borrowing is solid; the Quick check on lifetime elision was missed once.",
    },
    {
      title: "Bayesian statistics",
      note: "Finished, 4 of 4. Priors and posteriors are covered; hierarchical models never came up.",
    },
    { title: "Kubernetes for application developers", note: "Not started yet." },
    { title: "Following", note: "AI infrastructure and Climate tech policy, weekly." },
  ],
  suggestions: [
    {
      kind: "path",
      topic: "Async Rust: borrows across .await",
      level: "some_experience",
      why: "Async is where the lifetime rules you're learning bite hardest.",
      guidance:
        "Assume ownership and lifetime annotations are known. Cover borrows held across .await, Send and 'static bounds, and Pin.",
      teaching_style: "Worked example first, then the rule.",
    },
    {
      kind: "path",
      topic: "Bayesian A/B testing in practice",
      level: "some_experience",
      why: "You finished Bayesian statistics. This puts it to work on a real product decision.",
      guidance: "Priors for conversion rates, comparing posteriors, and when to stop a test.",
      teaching_style: "",
    },
    {
      kind: "beat",
      topic: "Rust language and ecosystem releases",
      level: "some_experience",
      why: "You're learning Rust. A weekly Beat keeps you current on what changes.",
      guidance: "",
      teaching_style: "",
    },
  ],
};

interface AccountConfig {
  aboutYou: string | null;
  summary: LearningSummary;
  /** Polls a triggered run spends `generating` before it lands `ready`. */
  pollsBeforeReady: number;
  /** When true, a triggered run lands `failed` instead of `ready`. */
  failRun: boolean;
}

const BASE_SUMMARY: LearningSummary = {
  state: "none",
  facts: { lessons_complete: 8, paths: 3, beats: 2 },
  summary: null,
  written_at: null,
  min_lessons_complete: 3,
};

const defaults = (): AccountConfig => ({
  aboutYou: null,
  summary: { ...BASE_SUMMARY },
  pollsBeforeReady: 1,
  failRun: false,
});

let config = defaults();
let pollsLeft = 0;

export function resetAccount(): void {
  config = defaults();
  pollsLeft = 0;
}

export function configureAccount(overrides: Partial<AccountConfig>): void {
  config = { ...config, ...overrides };
}

/** What the fake currently stores for About you (for assertions). */
export function storedAboutYou(): string | null {
  return config.aboutYou;
}

export const accountHandlers = [
  http.get(`${API_V1_BASE}/about-you`, () => HttpResponse.json({ about_you: config.aboutYou })),
  http.put(`${API_V1_BASE}/about-you`, async ({ request }) => {
    const body = (await request.json()) as { about_you: string | null };
    const trimmed = body.about_you?.trim() ?? "";
    config.aboutYou = trimmed ? trimmed : null;
    return HttpResponse.json({ about_you: config.aboutYou });
  }),
  http.get(`${API_V1_BASE}/learning-summary`, () => {
    if (config.summary.state === "generating") {
      if (pollsLeft > 0) {
        pollsLeft -= 1;
      } else {
        config.summary = config.failRun
          ? { ...config.summary, state: "failed" }
          : {
              ...config.summary,
              state: "ready",
              summary: EXAMPLE_SUMMARY,
              written_at: "2026-11-21T12:00:00Z",
            };
      }
    }
    return HttpResponse.json(config.summary);
  }),
  http.post(`${API_V1_BASE}/learning-summary`, () => {
    if (config.summary.state === "too_early") {
      return HttpResponse.json(
        { error: { code: "not_enough_progress", message: "Complete more lessons first." } },
        { status: 409 },
      );
    }
    if (config.summary.state !== "generating") {
      config.summary = { ...config.summary, state: "generating" };
      pollsLeft = config.pollsBeforeReady;
    }
    return HttpResponse.json(config.summary, { status: 202 });
  }),
];
