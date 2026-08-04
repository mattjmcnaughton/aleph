// Contract-shaped fakes for the Flashcards API (Phase 3 TDD §6-8, docs/api.md
// ## Flashcards). Mirrors `mocks/progress.ts`'s "no store, dial in the payload
// a test needs" shape for the summary/queue reads — the real derivation is
// D3's job, not this fake's — plus a small `draftRuns` store (mirrors
// `mocks/lessons.ts`) for the trigger+poll drafting surface, since that one
// genuinely has states to move through.
//
// `POST /reviews` nudges the dialed-in `queue`/`summary` forward on a grade —
// not a scheduler, just enough so a test's authoritative refetch (the
// `invalidateQueries` every grade triggers) sees a world consistent with the
// grade it just posted, without every test having to reconfigure by hand.
//
// AL-410's card list is the one Flashcards surface that genuinely needs a real
// in-memory store rather than a dialed-in payload: `GET /flashcards` filters,
// searches and paginates it, `PATCH`/`DELETE` mutate one row of it, and a test
// needs the *result* of that mutation to come back out the next `GET` — the
// same reason `draftRuns` (above) is a `Map`, not a config field, extended
// here to `cardsStore` (below).

import { HttpResponse, http } from "msw";
import {
  API_V1_BASE,
  type CardListItem,
  type FlashcardCitation,
  type FlashcardDraftRunState,
  type FlashcardGrade,
  type ReviewQueue,
  type ReviewSummary,
} from "../lib/api";

const DEFAULT_TODAY = "2026-08-04";

/** The zero-state summary: nothing due, no path breakdown. */
const DEFAULT_SUMMARY: ReviewSummary = {
  today: DEFAULT_TODAY,
  due_count: 0,
  estimated_minutes: 0,
  paths: [],
};

/** The zero-state queue: an already-finished (or never-started) day. */
const DEFAULT_QUEUE: ReviewQueue = {
  today: DEFAULT_TODAY,
  total: 0,
  completed: 0,
  scope_path_id: null,
  other_due_count: 0,
  cards: [],
};

interface DraftRun {
  state: FlashcardDraftRunState;
  cards: { id: string; front: string; back: string }[];
}

interface FlashcardsConfig {
  summary: ReviewSummary;
  queue: ReviewQueue;
  /** When true, `POST .../flashcard-drafts/keep` raises a generic `500`. */
  keepFails: boolean;
  /**
   * When set, `POST .../flashcard-drafts` (trigger) fails instead of claiming
   * a run — TDD §5.6's two frontend-owned rows (ticket 3, reasons updated for
   * AL-400): `"rate_limited"` → `429` (over `FLASHCARD_DRAFTS_PER_DAY`),
   * `"not_generated"` → `409 lesson_not_generated` (the guard is load-bearing
   * now that the trigger fires on lesson open rather than completion — an
   * ungenerated lesson is a real, reachable case). The run stays absent
   * either way — the poll never leaves `not_started` — which is exactly what
   * made both cases silent before ticket 3's fix.
   */
  triggerDraftsError: "rate_limited" | "not_generated" | null;
}

const defaultConfig: FlashcardsConfig = {
  summary: DEFAULT_SUMMARY,
  queue: DEFAULT_QUEUE,
  keepFails: false,
  triggerDraftsError: null,
};

let config: FlashcardsConfig = { ...defaultConfig };

/** lesson id -> its drafting run. Absent = never triggered — a real `200
 *  {state: "not_started", cards: []}` (D7's sparse table). */
const draftRuns = new Map<string, DraftRun>();

/** Every `POST /reviews` body the fake received, in order. */
let gradeRequests: { card_id: string; grade: FlashcardGrade; rung_before: number }[] = [];
/** Every `POST .../flashcard-drafts/keep` request, in order. */
let keepRequests: {
  lesson_id: string;
  kept_ids: string[];
  // Recorded because the server *requires* it — a keep writes `due_on = today +
  // ladder[0]`, so a body without an offset is a 422 the fake would otherwise
  // absorb silently (a real bug this fake once hid).
  tz_offset_minutes: number | undefined;
}[] = [];
/** Every `POST .../flashcard-drafts` (trigger) request, in order. */
let triggerRequests: string[] = [];
/** How many `GET /reviews/summary` the fake has served — 0 proves a flag-off
 *  or gated-off surface never fetched at all (`skipToken`). */
