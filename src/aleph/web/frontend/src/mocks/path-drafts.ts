// Contract-shaped fakes for the Path drafts API (`POST /path-drafts`,
// `POST /path-drafts/questions`, docs/api.md). Same shape as the other fakes:
// `configurePathDrafts({...})` dials in a failure or a held request, and
// `resetPathDrafts()` restores the defaults between tests and stories.
//
// Deterministic: a first draft is always EXAMPLE_FIRST_DRAFT and any redraft
// (answers sent) is EXAMPLE_SHARPENED_DRAFT, the approved mockup's Postgres
// example, so stories and tests read the same content the design was judged on.

import { HttpResponse, delay, http } from "msw";
import {
  API_V1_BASE,
  type CreateDraftQuestionsInput,
  type CreatePathDraftInput,
  type InterviewQuestion,
  type PathDraft,
} from "../lib/api";

export const EXAMPLE_IDEA =
  "I want to actually understand how Postgres works under the hood so I can figure out why our queries get slow at work.";

export const EXAMPLE_FIRST_DRAFT: PathDraft = {
  topic: "PostgreSQL internals for debugging slow queries",
  level: "some_experience",
  guidance: [
    "Start with how a query is parsed, planned and executed.",
    "Then storage: pages, the heap, TOAST, and what a sequential scan really reads.",
    "Indexes (B-tree, GIN, BRIN) and when the planner chooses them.",
    "MVCC, vacuum and bloat, then locking and lock waits.",
    "Finish by reading EXPLAIN ANALYZE on realistic slow queries.",
    "Skip installation and basic SQL syntax.",
  ].join("\n"),
  teaching_style:
    "Ground each lesson in a query you might meet at work, and show its EXPLAIN output.",
};

export const EXAMPLE_SHARPENED_DRAFT: PathDraft = {
  topic: "PostgreSQL internals for debugging slow production queries",
  level: "some_experience",
  guidance: [
    "You can run EXPLAIN but not read it fluently: teach reading plans in the first unit and return to them in every unit after.",
    "Start with how a query is parsed, planned and executed.",
    "Then storage: pages, the heap, TOAST, and what a sequential scan really reads.",
    "Indexes (B-tree, GIN, BRIN) and when the planner chooses them.",
    "MVCC, vacuum and bloat, then locking and lock waits.",
    "Finish with a unit of slow-query case studies from a busy OLTP app.",
    "Keep it to about 15 lessons. Leave out replication, high availability and extensions.",
  ].join("\n"),
  teaching_style:
    "Ground each lesson in a query you might meet at work, and show its EXPLAIN output.",
};

export const EXAMPLE_QUESTIONS: InterviewQuestion[] = [
  {
    prompt: "What will you mostly use this for?",
    why: "Decides whether the path ends in case studies or in design.",
    options: [
      "Diagnosing slow queries in production",
      "Designing schemas and indexes up front",
      "Understanding the internals for their own sake",
    ],
    multi: false,
  },
  {
    prompt: "How comfortable are you with EXPLAIN today?",
    why: "Sets where reading query plans starts.",
    options: ["I've never used it", "I run it but can't read it well", "I read plans fluently"],
    multi: false,
  },
  {
    prompt: "How big should the path be?",
    why: "Shapes how many lessons the outline gets.",
    options: ["Short, about 8 lessons", "Medium, about 15", "Thorough, 25 or more"],
    multi: false,
  },
  {
    prompt: "Anything to leave out?",
    why: "Pick any, or none.",
    options: ["Replication and high availability", "Extensions", "Partitioning"],
    multi: true,
  },
];

interface PathDraftsConfig {
  /** How many of the next `POST /path-drafts` calls answer `502 draft_failed`. */
  draftFailures: number;
  /** How many of the next `POST /path-drafts/questions` calls fail likewise. */
  questionFailures: number;
  /** When true, draft calls never answer (the drafting/redrafting spinner). */
  holdDrafts: boolean;
}

const defaults = (): PathDraftsConfig => ({
  draftFailures: 0,
  questionFailures: 0,
  holdDrafts: false,
});

let config = defaults();
let draftBodies: CreatePathDraftInput[] = [];
let questionBodies: CreateDraftQuestionsInput[] = [];

export function resetPathDrafts(): void {
  config = defaults();
  draftBodies = [];
  questionBodies = [];
}

export function configurePathDrafts(overrides: Partial<PathDraftsConfig>): void {
  config = { ...config, ...overrides };
}

/** Every `POST /path-drafts` body received, oldest first (for assertions). */
export function pathDraftBodies(): CreatePathDraftInput[] {
  return draftBodies;
}

/** Every `POST /path-drafts/questions` body received, oldest first. */
export function draftQuestionBodies(): CreateDraftQuestionsInput[] {
  return questionBodies;
}

function draftFailed() {
  return HttpResponse.json(
    {
      error: {
        code: "draft_failed",
        message: "Aleph couldn't write a draft just now. Try again.",
        request_id: "test-request-id",
      },
    },
    { status: 502 },
  );
}

export const pathDraftHandlers = [
  http.post(`${API_V1_BASE}/path-drafts`, async ({ request }) => {
    const body = (await request.json()) as CreatePathDraftInput;
    draftBodies.push(body);
    if (config.holdDrafts) await delay("infinite");
    if (config.draftFailures > 0) {
      config.draftFailures -= 1;
      return draftFailed();
    }
    const sharpened = (body.answers?.length ?? 0) > 0;
    return HttpResponse.json(sharpened ? EXAMPLE_SHARPENED_DRAFT : EXAMPLE_FIRST_DRAFT);
  }),
  http.post(`${API_V1_BASE}/path-drafts/questions`, async ({ request }) => {
    const body = (await request.json()) as CreateDraftQuestionsInput;
    questionBodies.push(body);
    if (config.questionFailures > 0) {
      config.questionFailures -= 1;
      return draftFailed();
    }
    const asked = new Set((body.answers ?? []).map((answer) => answer.question));
    const fresh = EXAMPLE_QUESTIONS.filter((question) => !asked.has(question.prompt));
    const questions =
      fresh.length >= 2
        ? fresh
        : [
            {
              prompt: `Anything else to change? (round ${asked.size + 1})`,
              why: "Anything the draft still misses.",
              options: ["Nothing more", "Make it shorter"],
              multi: false,
            },
            {
              prompt: `How should lessons open? (round ${asked.size + 1})`,
              why: "Sets the teaching style.",
              options: ["With an example", "With the rule"],
              multi: false,
            },
          ];
    return HttpResponse.json({ questions });
  }),
];
