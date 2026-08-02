// Contract-shaped fake for the Progress API (Streaks TDD §6/§8, `docs/api.md`
// once it lands). Mirrors `mocks/paths.ts`'s shape — `configureProgress({...})`
// + `resetProgress()` — but carries no store: the endpoint has no created or
// deleted rows, only one summary a test dials in directly.

import { HttpResponse, http } from "msw";
import { API_V1_BASE, type ActivityCell, type ProgressSummary } from "../lib/api";

/** 49 zero-filled entries ending at `today` — the "no completions" shape
 *  (§5.4), and the shipped `STREAK_ACTIVITY_WINDOW_DAYS` window. */
export function zeroActivity(today: string, days = 49): ActivityCell[] {
  const anchor = new Date(today);
  return Array.from({ length: days }, (_, i) => {
    const date = new Date(anchor);
    date.setDate(date.getDate() - (days - 1 - i));
    return { date: date.toISOString().slice(0, 10), count: 0 };
  });
}

const DEFAULT_TODAY = "2026-08-02";

/** The zero-state summary: no streak, no activity, no path rows (§5.4). */
const DEFAULT_SUMMARY: ProgressSummary = {
  today: DEFAULT_TODAY,
  current_streak: 0,
  best_streak: 0,
  completed_today: 0,
  activity: zeroActivity(DEFAULT_TODAY),
  paths: [],
};

interface ProgressConfig {
  summary: ProgressSummary;
  /** When true, `GET /progress/summary` raises a generic `500` (the failed-query row, TDD §5.4). */
  serverError: boolean;
  /**
   * When true, `GET /progress/summary` parks instead of answering, until a test
   * calls `releaseProgress()`. This is what makes the D10 cache tests
   * (`completion-refresh.test.tsx`) deterministic rather than a race: with the
   * GET held open, whatever the DOM shows right after a completion can only be
   * the client's own optimistic patch — a fetch that cannot resolve cannot be
   * what produced it. Mirrors `mocks/tutor.ts`'s `hang` / `finishTutorStream`.
   */
  hang: boolean;
}

const defaultConfig: ProgressConfig = {
  summary: DEFAULT_SUMMARY,
  serverError: false,
  hang: false,
};

let config: ProgressConfig = { ...defaultConfig };
/** How many `GET /progress/summary` requests the fake has served — 0 proves a
 *  flag-off surface never fetched at all (`skipToken`, TDD §8). */
let requests = 0;
/** Every `tz_offset_minutes` the fake received, in call order. */
const receivedOffsets: number[] = [];
/** Settle callbacks for requests parked by `hang`, released on demand. */
let heldRequests: Array<() => void> = [];

/** Reset store + config between tests (wired into tests/setup.ts). */
export function resetProgress(): void {
  config = { ...defaultConfig };
  requests = 0;
  receivedOffsets.length = 0;
  heldRequests = [];
}

/**
 * Dial in the summary a test needs (`{ summary: {...} }`), force the endpoint
 * to fail (`{ serverError: true }`) to exercise the "streak line renders
 * nothing, the paths list is unaffected" row of TDD §5.4, or park it
 * (`{ hang: true }`) for the D10 optimism-vs-authoritative timing (above).
 */
export function configureProgress(overrides: Partial<ProgressConfig>): void {
  config = { ...config, ...overrides };
}

/** How many `GET /progress/summary` the fake has served. */
export function progressRequestCount(): number {
  return requests;
}

/** Every `tz_offset_minutes` query param the fake has seen, in order. */
export function progressReceivedOffsets(): number[] {
  return [...receivedOffsets];
}

/**
 * Let every request parked by `hang` answer with the **current** `config.summary`
 * — read at release time, not at request time, so a test can change the
 * configured summary while a request sits open and observe the new value land
 * (the "authoritative refetch corrects it" half of D10).
 */
export function releaseProgress(): void {
  const held = heldRequests;
  heldRequests = [];
  for (const settle of held) settle();
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

export const progressHandlers = [
  http.get(`${API_V1_BASE}/progress/summary`, async ({ request }) => {
    requests += 1;
    const offsetParam = new URL(request.url).searchParams.get("tz_offset_minutes");
    if (offsetParam !== null) receivedOffsets.push(Number(offsetParam));
    if (config.hang) {
      await new Promise<void>((resolve) => heldRequests.push(resolve));
    }
    if (config.serverError) {
      return serverErrorEnvelope();
    }
    return HttpResponse.json(config.summary);
  }),
];
