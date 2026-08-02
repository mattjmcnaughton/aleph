# Proposal — Phase 5, slice 1: Streaks

**Status:** Proposal (not accepted) · **Owner:** solo builder · **Roadmap item:** [Phase 5 — Momentum](../roadmap.md#phase-5--momentum)
**References:** [`README.md`](../../README.md) · [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) (ubiquitous language) · [Phase 1 PRD](phase-1-path-generation.md) · [Phase 1 TDD](../tdds/phase-1-path-generation.md) · [`metrics.md`](../metrics.md) · prior art: habagou `domains/streaks.py`, `services/progress.py`

> **One doc, not two.** Every other phase here splits PRD and TDD. This slice is small enough that
> splitting it would cost more than it clarifies: the product surface is a number and a chip, and
> the technical design is one query, one pure module, and one endpoint. §§1–4 are the product
> boundary; §§5–10 are the technical design. If the slice grows past this shape, split it then.

## 1. Summary

Two streaks, both counted in **days**, both derived from work the learner already does:

- **Daily streak** (global) — consecutive days on which the learner completed **at least one
  lesson**, on any path. This is *the* streak: the one that gets the flame, the celebration, and the
  count on the home screen.
- **Path streak** (per path) — consecutive days on which the learner completed at least one lesson
  **on that path**. A quieter stat shown on the path itself, deliberately not celebrated (§4.3).

Nothing else from Phase 5 ships here: no weekly goal ring, no daily-minutes target, no stats page,
no notifications. The README bounds this feature deliberately ("streaks and progress tracking, and
nothing more"), and this slice takes the smallest honest bite of it.

**The core design claim: this needs no new table.** A lesson already records `completed_at`, and a
lesson already belongs to exactly one path owned by exactly one learner. "Days I completed a lesson"
is a `GROUP BY` over rows we have. The whole feature is one migration (an index), one pure domain
module, one repository method, one service, one endpoint, and two small UI surfaces.

## 2. Why now, and what it changes about the plan

Streaks are scheduled for **Phase 5**, and [`CONTEXT.md`](../CONTEXT.md)'s phase-boundary note lists
"Streak / goal ring / daily minutes" as deferred there. Building this now pulls a slice forward, the
same way Phase 2B was pulled forward from Phase 4 by owner decision. That is a legitimate move, but
it is not free: the vocabulary is authoritative, so shipping the word **streak** means the docs say
what the code does on the same day, not later.

Accepting this proposal therefore includes (step 0 of §11):

- **[`CONTEXT.md`](../CONTEXT.md)** — add **Daily streak**, **Path streak**, **Active day**, **Best
  streak** to *Progress & structure*; amend the phase-boundary bullet so it reads "goal ring / daily
  minutes — Phase 5; **streaks shipped early, see the streaks proposal**".
- **[`roadmap.md`](../roadmap.md)** — a sentence in Phase 5 recording that the streak slice landed
  ahead of the rest, exactly as Phase 2's paragraph records 2B's pull-forward.
- **[`docs/api.md`](../api.md)** — the new endpoint.
- **[`docs/metrics.md`](../metrics.md)** — the new event (§9).

The honest counter-argument: Phase 3 (flashcards) and Phase 4 (adaptive paths) both produce
*reasons to return*, and a streak counts returns that the product does not yet earn. A streak over a
loop with nothing due tomorrow measures willpower, not the product. This slice is cheap enough that
it is worth shipping anyway — but it should be read as instrumentation for the return metric, not as
a retention feature in its own right, and §9 states the metric that would prove it either way.

## 3. What a learner sees

**Home (`/`, "Your paths").** A single line above the list: `🔥 5-day streak · 1 lesson today`. At
zero it reads `Complete a lesson to start a streak` — an invitation, never a scold. Below it, a
45-day activity strip (the habagou heatmap, one cell per day, Nocturne teal at three intensities).

**Each switcher row.** A small neutral chip when the path streak is ≥ 2 days: `3-day`. No flame, no
color escalation.

**Path view.** The same neutral chip beside the existing progress roll-up.

**On completing a lesson.** If the completion is the first of the day, the streak line updates and
briefly says `Day 6 🔥`. It does not block, animate at length, or interrupt navigation to the next
lesson. If the completion is the second of the day, nothing happens — the streak is a day counter,
not a lesson counter.

**Never:** a push, an email, a "you're about to lose your streak" warning, a freeze/repair purchase,
or a leaderboard. Restraint is the feature.

## 4. Product decisions

**4.1 A day is a calendar day in the learner's local timezone.** [`CONTEXT.md`](../CONTEXT.md)
already defines **Day** this way for metrics; the streak inherits it rather than inventing a second
answer. Mechanics in D3.

**4.2 Completion is the signal — not viewing, not attempting.** A lesson read but not marked
complete does not count, and neither does an Attempt on its own. This matches the existing rule that
"completion, not correctness, is what counts" and keeps the streak to **one** input. Note this
diverges from **Engaged** (attempt *or* complete), which is the immutability boundary and answers a
different question; the two must not be conflated in code or prose.

**4.3 The path streak is a stat, not a game.** With multiple paths a learner naturally alternates —
which is exactly the behavior the **Breadth** metric wants — and a per-path streak breaks every time
they do. Celebrating it would punish the product's own goal. So the path streak is displayed
neutrally, is never the subject of a nudge, and is hidden below 2 days.

**4.4 The current streak does not break at midnight.** A learner who studied yesterday and has not
yet studied today still sees "5-day streak", not "0". The streak breaks when a day passes with no
completion — i.e. it is computed from the run ending **today, or yesterday if today is empty**.
(Ported from habagou's `compute_streaks`; it is the difference between a streak that motivates and
one that shouts at you before breakfast.)

**4.5 The daily target is one lesson.** habagou's streak requires 3 completions a day because its
activities are ~1 minute each; an Aleph lesson is a Read passage plus a Quick check. One is the
right bar, and it means the daily goal *is* the streak — no separate goal concept, no ring.

**4.6 Deleting a path erases its days from the global streak.** This is the real cost of the
no-new-table design (D1), and it is a genuine product wart: delete a path you soured on, lose the
streak you built on it. Accepted for v1 because deletion is rare and the escape hatch is designed
(D1, "if it bites"). The delete confirm gains one line of copy — *"This also removes its lessons
from your streak"* — so it is at least never a surprise.

## 5. Design decisions

**D1 — Derive from `lessons.completed_at`; store nothing.** Aleph's grain is *derived, never
stored*: unlock state (`domains/progression`), engagement (`domains/engagement`), and a Proposal's
status are all computed rather than persisted, because a second stored copy is how two answers to
one question start disagreeing. A streak is the same shape of question, and the source rows already
exist — `lessons.completed_at`, `lessons.path_id`, `paths.user_id`. Derivation means: no migration
beyond an index, **no backfill** (every existing completion counts from day one), no dual-write on
the completion path, no drift, and nothing to reconcile after the shaping/undo machinery touches a
path.

*The alternative considered*, and habagou's actual approach, is an append-only ledger — habagou
writes an `ActivityCompletion` row per completion and groups over that. Ported here it would be a
`streak_days (user_id, path_id NULL ON DELETE SET NULL, day, lesson_count)` table, ~150 lines plus a
backfill migration. Its one real advantage is surviving path deletion (§4.6). We are not paying that
price up front. **If it bites** — if deletion-loses-streak shows up in use — the upgrade is
additive and self-contained: add the table, backfill it from `lessons.completed_at` (which is
lossless, because derivation *is* the current definition), and switch the repository method behind
the same service interface. The domain module and every layer above it are unchanged. That is the
whole reason to start derived.

**D2 — One pure domain module, `domains/streaks.py`.** Input is a set of dates plus "today"; output
is `Streaks(current, best)`. No ORM, no session, no timezone arithmetic (the dates arriving are
already local — D3). This is a direct port of habagou's `compute_streaks` with the daily target
collapsed to 1 (§4.5), which simplifies its input from `Mapping[date, int]` to `AbstractSet[date]`.
Counts are still carried separately for the heatmap, but the streak logic never sees them — a
threshold we do not have is a threshold that cannot drift.

**D3 — The client sends its UTC offset; the server owns "today".** `GET /progress/summary` takes
`tz_offset_minutes`, exactly habagou's contract, and the frontend passes
`new Date().getTimezoneOffset()` **verbatim** — that value is `UTC − local` in minutes (UTC+2 sends
`-120`), so the server *subtracts* it to reach local time:

```sql
(l.completed_at - make_interval(mins => :tz_offset_minutes))::date
```

and derives today the same way (`datetime.now(UTC) - timedelta(minutes=tz_offset_minutes)`). One
sign convention, stated once, and a unit test pins it in both directions — this is precisely the
kind of thing that is silently off by a day for a whole hemisphere otherwise. Bounded `ge=-900,
le=900` on the query param; default `0`.

*Why not an account timezone column?* Because it is a second source of truth that goes stale when a
learner travels, and it would need a settings UI to fix. The offset is a property of the request,
which is where it actually lives. The visible consequence: crossing timezones can shift a day
boundary and, at the extreme, a streak. Accepted, and cheap to revisit if anyone ever cares.

**D4 — One endpoint returns global *and* per-path.** `GET /api/v1/progress/summary` returns the
global streak, the 45-day activity strip, and a per-path breakdown in one payload. The alternatives
are worse in specific ways: folding streaks into `PathProgressDTO` would add a `GROUP BY` to the
path-detail poll, which fires every 2–5s during generation; a separate `/paths/{id}/streak` would be
a second round trip for the switcher, which needs every path anyway. A learner's path count is small
and bounded, so one grouped query serves both readings. The frontend fetches it once and reads it
from home, from the switcher rows, and from the path view.

**D5 — One query, grouped by `(path_id, local_day)`.** The repository returns rows of
`(path_id, day, count)` for the caller's paths; the service folds them two ways — union the days for
the global streak, filter by `path_id` for each path streak. Per-path daily *counts* are not sent
over the wire (the heatmap is global only), which keeps the payload flat.

**D6 — The only migration is a partial index.**

```sql
CREATE INDEX ix_lessons_path_id_completed_at
  ON lessons (path_id, completed_at)
  WHERE completed_at IS NOT NULL;
```

It covers the group-by, and being partial it stays small — most lessons on a growing path are
incomplete. `paths` is already indexed on `user_id`.

**D7 — Ship behind a `streaks` feature flag.** Same machinery and same reasoning as `tutor` and
`shaping`: add `FeatureFlag.STREAKS`, default **off** in `FLAG_DEFAULTS`, present in
`ADMIN_DEFAULT_FLAGS` so it is dogfooded in production before launch. Launch is the flag flip, and
the endpoint returns `404` when the flag is off for the caller so a disabled feature has no wire
surface at all.

**D8 — Idempotent re-completion is free, and Undo is already safe.** Re-completing a lesson does not
re-stamp `completed_at` (the repository's `completed_at IS NULL` guard), so a day cannot be
double-counted — and under D1 there is no separate write to make idempotent in the first place.
Shaping's **Undo** never touches progress by definition, so a Change that is applied and undone
leaves the streak untouched. Both properties are inherited, not built; both get a regression test
(§10) so they stay inherited.

## 6. Data & queries

No schema change beyond D6's index. The one query:

```sql
SELECT l.path_id,
       (l.completed_at - make_interval(mins => :tz_offset_minutes))::date AS day,
       count(*) AS n
FROM lessons l
JOIN paths p ON p.id = l.path_id
WHERE p.user_id = :user_id
  AND l.completed_at IS NOT NULL
GROUP BY l.path_id, day
```

Ownership is enforced in the query (the `paths.user_id` predicate), matching how
`PathRepository.get_for_user` scopes every other read — another learner's completions can never
enter the fold.

## 7. API contract

```
GET /api/v1/progress/summary?tz_offset_minutes=-120
```

```jsonc
{
  "today": "2026-08-02",
  "current_streak": 5,
  "best_streak": 12,
  "completed_today": 1,
  "activity": [                       // 45 entries, oldest first, gaps filled with 0
    { "date": "2026-06-19", "count": 0 },
    { "date": "2026-06-20", "count": 2 }
  ],
  "paths": [                          // one entry per live path, including 0-streak paths
    { "path_id": "…", "current_streak": 3, "best_streak": 7, "completed_today": 1 }
  ]
}
```

`401` unauthenticated · `404` when the `streaks` flag is off for the caller (D7) · `422` on an
out-of-range offset, through the shared validation envelope.

New DTO module `dtos/progress.py`; new router `routers/v1/progress.py`; new service
`services/progress_read.py` (named for the existing `paths_read` / `lessons_read` read-side
convention); new repository method on `LessonRepository`. Layering is the standard
`routers → services → repositories`, with `domains/streaks.py` pure beneath it.

## 8. Frontend

- `lib/api.ts` — `progressSummaryQueryOptions`, with the `getTimezoneOffset()` call at the single
  call site (D3), plus the `ProgressSummary` type mirroring §7.
- `components/streak-line.tsx` — the headline + flame + empty state (§3).
- `components/activity-strip.tsx` — the 45-cell heatmap, three Nocturne intensities.
- `components/streak-chip.tsx` — the neutral per-path chip, rendered only at ≥ 2 days (§4.3).
- Home renders the line, the strip, and a chip per row; the path view renders a chip.
- **Freshness:** completing a lesson invalidates the summary query, so the number moves in the same
  interaction that earned it. This is the one piece of wiring that is easy to forget and immediately
  visible when missing.
- Gated on the `streaks` flag via `useFeatureFlag` (`lib/feature-flags.ts`, which reads the cached
  session payload) — no flag, no fetch.
- `mocks/handlers.ts` gains a summary handler so the component tests have a payload.

## 9. Events & metrics

One new event, `progress_summary_viewed` (fields: `current_streak`, `path_count`; workflow `W22`),
registered in `events.py`'s `EVENT_FIELDS` — the manifest test and
`tests/unit/test_metrics_queries.py` enforce that nothing references a field no event emits.

No event is needed for the streak *itself*: `lesson_completed` already carries `account_id` and its
timestamp, so streak length is computable in Logfire from data we have been emitting since Phase 1 —
including **for the period before this ships**, which is the useful part. That gives a real baseline
for the one question worth asking:

> **Does the streak move return?** Compare the existing **Return** metric (activated learners back on
> a 2nd distinct day) for accounts before and after the flag flip.

If it does not move it, this slice is decoration and Phase 5's remaining scope should be re-argued
rather than built. A `streak_return.sql` query alongside `return_rate.sql` makes that answerable
instead of arguable.

**New workflows** (W22 is the next free number; W1–W21 are taken):

- **W22 — Completing a lesson visibly advances the streak.** Complete the day's first lesson → the
  count increments in the same interaction, on a phone viewport.
- **W23 — A streak survives a missed day boundary but breaks on a missed day.** Studied yesterday,
  nothing today → the streak still reads yesterday's length (§4.4). Two days idle → zero.

## 10. Testing

Red-green TDD, fakes over mocks (CLAUDE.md).

**Unit — `tests/unit/test_streaks.py`** (pure, the bulk of the value): empty input → `(0, 0)`; a
single day today → `(1, 1)`; run ending today; run ending yesterday with today empty (§4.4); today
empty *and* yesterday empty → current `0`, best preserved; a gap splits runs and `best` is the
longest, not the latest; out-of-order and duplicate input; `best ≥ current` always.

**Unit — service, against a fake repository behind a `Protocol`**: the global fold unions days
across paths (two paths, same day → one active day); per-path folds are independent; the 45-day
activity window is contiguous and zero-filled; a path with no completions still appears with a
0-streak.

**Unit — timezone**: the `getTimezoneOffset()` sign convention in both hemispheres (D3), including a
completion at 23:30 UTC that is *tomorrow* for UTC+2 and *today* for UTC−5.

**Integration — `tests/integration/test_progress_api.py`** (real Postgres): the endpoint against
seeded completions; `tz_offset_minutes` shifts a day boundary end-to-end; another learner's
completions never appear; an idempotent re-complete does not change the payload (D8); a path deleted
mid-test removes its days (§4.6 — pinning the accepted wart so it is a decision, not a bug); flag
off → `404`.

**E2E — Playwright, phone viewport**: W22 and W23 as
`src/aleph/web/frontend/tests/e2e/journeys/w22.spec.ts` and `w23.spec.ts`, each a
`test.describe(…, { tag: "@w22" })` — the naming and tagging the existing w1–w21 journeys use.
(`@pytest.mark.workflow("W22")` is the *backend* half of that convention and belongs on the
integration tests above, not here.)

## 11. Delivery plan

Seven commits, each independently reviewable, in dependency order.

| # | Commit | Scope |
| - | ------ | ----- |
| 0 | `docs:` | CONTEXT.md vocabulary + roadmap reconciliation (§2). **Precedes the code** — the vocabulary is authoritative. |
| 1 | `feat:` | `domains/streaks.py` + its unit tests. Pure, no dependencies, mergeable alone. |
| 2 | `feat:` | Migration `0009` (the partial index, D6) + the repository method + its integration test. |
| 3 | `feat:` | `dtos/progress.py`, `services/progress_read.py`, `routers/v1/progress.py`, the `streaks` flag, the API integration tests, `docs/api.md`. |
| 4 | `feat:` | Frontend: api client, the three components, home + path view wiring, query invalidation, msw handlers, component tests. |
| 5 | `feat:` | The `progress_summary_viewed` event, `queries/logfire/streak_return.sql`, `docs/metrics.md`. |
| 6 | `test:` | W22/W23 e2e. |

Steps 1 and 2 are parallel; 3 depends on both. Launch (the flag flip) is a separate change after
dogfooding, following the `deploy.md` flagged-phase runbook.

**Rough size:** ~700 lines of production code and ~600 of tests, most of it the frontend. The
backend is genuinely small — that is the point of D1.

## 12. Explicitly out of scope

Weekly goal ring · daily-minutes target · a dedicated stats/progress page · streak freezes, repairs
or purchases · notifications or email of any kind · leaderboards or any social surface · milestone
badges (habagou has `next_milestone`; it is a nudge mechanic and this slice does not need one) ·
per-path heatmaps · timezone as an account setting (D3) · counting Attempts or lesson views as
activity (§4.2).

## 13. Open questions

1. **Is the activity strip in v1, or does the streak line ship alone?** The strip is the single
   biggest chunk of frontend here, and the streak line delivers the feature without it. Splitting it
   out of step 4 is easy if v1 wants to be smaller still.
2. **Should the delete confirm really carry the streak warning (§4.6),** or does that advertise a
   wart better left quiet until someone hits it? Leaning toward including it — a surprise here is
   worse than a sentence.
3. **Does the path streak earn its place at all?** It was asked for explicitly, and it is nearly free
   given D5 — but §4.3 argues it is a stat nobody acts on. Worth a look after a few weeks of
   dogfooding; removing it later is a one-component deletion.
