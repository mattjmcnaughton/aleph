// Contract-shaped fakes for the Beats & Briefs API (Phase 6 TDD §6-8,
// AL-530, docs/api.md ## Analyst). Mirrors `mocks/paths.ts`'s sentinel-topic
// + `pollsRemaining` shape for the trigger+poll research cycle — a Beat's
// own version of "generating", with the smaller state set §4/§6 actually
// ships: `idle | researching | failed | refused`, no `pending`.
//
// Convention: the topic string selects the eventual resolution via
// sentinels (`REFUSED_BEAT_TOPIC_SENTINEL`/`FAILED_BEAT_TOPIC_SENTINEL`), and
// `configureBeats({ pollsBeforeResolve })` tunes how many polls a Beat spends
// `researching` before it settles. The default (0) settles on the very
// first response — mirroring the shipped router's drain-then-read ordering
// (`routers/v1/beats.py`'s own module doc): a fresh Beat's first claim is
// already reflected in the `202` body, never a stale pre-claim `idle` a
// first poll would wrongly treat as terminal.

import { HttpResponse, http } from "msw";
import {
  API_V1_BASE,
  type BeatDetail,
  type BeatResearchState,
  type BeatSummary,
  type BriefEntry,
  type Level,
} from "../lib/api";

/** Topic contains this (case-insensitive) → the research run refuses. */
export const REFUSED_BEAT_TOPIC_SENTINEL = "refuse-me";
/** Topic contains this → the research run fails, retryable. */
export const FAILED_BEAT_TOPIC_SENTINEL = "fail-me";

const REFUSAL_MESSAGE =
  "Aleph can't research this topic. It sits outside what we can safely report on.";

const DEFAULT_TODAY = "2026-08-10";

type SettledState = "idle" | "failed" | "refused";

function resolutionForTopic(topic: string): SettledState {
  const t = topic.toLowerCase();
  if (t.includes(REFUSED_BEAT_TOPIC_SENTINEL)) return "refused";
  if (t.includes(FAILED_BEAT_TOPIC_SENTINEL)) return "failed";
  return "idle";
}

interface StoredEntry {
  id: string;
  kind: "published" | "skipped";
  number: number | null;
  publishedOn: string;
  title?: string;
  readAt?: string | null;
  skipLine?: string;
}

interface StoredBeat {
  id: string;
  topic: string;
  level: Level;
  anchorWeekday: number;
  guidance: string | null;
  /** The state a settled (non-researching) Beat reads — never `researching`
   *  itself, which is derived from `pollsRemaining` below. */
  resolution: SettledState;
  /** While > 0, `research_state` reads `researching`; each poll (list or
   *  detail) decrements it (mirrors `mocks/paths.ts`'s identically-named
   *  field). */
  pollsRemaining: number;
  researchStartedAt: string;
  /** Newest first, matching the real rail's own order. */
  entries: StoredEntry[];
  nextNumber: number;
}

interface BeatsConfig {
  /** Polls a fresh (or retried) Beat spends `researching` before settling. */
  pollsBeforeResolve: number;
  /** When true, `POST /beats` raises the stock Beat-cap `429` envelope. */
  rateLimited: boolean;
  /** When true, `POST /beats/{id}/retry` raises the daily research-cap `429`. */
  retryRateLimited: boolean;
  /** When true, `POST /beats/{id}/retry` raises a generic `500`. */
  retryFails: boolean;
  /** What a *successful* (`idle`-resolving) settle publishes: a new Brief,
   *  a Skipped entry, or nothing at all — a Beat whose rail was hand-seeded
   *  via `seedBeat` and should not grow a further row just because a test
   *  polled it. */
  onSuccess: "published" | "skipped" | "none";
}

const defaultConfig: BeatsConfig = {
  pollsBeforeResolve: 0,
  rateLimited: false,
  retryRateLimited: false,
  retryFails: false,
  onSuccess: "published",
};

let config: BeatsConfig = { ...defaultConfig };
const store = new Map<string, StoredBeat>();
let idCounter = 0;
/** Every body `POST /beats` received, in call order (mirrors
 *  `mocks/paths.ts`'s `createBodies`). */
const createBodies: Array<Record<string, unknown>> = [];
/** How many times `GET /beats` was served — lets a test see the list poll
 *  (never) or stop (always) firing. */
let listRequests = 0;
/** `GET /beats/{id}` requests served, per Beat id — the stalled-poll
 *  regression shape `flashcardDraftsPollRequestCount` already proves: a
 *  terminal state must hold this steady no matter how far fake time runs
 *  past it. */
const detailPollRequests = new Map<string, number>();

