import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession, type ReviewQueue } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { configureFlashcards } from "../mocks/flashcards";
import {
  configureProgress,
  progressRequestCount,
  releaseProgress,
  zeroActivity,
} from "../mocks/progress";
import { server } from "../mocks/server";
import { App } from "./app";

// Phase 3 TDD §8: the day's first review advances the streak line — the same
// "Day N 🔥" beat the day's first lesson completion gets (Streaks D10) — from
// a **flashcards** mutation (`routes/review.tsx`'s grade `onSuccess`) reaching
// into the **progress** cache. This is the phase's one piece of cross-domain
// cache wiring, and the sibling of `completion-refresh.test.tsx`'s own D10
// suite: same technique (`hang`/`releaseProgress` park the `GET` so whatever
// the streak line shows right after a grade can only be the client's own
// cache, never a lucky fast refetch), and the same three properties.
//
// The one thing this file pins that the completion suite does not have to:
// the guard here is **today's activity cell**, never `completed_today` — the
// backend's `completed_today` counts lesson completions only and a review
// never moves it (docs/api.md), so keying off it would never no-op on a
// second same-day review, and every card graded after the first would
// double-count the streak. The backend marks a review-only day's cell
// `count = 1` for exactly this reason, which is what the guard reads instead.

const TODAY = "2026-08-04";

// Both flags on: this suite needs the streak line itself (to observe the
// patch) as well as `flashcards` (to reach the review session at all).
const flashcardsSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { flashcards: true, streaks: true } },
};

function useFlashcardsSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(flashcardsSession)));
}

const CARD = {
  card_id: "c1",
  front: "What does `extends` mean?",
  back: "It constrains T.",
  rung: 0,
  got_it_interval_days: 7,
  path_id: null,
  source: {
    kind: "degraded" as const,
    lesson_title: "Generic constraints",
    path_title: "Learn TypeScript",
  },
};

function oneCardQueue(): ReviewQueue {
  return {
    today: TODAY,
    total: 1,
    completed: 0,
    scope_path_id: null,
    other_due_count: 0,
    cards: [CARD],
  };
}

/** Due enough that the pill renders — the door this suite navigates through. */
function seedOneCardDue(): void {
  configureFlashcards({
    queue: oneCardQueue(),
    summary: { today: TODAY, due_count: 1, estimated_minutes: 1, paths: [] },
  });
}

function progressSummaryFixture(overrides: {
  current_streak: number;
  best_streak: number;
  /** Today's activity cell — the guard this patch actually keys off. */
  todayCount: number;
}) {
  const activity = zeroActivity(TODAY);
  activity[activity.length - 1] = {
    ...activity[activity.length - 1],
    count: overrides.todayCount,
  };
  return {
    today: TODAY,
    current_streak: overrides.current_streak,
    best_streak: overrides.best_streak,
    // Deliberately NOT derived from `todayCount`: a review-only day leaves
    // this at 0 (docs/api.md — lesson completions only), which is the whole
    // reason the patch cannot key off it the way the completion patch does.
    completed_today: 0,
    activity,
    paths: [],
  };
}

/** Today's cell — the last of the 49, oldest-first (TDD §6). */
function todayActivityCell(): HTMLElement {
  const cells = screen.getAllByTestId("activity-cell");
  return cells[cells.length - 1];
}

/** Home → the due pill → the one card, graded `Got it` → back home. */
async function gradeViaPillAndReturnHome(): Promise<void> {
  fireEvent.click(screen.getByTestId("review-pill"));
  fireEvent.click(await screen.findByTestId("review-card-flip"));
  fireEvent.click(screen.getByTestId("review-grade-got-it"));
  // Wait for the grade's own `onSuccess` to have actually run before leaving:
  // the queue's local patch and the progress patch are sequential statements
  // in the same handler, so the one-card queue emptying (`session-complete`)
  // is proof the streak patch below it has already landed too — otherwise
  // clicking home immediately races the still-pending mutation.
  await screen.findByTestId("session-complete");
  fireEvent.click(screen.getByRole("link", { name: "Aleph home" }));
  await screen.findByTestId("paths-switcher");
}

describe("Flashcards — the review-side streak patch (Phase 3 TDD §8)", () => {
  it("[cold cache] grading before ever visiting home fabricates nothing", async () => {
    useFlashcardsSession();
    seedOneCardDue();

    window.history.pushState({}, "", "/review");
    render(<App />);
    fireEvent.click(await screen.findByTestId("review-card-flip"));
    fireEvent.click(screen.getByTestId("review-grade-got-it"));
    await screen.findByTestId("session-complete");

    // Home was never visited: there is no cache entry under `["progress", …]`
    // for the patch to touch, and no observer for `invalidateQueries` to wake
    // — both have to hold for this to stay at zero rather than becoming a
    // request nobody asked for.
    expect(progressRequestCount()).toBe(0);
  });

  it("[second review of the day] an already-active cell leaves the streak untouched", async () => {
    useFlashcardsSession();
    seedOneCardDue();
    configureProgress({
      summary: progressSummaryFixture({ current_streak: 5, best_streak: 5, todayCount: 1 }),
    });

    window.history.pushState({}, "", "/");
    render(<App />);
    await screen.findByTestId("streak-line");
    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 5-day streak");

    // Park the next GET: the streak line's value right after this can only be
    // read off the cache the grade mutation itself wrote.
    configureProgress({ hang: true });
    await gradeViaPillAndReturnHome();

    // The cell was already `1` (an earlier review today, or a lesson
    // completion — either reads as "already active"), so the guard
    // (`activity[lastDay].count > 0`) must have returned `old` untouched.
    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 5-day streak");
    expect(todayActivityCell().getAttribute("data-intensity")).toBe("dim");

    // Release with the server's own count for a genuine second same-day
    // review — the cell legitimately does not move further (D11: a
    // review-only day is marked `count = 1`, not incremented per review) —
    // confirming the guard's no-op was correct, not a coincidence.
    configureProgress({
      summary: progressSummaryFixture({ current_streak: 5, best_streak: 5, todayCount: 1 }),
    });
    releaseProgress();

    await waitFor(() => {
      expect(screen.getByTestId("streak-line").textContent).toBe("🔥 5-day streak");
    });
  });

  it("[first review of the day] the line moves before any refetch, then the refetch confirms it", async () => {
    useFlashcardsSession();
    seedOneCardDue();
    configureProgress({
      summary: progressSummaryFixture({ current_streak: 4, best_streak: 4, todayCount: 0 }),
    });

    window.history.pushState({}, "", "/");
    render(<App />);
    await screen.findByTestId("streak-line");
    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 4-day streak");

    configureProgress({ hang: true });
    await gradeViaPillAndReturnHome();

    // The "Day 5 🔥" beat fires off this value, in this interaction — the GET
    // that would otherwise be needed to learn it is still parked, so this can
    // only be the optimistic patch, not a lucky fast round trip.
    expect(screen.getByTestId("streak-line").textContent).toBe("🔥 5-day streak");
    expect(todayActivityCell().getAttribute("data-intensity")).toBe("dim");

    // The authoritative payload lands and confirms it (released once the
    // optimistic value has already been observed above).
    configureProgress({
      summary: progressSummaryFixture({ current_streak: 6, best_streak: 6, todayCount: 1 }),
    });
    releaseProgress();

    await waitFor(() => {
      expect(screen.getByTestId("streak-line").textContent).toBe("🔥 6-day streak");
    });
  });
});