let summaryRequests = 0;
/** How many `GET /reviews/queue` the fake has served — same proof as
 *  `summaryRequests`, for the queue's own `skipToken` gate (`routes/review.tsx`). */
let queueRequests = 0;
/** `GET .../flashcard-drafts` requests served, per lesson id — the BLOCKER
 *  fix's own pin (finding 1): a poll terminal at `not_started` serves exactly
 *  one no matter how much fake time elapses; the old bug grew this without
 *  bound, every 5s, forever. */
const draftsPollRequests = new Map<string, number>();

// --- AL-410: the card list (`/cards`) ----------------------------------------
//
// `CardListItem` plus the one field the wire shape never carries — `pathId`,
// kept here only so the fake can filter by `path_id` the way the real query
// does (`source_path_id == path_id`, AL-410 plan §2). Never serialized: the
// citation names a path only through its (already-copied) `path_title`, so
// leaking a bare `pathId` onto the wire payload would be a shape the real API
// never sends.
interface CardRecord {
  id: string;
  front: string;
  back: string;
  rung: number;
  due_on: string;
  edited_at: string | null;
  source: FlashcardCitation;
  pathId: string | null;
  /** ISO datetime — the real ordering/cursor key (`kept_at DESC, id DESC`,
   *  AL-410 plan §2). Distinct from `due_on`, which is a bare date and has
   *  nothing to do with keep order. */
  keptAt: string;
  /** Mirrors `deleted_at IS NOT NULL` (AL-410 plan §1) — filtered out of
   *  every read below, never spliced out of the array, so a re-`DELETE`
   *  after the first still has a row to find and correctly re-404. */
  deleted: boolean;
}

let cardsStore: CardRecord[] = [];
/** When true, `PATCH /flashcards/:id` raises a generic `500` (mirrors
 *  `keepFails` above) — a test proving Save surfaces a retry notice. */
let updateCardFails = false;
let cardsListRequests = 0;
let cardUpdateRequests: { card_id: string; front: string; back: string }[] = [];
let cardDeleteRequests: string[] = [];

function toCardListItem(record: CardRecord): CardListItem {
  return {
    id: record.id,
    front: record.front,
    back: record.back,
    rung: record.rung,
    due_on: record.due_on,
    edited_at: record.edited_at,
    source: record.source,
  };
}

/** Active (non-deleted) cards, newest-kept-first — `kept_at DESC, id DESC`,
 *  the real query's own order (AL-410 plan §2). */
function sortedActiveCards(): CardRecord[] {
  return cardsStore
    .filter((record) => !record.deleted)
    .sort((a, b) => {
      if (a.keptAt !== b.keptAt) return a.keptAt < b.keptAt ? 1 : -1;
      return a.id < b.id ? 1 : -1;
    });
}

function matchesCardFilters(record: CardRecord, pathId: string | null, q: string | null): boolean {
  if (pathId !== null && record.pathId !== pathId) return false;
  if (q !== null && q !== "") {
    const needle = q.toLowerCase();
    if (
      !record.front.toLowerCase().includes(needle) &&
      !record.back.toLowerCase().includes(needle)
    ) {
      return false;
    }
  }
  return true;
}

/** Opaque `"{kept_at}|{id}"` cursor (AL-410 plan §2) — built and parsed only
 *  here, exactly as disposable to a caller as the real one is. */
function buildCardsCursor(record: CardRecord): string {
  return `${record.keptAt}|${record.id}`;
}

function parseCardsCursor(cursor: string): { keptAt: string; id: string } | null {
  const separator = cursor.indexOf("|");
  if (separator === -1) return null;
  return { keptAt: cursor.slice(0, separator), id: cursor.slice(separator + 1) };
}