/** Reset store + config between tests (wired into tests/setup.ts). */
export function resetBeats(): void {
  config = { ...defaultConfig };
  store.clear();
  idCounter = 0;
  createBodies.length = 0;
  listRequests = 0;
  detailPollRequests.clear();
}

/** Tune the fake's polling/rate-limit/settle behaviour for a single test. */
export function configureBeats(overrides: Partial<BeatsConfig>): void {
  config = { ...config, ...overrides };
}

/** Every raw body `POST /beats` was called with, in order — including ones
 *  the fake then rejected (the 429 case), so a test can assert what the
 *  form *sent* even when the server refused it. */
export function createBeatBodies(): Array<Record<string, unknown>> {
  return createBodies.map((body) => ({ ...body }));
}

/** How many `GET /beats` the fake has served — proves the list is never
 *  polled (TDD §7) when a test asserts this stays at 1 across fake time. */
export function beatsListRequestCount(): number {
  return listRequests;
}

/** How many times the detail poll has actually hit the network for one Beat
 *  — the regression counter: a terminal state must hold this steady no
 *  matter how far fake time is advanced past it. */
export function beatDetailPollRequestCount(id: string): number {
  return detailPollRequests.get(id) ?? 0;
}

/** Directly seed a stored Beat — a rail to open without driving a research
 *  cycle through it. */
export function seedBeat(beat: {
  id: string;
  topic: string;
  level: Level;
  anchorWeekday?: number;
  guidance?: string | null;
  resolution?: SettledState;
  pollsRemaining?: number;
  entries?: Array<
    | {
        kind: "published";
        id: string;
        number: number;
        publishedOn: string;
        title: string;
        readAt?: string | null;
      }
    | { kind: "skipped"; id: string; publishedOn: string; skipLine: string }
  >;
}): void {
  const entries: StoredEntry[] = (beat.entries ?? []).map((entry) =>
    entry.kind === "published"
      ? {
          id: entry.id,
          kind: "published",
          number: entry.number,
          publishedOn: entry.publishedOn,
          title: entry.title,
          readAt: entry.readAt ?? null,
        }
      : {
          id: entry.id,
          kind: "skipped",
          number: null,
          publishedOn: entry.publishedOn,
          skipLine: entry.skipLine,
        },
  );
  const highestNumber = Math.max(0, ...entries.map((entry) => entry.number ?? 0));
  store.set(beat.id, {
    id: beat.id,
    topic: beat.topic,
    level: beat.level,
    anchorWeekday: beat.anchorWeekday ?? 0,
    guidance: beat.guidance ?? null,
    resolution: beat.resolution ?? "idle",
    pollsRemaining: beat.pollsRemaining ?? 0,
    researchStartedAt: new Date().toISOString(),
    entries,
    nextNumber: highestNumber + 1,
  });
}

function toEntryDTO(entry: StoredEntry): BriefEntry {
  if (entry.kind === "published") {
    return {
      kind: "published",
      id: entry.id,
      number: entry.number as number,
      published_on: entry.publishedOn,
      title: entry.title as string,
      read_at: entry.readAt ?? null,
    };
  }
  return {
    kind: "skipped",
    id: entry.id,
    number: null,
    published_on: entry.publishedOn,
    skip_line: entry.skipLine as string,
  };
}

/**
 * Settle a Beat whose `pollsRemaining` just hit zero: a successful
 * (`idle`-resolving) run publishes one new rail entry — mirroring the real
 * research pipeline's own live outcomes (`brief_research_completed`'s
 * `outcome`: `published`/`skipped`/`failed`/`refused`). `failed`/`refused`
 * add nothing; that is the whole difference between a run that produced
 * content and one that didn't.
 */
function settle(beat: StoredBeat): void {
  if (beat.resolution !== "idle" || config.onSuccess === "none") return;
  const id = `e0000000-0000-4000-8000-${String(beat.entries.length + 1).padStart(12, "0")}`;
  if (config.onSuccess === "skipped") {
    beat.entries.unshift({
      id,
      kind: "skipped",
      number: null,
      publishedOn: DEFAULT_TODAY,
      skipLine: "Nothing material since the last Brief.",
    });
    return;
  }
  beat.entries.unshift({
    id,
    kind: "published",
    number: beat.nextNumber,
    publishedOn: DEFAULT_TODAY,
    title: `What changed in ${beat.topic}`,
    readAt: null,
  });
  beat.nextNumber += 1;
}

