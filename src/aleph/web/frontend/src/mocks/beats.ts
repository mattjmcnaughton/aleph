// Contract-shaped fakes for the Beats & Briefs API (Phase 6 TDD §6-8,
// AL-530, docs/api.md ## Analyst). Mirrors `mocks/paths.ts`'s sentinel-topic
// + `pollsRemaining` shape for the trigger+poll research cycle — a Beat's
// own version of "generating", with the smaller state set §4/§6 actually
// ships: `idle | researching | failed | refused`, no `pending`.
//
// Convention: the topic string selects the eventual resolution via
// sentinels (`REFUSED_BEAT_TOPIC_SENTINEL`/`FAILED_BEAT_TOPIC_SENTINEL`), and
// `configureBeats({ pollsBeforeResolve })` tunes how many polls a Beat spends
// `researching` before it settles.
//
// **`POST /beats` and a real retry claim NEVER settle synchronously**
// (code-review FIX 4, correcting this file's original shape). The shipped
// route's own `entries=[]` is unconditional (`routers/v1/beats.py::
// deploy_beat` builds its response from `_beat_detail_dto(beat, entries=[])`
// — never a `BriefRepository` read) — because the pipeline is *spawned*,
// never awaited, so no response inside the same request can ever reflect
// work a background task has not run yet. Both handlers below force
// `pollsRemaining` to **at least 1** for exactly this reason: even at the
// `pollsBeforeResolve` default of `0`, the Beat this fake just claimed must
// still read `researching` with `entries: []` in its own `202` body, and
// settle only on a later, real `GET /beats/{id}` — the seed-then-poll
// handoff `beats-deploy.test.tsx`'s own "the detail poll actually runs"
// case exercises. Before this fix `settle()` ran inside the `POST`/`retry`
// handlers themselves whenever `pollsRemaining` was already `0` (the
// default), so a test suite that never overrode `pollsBeforeResolve` — every
// test in `beats-deploy.test.tsx` — got a terminal `202` body every time,
// and `routes/beats.new.tsx`'s `setQueryData` seed meant the Beat view's own
// `GET /beats/{id}` never fired at all behind it.