/** Reset store + config between tests (wired into tests/setup.ts). */
export function resetFlashcards(): void {
  config = { ...defaultConfig };
  draftRuns.clear();
  gradeRequests = [];
  keepRequests = [];
  triggerRequests = [];
  summaryRequests = 0;
  queueRequests = 0;
  draftsPollRequests.clear();
  cardsStore = [];
  updateCardFails = false;
  cardsListRequests = 0;
  cardUpdateRequests = [];
  cardDeleteRequests = [];
}

/** Dial in the summary/queue a test needs, or force a write to fail. */
export function configureFlashcards(overrides: Partial<FlashcardsConfig>): void {
  config = { ...config, ...overrides };
}

/**
 * Pre-populate a lesson's drafting run (a returning learner resuming a
 * completed lesson, or `app/flashcards-drafts.test.tsx`'s own fixture setup).
 *
 * Not "W24's fixture": W24 is a Playwright e2e journey that drives the real
 * stub backend end to end and never touches MSW at all.
 */
export function seedFlashcardDraftRun(lessonId: string, run: DraftRun): void {
  draftRuns.set(lessonId, run);
}

/**
 * Directly seed a kept card into the `/cards` store (AL-410's `seedPath`
 * analogue). `pathId` is fake-only filtering plumbing (see `CardRecord`
 * above), never part of the wire shape a test asserts against.
 */
export function seedCard(card: {
  id: string;
  front: string;
  back: string;
  due_on: string;
  source: FlashcardCitation;
  rung?: number;
  edited_at?: string | null;
  pathId?: string | null;
  keptAt?: string;
}): void {
  cardsStore.push({
    id: card.id,
    front: card.front,
    back: card.back,
    rung: card.rung ?? 0,
    due_on: card.due_on,
    edited_at: card.edited_at ?? null,
    source: card.source,
    pathId: card.pathId ?? null,
    keptAt: card.keptAt ?? new Date().toISOString(),
    deleted: false,
  });
}

/** Force `PATCH /flashcards/:id` to fail with a generic `500`. */
export function configureCardUpdateFailure(fails: boolean): void {
  updateCardFails = fails;
}

export function cardsListRequestCount(): number {
  return cardsListRequests;
}

export function cardUpdateRequestBodies(): typeof cardUpdateRequests {
  return [...cardUpdateRequests];
}

export function cardDeleteRequestIds(): string[] {
  return [...cardDeleteRequests];
}

export function flashcardGradeRequests(): typeof gradeRequests {
  return [...gradeRequests];
}

export function flashcardKeepRequests(): typeof keepRequests {
  return [...keepRequests];
}

export function flashcardTriggerRequests(): string[] {
  return [...triggerRequests];
}

export function reviewSummaryRequestCount(): number {
  return summaryRequests;
}

export function reviewQueueRequestCount(): number {
  return queueRequests;
}

/** How many times the drafts poll has actually hit the network for one lesson
 *  — the BLOCKER regression test's counter (finding 1): a terminal
 *  `not_started` must hold this at whatever it was after the initial fetch,
 *  no matter how far fake time is advanced past it. */