function detailFor(beat: StoredBeat): BeatDetail {
  const researching = beat.pollsRemaining > 0;
  const state: BeatResearchState = researching ? "researching" : beat.resolution;
  return {
    id: beat.id,
    topic: beat.topic,
    level: beat.level,
    guidance: beat.guidance,
    anchor_weekday: beat.anchorWeekday,
    cadence: "weekly",
    research_state: state,
    research_started_at: beat.researchStartedAt,
    refusal_message: state === "refused" ? REFUSAL_MESSAGE : null,
    entries: beat.entries.map(toEntryDTO),
  };
}

function summaryFor(beat: StoredBeat): BeatSummary {
  const detail = detailFor(beat);
  const unread = beat.entries.filter(
    (entry) => entry.kind === "published" && entry.readAt == null,
  ).length;
  return {
    id: detail.id,
    topic: detail.topic,
    level: detail.level,
    anchor_weekday: detail.anchor_weekday,
    cadence: detail.cadence,
    research_state: detail.research_state,
    research_started_at: detail.research_started_at,
    refusal_message: detail.refusal_message,
    unread_count: unread,
  };
}

/** Advance one Beat's research clock by one poll (list or detail): mirrors
 *  the real drain running on every `GET` (TDD D15). */
function tick(beat: StoredBeat): void {
  if (beat.pollsRemaining <= 0) return;
  beat.pollsRemaining -= 1;
  if (beat.pollsRemaining === 0) settle(beat);
}

function rateLimitEnvelope() {
  return HttpResponse.json(
    {
      error: {
        code: "rate_limited",
        message: "You've reached the limit for Beats.",
        request_id: "test-request-id",
      },
    },
    { status: 429 },
  );
}

function serverErrorEnvelope() {
  return HttpResponse.json(
    {
      error: {
        code: "internal_error",
        message: "Something went wrong.",
        request_id: "test-request-id",
      },
    },
    { status: 500 },
  );
}

function notFoundEnvelope() {
  return HttpResponse.json(
    { error: { code: "not_found", message: "Beat not found." } },
    { status: 404 },
  );
}

export const beatHandlers = [
  http.post(`${API_V1_BASE}/beats`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown> & {
      topic: string;
      level: Level;
      anchor_weekday: number;
      guidance?: string;
    };
    createBodies.push({ ...body });
    if (config.rateLimited) {
      return rateLimitEnvelope();
    }
    idCounter += 1;
    const id = `b0000000-0000-4000-8000-${String(idCounter).padStart(12, "0")}`;
    const beat: StoredBeat = {
      id,
      topic: body.topic,
      level: body.level,
      anchorWeekday: body.anchor_weekday,
      guidance: body.guidance ?? null,
      resolution: resolutionForTopic(body.topic),
      pollsRemaining: config.pollsBeforeResolve,
      researchStartedAt: new Date().toISOString(),
      entries: [],
      nextNumber: 1,
    };
    store.set(id, beat);
    // The real router drains-then-reads inside the SAME request (D15) — a
    // fresh Beat's cadence is unconditionally claimable, so the `202` body
    // already reflects the claim rather than a stale pre-claim `idle`.
    if (beat.pollsRemaining === 0) settle(beat);
    return HttpResponse.json(detailFor(beat), { status: 202 });
  }),

  // Registered before `/beats/:id` for readability; MSW matches the exact
  // path regardless, so the two never collide.
  http.get(`${API_V1_BASE}/beats`, () => {
    listRequests += 1;
    for (const beat of store.values()) tick(beat);
    // Newest first (docs/api.md); the store is insertion-ordered.
    return HttpResponse.json({ beats: [...store.values()].reverse().map(summaryFor) });
  }),

  http.get(`${API_V1_BASE}/beats/:id`, ({ params }) => {
    const id = params.id as string;
    detailPollRequests.set(id, (detailPollRequests.get(id) ?? 0) + 1);
    const beat = store.get(id);
    if (!beat) return notFoundEnvelope();
    tick(beat);
    return HttpResponse.json(detailFor(beat));
  }),

  http.post(`${API_V1_BASE}/beats/:id/retry`, ({ params }) => {
    if (config.retryRateLimited) {
      return rateLimitEnvelope();
    }
    if (config.retryFails) {
      return serverErrorEnvelope();
    }
    const beat = store.get(params.id as string);
    if (beat && beat.resolution === "failed") {
      // A retry re-claims the failed run; this time it succeeds (mirrors
      // `mocks/paths.ts`'s own retry handler).
      beat.resolution = "idle";
      beat.pollsRemaining = config.pollsBeforeResolve;
      beat.researchStartedAt = new Date().toISOString();
      if (beat.pollsRemaining === 0) settle(beat);
    }
    if (!beat) return notFoundEnvelope();
    return HttpResponse.json(detailFor(beat), { status: 202 });
  }),
];