import { HttpResponse, http } from "msw";
import {
  API_V1_BASE,
  type BeatDetail,
  type BeatResearchState,
  type BeatSummary,
  type BriefEntry,
  type Level,
  type ReadPingMarker,
  type Source,
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

/**
 * Every `tz_offset_minutes` query param the fake has seen, per endpoint
 * (code-review FIX 1) — the `mocks/progress.ts::progressReceivedOffsets`
 * precedent, one array per Beats call site since a single deploy-then-poll
 * flow exercises three or four of them in one test and a merged list would
 * lose which request sent what.
 */
const deployReceivedOffsets: number[] = [];
const listReceivedOffsets: number[] = [];
const detailReceivedOffsets: number[] = [];
const retryReceivedOffsets: number[] = [];

// --- Briefs (AL-531): the reading surface + read-ping fakes ----------------
//
// A separate store from `StoredBeat` above — a Brief's own detail payload
// (`BriefDetailDTO`, TDD §6) is not derived from a Beat's rail entries here
// (unlike the real backend, which joins the two tables), so a test seeds
// whichever of the two it needs directly. `seedBrief` takes a `beatId` only
// so the read ping's invalidation target is a real value; nothing here
// requires the id to also exist in `store` above.

interface StoredBrief {
  id: string;
  beatId: string;
  number: number | null;
  publishedOn: string;
  title: string | null;
  bodyMarkdown: string | null;
  buildsOn: { id: string; number: number; publishedOn: string } | null;
  sources: Source[];
  readAt: string | null;
  sourcesSeenAt: string | null;
}

const briefStore = new Map<string, StoredBrief>();
/** Every `POST /briefs/{id}/read` the fake has received, in call order —
 *  the exactly-once proof (`briefReadPingsFor`) reads this. */
const briefReadPings: Array<{
  briefId: string;
  marker: ReadPingMarker;
  tzOffsetMinutes: number | null;
}> = [];

/**
 * Seed a Brief directly — no research cycle to drive it through (the
 * `seedBeat` precedent above, one entity over). Every field but `id` and
 * `beatId` defaults to a plausible published Brief so a test that only
 * cares about, say, the read ping does not have to spell out a title and a
 * body it never asserts on.
 */
export function seedBrief(brief: {
  id: string;
  beatId: string;
  number?: number;
  publishedOn?: string;
  title?: string;
  bodyMarkdown?: string;
  buildsOn?: { id: string; number: number; publishedOn: string } | null;
  sources?: Source[];
  readAt?: string | null;
  sourcesSeenAt?: string | null;
}): void {
  briefStore.set(brief.id, {
    id: brief.id,
    beatId: brief.beatId,
    number: brief.number ?? 1,
    publishedOn: brief.publishedOn ?? DEFAULT_TODAY,
    title: brief.title ?? `Brief #${brief.number ?? 1}`,
    bodyMarkdown: brief.bodyMarkdown ?? "The body of the Brief.",
    buildsOn: brief.buildsOn ?? null,
    sources: brief.sources ?? [],
    readAt: brief.readAt ?? null,
    sourcesSeenAt: brief.sourcesSeenAt ?? null,
  });
}

/**
 * Seed a Skipped entry's id — the API resolves it (`BriefDetailDTO` nulls
 * `number`/`title`/`body_markdown`), even though the rail never links to
 * one (hand-off item 2). Exercises the route's own graceful deep-link
 * degradation.
 */
export function seedSkippedBriefId(brief: {
  id: string;
  beatId: string;
  publishedOn?: string;
}): void {
  briefStore.set(brief.id, {
    id: brief.id,
    beatId: brief.beatId,
    number: null,
    publishedOn: brief.publishedOn ?? DEFAULT_TODAY,
    title: null,
    bodyMarkdown: null,
    buildsOn: null,
    sources: [],
    readAt: null,
    sourcesSeenAt: null,
  });
}

/** Every read ping received for one Brief, in call order — the test-facing
 *  proof that a ping fired exactly once (or not at all). */
export function briefReadPingsFor(
  briefId: string,
): Array<{ marker: ReadPingMarker; tzOffsetMinutes: number | null }> {
  return briefReadPings
    .filter((ping) => ping.briefId === briefId)
    .map(({ marker, tzOffsetMinutes }) => ({ marker, tzOffsetMinutes }));
}

/** Reset store + config between tests (wired into tests/setup.ts). */
export function resetBeats(): void {
  config = { ...defaultConfig };
  store.clear();
  idCounter = 0;
  createBodies.length = 0;
  listRequests = 0;
  detailPollRequests.clear();
  deployReceivedOffsets.length = 0;
  listReceivedOffsets.length = 0;
  detailReceivedOffsets.length = 0;
  retryReceivedOffsets.length = 0;
  briefStore.clear();
  briefReadPings.length = 0;
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

/** Every `tz_offset_minutes` `POST /beats` received, in call order
 *  (code-review FIX 1). */
export function beatDeployReceivedOffsets(): number[] {
  return [...deployReceivedOffsets];
}

/** Every `tz_offset_minutes` `GET /beats` received, in call order
 *  (code-review FIX 1). */
export function beatsListReceivedOffsets(): number[] {
  return [...listReceivedOffsets];
}

/** Every `tz_offset_minutes` `GET /beats/{id}` received, in call order
 *  (code-review FIX 1) — across every Beat id, since a test asserting this
 *  only ever polls one at a time. */
export function beatDetailReceivedOffsets(): number[] {
  return [...detailReceivedOffsets];
}

/** Every `tz_offset_minutes` `POST /beats/{id}/retry` received, in call
 *  order (code-review FIX 1). */
export function beatRetryReceivedOffsets(): number[] {
  return [...retryReceivedOffsets];
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

/** Read `tz_offset_minutes` off a request URL and record it (code-review
 *  FIX 1) — `undefined` when the caller omitted it (never reachable from the
 *  wrapped `lib/api.ts` call sites, which all send it, but distinct from a
 *  parse failure). */
function recordOffset(request: Request, sink: number[]): void {
  const raw = new URL(request.url).searchParams.get("tz_offset_minutes");
  if (raw !== null) sink.push(Number(raw));
}

export const beatHandlers = [
  http.post(`${API_V1_BASE}/beats`, async ({ request }) => {
    recordOffset(request, deployReceivedOffsets);
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
      // Forced to at least 1 (code-review FIX 4, see the module doc): the
      // real router's `deploy_beat` claims synchronously (so this response
      // always reads `researching`, never a stale pre-claim `idle`) but
      // spawns the pipeline rather than awaiting it — no `202` body can ever
      // carry a Brief the pipeline has not yet published, no matter how low
      // `pollsBeforeResolve` is dialed.
      pollsRemaining: Math.max(config.pollsBeforeResolve, 1),
      researchStartedAt: new Date().toISOString(),
      entries: [],
      nextNumber: 1,
    };
    store.set(id, beat);
    // Never settle here — see the module doc's FIX 4 note. `entries` stays
    // `[]` and `research_state` reads `researching` (via `pollsRemaining`
    // above), unconditionally, exactly like the real route.
    return HttpResponse.json(detailFor(beat), { status: 202 });
  }),

  // Registered before `/beats/:id` for readability; MSW matches the exact
  // path regardless, so the two never collide.
  http.get(`${API_V1_BASE}/beats`, ({ request }) => {
    listRequests += 1;
    recordOffset(request, listReceivedOffsets);
    for (const beat of store.values()) tick(beat);
    // Newest first (docs/api.md); the store is insertion-ordered.
    return HttpResponse.json({ beats: [...store.values()].reverse().map(summaryFor) });
  }),

  http.get(`${API_V1_BASE}/beats/:id`, ({ params, request }) => {
    const id = params.id as string;
    detailPollRequests.set(id, (detailPollRequests.get(id) ?? 0) + 1);
    recordOffset(request, detailReceivedOffsets);
    const beat = store.get(id);
    if (!beat) return notFoundEnvelope();
    tick(beat);
    return HttpResponse.json(detailFor(beat));
  }),

  http.post(`${API_V1_BASE}/beats/:id/retry`, ({ params, request }) => {
    recordOffset(request, retryReceivedOffsets);
    if (config.retryRateLimited) {
      return rateLimitEnvelope();
    }
    if (config.retryFails) {
      return serverErrorEnvelope();
    }
    const beat = store.get(params.id as string);
    if (!beat) return notFoundEnvelope();
    const researching = beat.pollsRemaining > 0;
    if (beat.resolution === "failed" && !researching) {
      // A retry re-claims the failed run; this time it succeeds (mirrors
      // `mocks/paths.ts`'s own retry handler) — but, exactly like `POST
      // /beats` above (code-review FIX 4, see the module doc), never
      // synchronously: the real route's `trigger_retry` now AWAITS only the
      // claim before building its response (code-review FIX 9 on
      // AL-530/AL-522) — the response already reflects `researching`, never
      // the pipeline's outcome, which is still spawned and settles only on
      // a later poll's own `tick()`. Before this fix the handler mutated
      // `resolution` *and* called `settle()` in the same turn whenever
      // `pollsBeforeResolve` was `0`, so this handler could never reproduce
      // the real route's pre-spawn response.
      beat.resolution = "idle";
      beat.pollsRemaining = Math.max(config.pollsBeforeResolve, 1);
      beat.researchStartedAt = new Date().toISOString();
    }
    return HttpResponse.json(detailFor(beat), { status: 202 });
  }),

  http.get(`${API_V1_BASE}/briefs/:id`, ({ params }) => {
    const brief = briefStore.get(params.id as string);
    if (!brief) return notFoundEnvelope();
    return HttpResponse.json({
      id: brief.id,
      beat_id: brief.beatId,
      number: brief.number,
      published_on: brief.publishedOn,
      title: brief.title,
      body_markdown: brief.bodyMarkdown,
      builds_on: brief.buildsOn
        ? {
            id: brief.buildsOn.id,
            number: brief.buildsOn.number,
            published_on: brief.buildsOn.publishedOn,
          }
        : null,
      sources: brief.sources,
    });
  }),

  http.post(`${API_V1_BASE}/briefs/:id/read`, async ({ params, request }) => {
    const brief = briefStore.get(params.id as string);
    if (!brief) return notFoundEnvelope();
    const body = (await request.json()) as { marker: ReadPingMarker };
    const raw = new URL(request.url).searchParams.get("tz_offset_minutes");
    briefReadPings.push({
      briefId: brief.id,
      marker: body.marker,
      tzOffsetMinutes: raw !== null ? Number(raw) : null,
    });
    // First-write-wins (D11) — a repeat ping with the same marker never
    // moves the timestamp, mirroring `mark_read`/`mark_sources_seen`.
    if (body.marker === "opened") {
      if (brief.readAt === null) {
        brief.readAt = new Date().toISOString();
        // A real backend's Brief and rail-entry `read_at` are the same
        // column on the same row; this fake keeps two stores (`seedBeat`'s
        // `entries` and `seedBrief`'s own record) for test-authoring
        // convenience, so mirror the write into the Beat's rail entry when
        // one happens to share this id — what makes the invalidation this
        // ping's caller triggers (`["beats", beatId]`) actually visible.
        const beat = store.get(brief.beatId);
        const entry = beat?.entries.find((e) => e.id === brief.id && e.kind === "published");
        if (entry) entry.readAt = brief.readAt;
      }
    } else if (brief.sourcesSeenAt === null) {
      brief.sourcesSeenAt = new Date().toISOString();
    }
    return new HttpResponse(null, { status: 204 });
  }),
];