export function flashcardDraftsPollRequestCount(lessonId: string): number {
  return draftsPollRequests.get(lessonId) ?? 0;
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

export const flashcardHandlers = [
  http.get(`${API_V1_BASE}/reviews/summary`, () => {
    summaryRequests += 1;
    return HttpResponse.json(config.summary);
  }),

  http.get(`${API_V1_BASE}/reviews/queue`, () => {
    queueRequests += 1;
    return HttpResponse.json(config.queue);
  }),

  http.post(`${API_V1_BASE}/reviews`, async ({ request }) => {
    const body = (await request.json()) as {
      card_id: string;
      grade: FlashcardGrade;
      rung_before: number;
      tz_offset_minutes: number;
    };
    // Recorded without `tz_offset_minutes` — its value depends on the test
    // runner's own local timezone, which every other request already carries
    // in its query string rather than a body a test would assert on exactly.
    gradeRequests.push({ card_id: body.card_id, grade: body.grade, rung_before: body.rung_before });

    const card = config.queue.cards.find((c) => c.card_id === body.card_id);
    const nextRung =
      body.grade === "got_it" ? (card?.rung ?? 0) + 1 : Math.max(0, (card?.rung ?? 0) - 1);

    // Move the fake's dialed-in queue/summary forward the same way the real
    // derivation would (§5.3): `got_it` satisfies the card — removed from the
    // unsatisfied remainder, `completed` up, `due_count` down; `again` demotes
    // it and sends it to the back of serve order rather than removing it (D8 —
    // it is still due, later the same session).
    if (card) {
      const rest = config.queue.cards.filter((c) => c.card_id !== body.card_id);
      if (body.grade === "got_it") {
        config = {
          ...config,
          queue: { ...config.queue, cards: rest, completed: config.queue.completed + 1 },
          summary: { ...config.summary, due_count: Math.max(0, config.summary.due_count - 1) },
        };
      } else {
        config = {
          ...config,
          queue: { ...config.queue, cards: [...rest, { ...card, rung: nextRung }] },
        };
      }
    }

    return HttpResponse.json({
      card_id: body.card_id,
      rung: nextRung,
      due_on: config.summary.today,
    });
  }),

  http.post(`${API_V1_BASE}/lessons/:lessonId/flashcard-drafts`, ({ params }) => {
    const lessonId = params.lessonId as string;
    triggerRequests.push(lessonId);
    // TDD §5.6's two frontend-owned failure rows (ticket 3): neither claims a
    // run, so the poll stays at `not_started` either way — this is the exact
    // shape a capped or not-yet-generated trigger produces on the wire.
    if (config.triggerDraftsError === "rate_limited") {
      return HttpResponse.json(
        { error: { code: "rate_limited", message: "Daily drafting limit reached." } },
        { status: 429 },
      );
    }
    if (config.triggerDraftsError === "not_generated") {
      return HttpResponse.json(
        {
          error: {
            code: "conflict",
            message: "This lesson has no content yet.",
            details: { reason: "lesson_not_generated" },
          },
        },
        { status: 409 },
      );
    }
    // Idempotent claim (D7): an absent or `failed` run (re)starts `generating`
    // — a test that wants specific cards seeds a `generated` run with
    // `seedFlashcardDraftRun` (before *or* after triggering; only an absent-or-
    // failed run is ever touched here). An already-generating/generated run is
    // untouched — a no-op `202`.
    const existing = draftRuns.get(lessonId);
    if (!existing || existing.state === "failed") {
      draftRuns.set(lessonId, { state: "generating", cards: [] });
    }
    return HttpResponse.json({ id: lessonId }, { status: 202 });
  }),

  http.get(`${API_V1_BASE}/lessons/:lessonId/flashcard-drafts`, ({ params }) => {
    const lessonId = params.lessonId as string;
    draftsPollRequests.set(lessonId, (draftsPollRequests.get(lessonId) ?? 0) + 1);
    const run = draftRuns.get(lessonId);
    // No row at all — real `200 {state: "not_started", cards: []}` (D7's
    // sparse table: a row exists only once drafting was actually triggered).
    // This used to be a `404`, the one state the fake never produced — which
    // is exactly how the BLOCKER bug (finding 1) survived a full test suite.
    if (!run) {
      return HttpResponse.json({ state: "not_started", cards: [] });
    }
    return HttpResponse.json({ state: run.state, cards: run.cards });
  }),

  http.post(
    `${API_V1_BASE}/lessons/:lessonId/flashcard-drafts/keep`,
    async ({ params, request }) => {
      const lessonId = params.lessonId as string;
      const body = (await request.json()) as {
        kept_ids: string[];
        tz_offset_minutes?: number;
      };
      keepRequests.push({
        lesson_id: lessonId,
        kept_ids: body.kept_ids,
        tz_offset_minutes: body.tz_offset_minutes,
      });
      if (config.keepFails) {
        return serverErrorEnvelope();
      }
      // Every draft for this lesson is gone after a keep (D6) — kept ones moved
      // into the schedule, discarded ones deleted outright. Never soft-deleted.
      const run = draftRuns.get(lessonId);
      if (run) {
        draftRuns.set(lessonId, { ...run, cards: [] });
      }
      // The real route answers `200 {kept_ids}` (docs/api.md), not `204` — the
      // fake used to disagree, benign only because the client typed the call
      // `Promise<void>` and threw the body away either way.
      return HttpResponse.json({ kept_ids: body.kept_ids });
    },
  ),

  // --- AL-410: `/cards` -------------------------------------------------------

  http.get(`${API_V1_BASE}/flashcards`, ({ request }) => {
    cardsListRequests += 1;
    const url = new URL(request.url);
    const limit = Number(url.searchParams.get("limit") ?? "20");
    const cursorParam = url.searchParams.get("cursor");
    const pathId = url.searchParams.get("path_id");
    const q = url.searchParams.get("q");

    let rows = sortedActiveCards().filter((record) => matchesCardFilters(record, pathId, q));
    if (cursorParam) {
      const cursor = parseCardsCursor(cursorParam);
      if (cursor) {
        // `(kept_at, id) < (cursor_kept_at, cursor_id)`, desc order (AL-410
        // plan §2) — the same one-row-comparison the real keyset predicate
        // makes, just spelled without `tuple_()`.
        rows = rows.filter((record) => {
          if (record.keptAt !== cursor.keptAt) return record.keptAt < cursor.keptAt;
          return record.id < cursor.id;
        });
      }
    }

    // Fetch `limit + 1` and trim (AL-410 plan §2): the extra row's presence,
    // not a count comparison against the *filtered* total, is what says
    // whether there is a next page — matching the real query exactly.
    const page = rows.slice(0, limit);
    const hasMore = rows.length > limit;
    const next_cursor = hasMore ? buildCardsCursor(page[page.length - 1]) : null;

    return HttpResponse.json({ cards: page.map(toCardListItem), next_cursor });
  }),

  http.patch(`${API_V1_BASE}/flashcards/:cardId`, async ({ params, request }) => {
    const cardId = params.cardId as string;
    const body = (await request.json()) as { front: string; back: string };
    const record = cardsStore.find((row) => row.id === cardId && !row.deleted);
    // Unowned / unknown / already-deleted all read as `404` on the real route
    // (AL-410 plan §5) — the fake collapses the same three cases into one
    // "no matching active row" branch, since it never models ownership at all.
    if (!record) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "Card not found." } },
        { status: 404 },
      );
    }
    if (updateCardFails) {
      return serverErrorEnvelope();
    }
    cardUpdateRequests.push({ card_id: cardId, front: body.front, back: body.back });
    // Never touches `rung`/`due_on` (AL-410 plan §2) — only these two fields
    // and the provenance marker move.
    record.front = body.front;
    record.back = body.back;
    record.edited_at = new Date().toISOString();
    return HttpResponse.json(toCardListItem(record));
  }),

  http.delete(`${API_V1_BASE}/flashcards/:cardId`, ({ params }) => {
    const cardId = params.cardId as string;
    const record = cardsStore.find((row) => row.id === cardId && !row.deleted);
    if (!record) {
      // Already-deleted is `404`, not a silent success (AL-410 plan §2) — what
      // makes a double-tapped delete honest rather than quietly successful
      // twice; `use-delete-card.ts` relies on exactly this to fold a repeat
      // delete into its own "already gone" success path.
      return HttpResponse.json(
        { error: { code: "not_found", message: "Card not found." } },
        { status: 404 },
      );
    }
    record.deleted = true;
    cardDeleteRequests.push(cardId);
    return new HttpResponse(null, { status: 204 });
  }),
];
