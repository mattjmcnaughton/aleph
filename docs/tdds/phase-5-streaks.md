# TDD — Phase 5, slice 1: Streaks

**Status:** Draft · **Owner:** solo builder · **Companion to:** [Phase 5 streaks PRD](../prds/phase-5-streaks.md)
**References:** [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) · [Phase 1 TDD](phase-1-path-generation.md) · [Phase 2B TDD](phase-2b-shape-your-path.md) · [`metrics.md`](../metrics.md) · prior art: habagou `domains/streaks.py`, `services/progress.py`

> The PRD owns the product boundary — what a streak is, what breaks it, what is deliberately
> never built. This TDD owns everything else: the pure module and its edges, the one query and
> its timezone mechanics, the endpoint and its DTOs, the flag, the frontend surfaces and their
> cache wiring, the e2e clock problem, and the delivery plan.

Decision numbers restart at D1, scoped to this document. References into earlier TDDs are
always qualified ("Phase 1 D5", "Phase 2B §5.6").

> **This document is a split, not a new design.** The PRD shipped as one combined doc on the
> argument that the slice was too small to split. Splitting it was the owner's call once the
> technical surface turned out to have four decisions the product boundary should not carry
> (D3's `timestamptz` correction, D9's dropped event, D10's cache key, D11's clock). The PRD is
> trimmed to §§1–4 plus scope and open questions; §§5–13 of the original live here, renumbered
> and — in the places §14 records — corrected.

## 1. Decision record

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| D1 | Storage | **Derive from `lessons.completed_at`; store nothing.** No streak table, no ledger, no backfill, no dual-write on the completion path | PRD §1's core design claim. Aleph's grain is *derived, never stored* — unlock state (`domains/progression`), engagement (`domains/engagement`) and a Proposal's resolution (Phase 2B D3) are all computed, because a second stored copy is how two answers to one question start disagreeing. The source rows already exist and every historical completion counts from day one |
| D2 | Pure module | **`domains/streaks.py`** — `compute_streaks(active_days: AbstractSet[date], *, today) -> Streaks` and, separately, `activity_window(counts: Mapping[date, int], *, today, days) -> list[ActivityCell]` | A port of habagou's `compute_streaks` with the daily target collapsed to 1 (PRD §4.5), which simplifies its input from `Mapping[date, int]` to a set. **The streak function never sees counts** — a threshold we do not have is a threshold that cannot drift. The window function needs them and is therefore a different function, not a different parameter |
| D3 | Day boundary | Client sends `tz_offset_minutes` (`getTimezoneOffset()` verbatim); the server subtracts it to reach local time, **after** pinning the timestamp to UTC: `((completed_at AT TIME ZONE 'UTC') - make_interval(mins => :tz))::date` | PRD §4.1. The `AT TIME ZONE 'UTC'` is a **correction to the combined doc's SQL** (§14, R1): casting a `timestamptz` to `date` resolves in the session's `TimeZone` GUC, so that expression is correct only while that GUC is UTC. Pinning first makes the arithmetic independent of server configuration — which is the entire point of taking the offset from the request |
| D4 | Endpoint | **One** endpoint, `GET /api/v1/progress/summary`, returning the global streak, the activity window and the per-path breakdown in one payload | Folding streaks into `PathProgressDTO` would add a `GROUP BY` to the path-detail poll, which fires every 2–5s during generation; a separate `/paths/{id}/streak` would be a round trip per row on a screen that needs every row. The name is a Phase-5 envelope: the goal ring, the minutes target and the stats page grow the payload rather than the API |
| D5 | Query | **One query grouped by `(path_id, local_day)`**, ownership enforced by the `paths.user_id` predicate; the service folds it twice — union the days for the global streak, filter by `path_id` per path. **A path with no completions produces no row and is simply absent** from `paths` | The combined doc's §6, with its contract corrected (§14, R2): the group-by cannot manufacture a row for a path that has none, and the chip is hidden below 2 days anyway, so "absent means zero" costs no pixel and keeps the query the tidy group-by the design is built on |
| D6 | Migration | **`0009_lesson_completed_at_index`** — one partial index, `lessons (path_id, completed_at) WHERE completed_at IS NOT NULL`. Nothing else | It covers the join's inner side and the group-by's input; partial keeps it small, since most lessons on a growing path are incomplete. `ix_paths_user_id` already covers the outer side |
| D7 | Rollout | **`FeatureFlag.STREAKS`**, `False` in `FLAG_DEFAULTS`, present in `ADMIN_DEFAULT_FLAGS`; gated **router-level** (`dependencies=[Depends(require_streaks_enabled)]`) so a future route cannot forget it. Off → `404` | The `tutor` / `shaping` machinery verbatim (Phase 2 D14, Phase 2B). Dogfooded in production before launch; launch is one committed `FEATURE_FLAG_DEFAULTS` entry. A disabled feature has no wire surface at all |
| D8 | Idempotence | **Inherited, not built.** Re-completion cannot double-count (`mark_completed_and_finalize`'s `completed_at IS NULL` guard); Undo never touches progress by definition (Phase 2B §5.7) | Under D1 there is no second write to make idempotent. Both properties get a regression test (§11) so they *stay* inherited — that is the only work here |
| D9 | Instrumentation | **No new product event.** `EVENT_FIELDS` is untouched; `queries/logfire/streak_return.sql` computes streak length from `lesson_completed`, which has carried `account_id` and a timestamp since Phase 1 | §14, R3. A `progress_summary_viewed` firing per GET would mostly count invalidation refetches caused by completions (D10) — a number that reads as engagement and isn't. The retroactive baseline, which is the whole reason the combined doc wanted an event, comes free from `lesson_completed` instead |
| D10 | Cache wiring | Own key `["progress", "summary", tzOffset]`, invalidated **explicitly** by the completion mutation, plus an **optimistic bump** of the cached payload when the completion is the day's first | The honest key: a global cross-path summary does not belong under the `["paths"]` prefix, even though nesting it there would make invalidation free. The forgettable line is pinned by `src/app/completion-refresh.test.tsx`, which exists for exactly this class of bug. Optimism is what makes PRD §3's "same interaction" true on a phone rather than true after a round trip |
| D11 | E2E clock | The stub backend (`scripts/e2e_backend.py`) mounts a test-only `POST /__e2e__/shift-completions` that backdates a path's completions. **Mounted by `create_stub_app` only** — the production factory never sees the router | Phase 1 D10 / Phase 2B D12 discipline: determinism lives in the stub backend, never behind a config guard in real code. W23 needs yesterday to exist and Playwright cannot wait; a *shift* primitive is enough, so no fabricated lessons and no clock seam in the server |
| D12 | Activity strip | A **7-row × 7-column week grid** (49 cells, of which the window's 45 are live), weekday-aligned, three teal intensities | 45 cells in one row is ~6px each on a 390px viewport. The week grid lands at ~14–16px, and the weekly rhythm ("never on Wednesdays") is legible — which a flat row hides. Costs a leading-pad rule and vertical space on home |

## 2. Extension map

| Concern | Existing asset | Streaks change |
| --- | --- | --- |
| Completion timestamp | `lessons.completed_at`, stamped by `LessonRepository.mark_completed_and_finalize` under a `completed_at IS NULL` guard | **Reuse untouched.** This slice adds no write path at all — the single most important property of D1 |
| Ownership scoping | `PathRepository.get_for_user`, `LessonRepository.get_for_user`'s join-to-path | **Reuse the pattern:** the new read carries `JOIN paths … WHERE paths.user_id = :uid` in the query, not in the service |
| Grouped aggregate reads | `LessonRepository.progress_summaries` (group-by returning a dict keyed by path id) | **Extend:** a second grouped read on the same repository, `completion_days_for_user` — deliberately *not* folded into `progress_summaries`, whose callers poll per path |
| Pure derivation | `domains/progression.py`, `domains/engagement.py` — frozen inputs, no ORM, stdlib only | **New** `domains/streaks.py` under the same rules; auto-covered by the layering test |
| Read-side services | `services/paths_read.py`, `services/lessons_read.py` — module-level frozen views + one async function taking `session` first | **New** `services/progress_read.py` in the identical shape |
| Router conventions | `routers/v1/`, `CurrentUser` / `Session` aliases, 404-never-403, the error envelope | **New** `routers/v1/progress.py`; conventions verbatim |
| Feature flags | `FeatureFlag` enum, `FLAG_DEFAULTS`, `ADMIN_DEFAULT_FLAGS`, `require_*_enabled` router dependency, `user.feature_flags` on the session payload | **Extend:** one enum member, two registry entries, one dependency — all copied from `require_shaping_enabled` |
| Frontend HTTP seam | `lib/api.ts` — `apiFetch`, `queryOptions` pairs, `skipToken` for idle | **Extend:** `progressSummaryQueryOptions`, `ProgressSummary` types |
| Flag gating (client) | `lib/feature-flags.ts` `useFeatureFlag`, reading the cached session | **Reuse verbatim** — no flag, no fetch (`skipToken`) |
| Home surface | `routes/index.tsx` — `paths-switcher`, `paths-list`, `PathRow` | **Extend:** streak line + activity strip above the list, chip inside each row |
| Completion refresh | `lessons.$lessonId.tsx` mutation `onSuccess`; `src/app/completion-refresh.test.tsx` | **Extend:** one optimistic `setQueryData` + one `invalidateQueries` (D10), pinned by that test file |
| MSW | `mocks/handlers.ts` composing per-domain handler modules with `configure*` / `reset*` | **New** `mocks/progress.ts` in the same shape |
| E2E harness | `scripts/e2e_backend.py::create_stub_app`, `tests/e2e/fixtures/journey.ts`, `w<N>.spec.ts` + `@w<N>` | **Extend:** the `/__e2e__` router (D11), a `shiftCompletions` fixture helper, `w22.spec.ts` / `w23.spec.ts` |

**Built new:** migration `0009` (§4), `domains/streaks.py` (§5.1), `LessonRepository.completion_days_for_user`
(§5.2), `services/progress_read.py` (§5.3), `dtos/progress.py` + `routers/v1/progress.py` (§6),
`streak-line.tsx` / `activity-strip.tsx` / `streak-chip.tsx` (§8), `queries/logfire/streak_return.sql`
(§9), the `/__e2e__` stub router (§11).

**Not built, and named so the absence is a decision:** no new table, no backfill, no migration
beyond an index, no new event, no new agent, no evals (§10), no rate limiter (§7), no config
knob other than the window length (§13).

## 3. Architecture overview

Layering unchanged: `routers → services → repositories`, with `domains/` pure beneath. The
structural claim this slice makes is a *negative* one — **nothing here writes** — and it is
enforced by module topology: `services/progress_read.py` holds no `session.commit()`, calls no
repository mutator, and is reachable only from a `GET`.

```
src/aleph/
  domains/
    streaks.py          # compute_streaks + activity_window — pure, stdlib only
  repositories/
    lessons.py          # + completion_days_for_user (the one query)
  services/
    progress_read.py    # folds the rows two ways; owns "today"
  routers/v1/
    progress.py         # GET /api/v1/progress/summary, router-gated on the streaks flag
  dtos/
    progress.py
  services/
    feature_flags.py    # + FeatureFlag.STREAKS
```

The read path, end to end:

```
GET /progress/summary?tz_offset_minutes=-120
  → require_streaks_enabled            (404 if off, before any work)
  → load_progress_summary(session, user_id=…, tz_offset_minutes=…)
      → LessonRepository.completion_days_for_user   → [(path_id, day, count)]
      → today = (now(UTC) - offset).date()
      → compute_streaks({all days})                 → global Streaks
      → compute_streaks({days of path p}) per path  → per-path Streaks
      → activity_window({day: sum(count)}, today, 45)
  → ProgressSummaryResponse
```

## 4. Data model & storage schema (migration `0009`)

No schema change. One index:

```
0009_lesson_completed_at_index
lessons   + INDEX ix_lessons_path_id_completed_at (path_id, completed_at)
            WHERE completed_at IS NOT NULL
```

Written in the `0007_applied_change_uniqueness` style (`op.create_index(..., postgresql_where=…)`),
`down_revision = "0008_path_title_and_guidance"`. Online-safe on Neon at this table's size;
`CONCURRENTLY` is not used because Alembic runs migrations in a transaction and the table is small
— if it ever isn't, that is a one-line change with `autocommit_block()`.

The index is declared on the model too (`Lesson.__table_args__`), so `tests/integration/test_schema.py`
— which asserts model/DDL agreement — keeps the two honest.

**What the plan should look like:** `ix_paths_user_id` seeks the learner's paths, then a nested
loop does a partial-index scan per path. The index is covering for the scan's needs (`path_id`,
`completed_at`), so an index-only scan is available when the visibility map is warm. The
group-by key is an *expression*, so the index cannot supply the grouping order — sorting/hashing
a learner's completion rows is the cost, and it is bounded by their total completed lessons.

**Growth:** the query returns every completion day ever, because `best_streak` is all-time (§14, R4).
One row per `(path, day)`, so a learner studying daily on three paths for two years is ~2 000 rows.
That is the honest ceiling and it is not a problem; if it ever becomes one, the fix is to bound
`best` to a window, which is a product change, not a technical one.

## 5. The read pipeline

### 5.1 Pure domain (`domains/streaks.py`)

Stdlib only, frozen inputs, no ORM — the `domains/__init__.py` contract verbatim.

```python
@dataclass(frozen=True)
class Streaks:
    current: int
    best: int

@dataclass(frozen=True)
class ActivityCell:
    day: date
    count: int

def compute_streaks(active_days: AbstractSet[date], *, today: date) -> Streaks: ...

def activity_window(
    counts: Mapping[date, int], *, today: date, days: int
) -> list[ActivityCell]: ...
```

**`compute_streaks` semantics**, stated once here and pinned by the unit tests:

- An **Active day** is a day on which at least one lesson was completed (PRD §4.5 — the target is
  one, so membership in the set *is* the target).
- `current` is the length of the run of consecutive days ending at `today`, **or at `today - 1` if
  today has no completion yet** (PRD §4.4 — the streak does not break at midnight, it breaks when
  a whole day passes empty). If neither today nor yesterday is active, `current` is `0`.
- `best` is the longest run anywhere in the input, **including runs that are not the latest** —
  and therefore `best >= current` always, which is an assertion in the tests rather than a
  comment here.
- Input is a set, so duplicates and ordering are structurally impossible to get wrong; days in the
  future (a clock skew, a travelling learner) are not special-cased, and a future day simply
  extends a run. Recorded rather than defended: the alternative — clamping to `today` — would make
  a learner who crosses the date line briefly lose a day they earned, which is worse than briefly
  gaining one.

**`activity_window` semantics:** exactly `days` cells, oldest first, ending at `today`, gaps
zero-filled, days outside the window dropped. It takes counts because the heatmap has three
intensities; `compute_streaks` does not, and the two never share an argument (D2).

### 5.2 The query (`repositories/lessons.py`)

```python
@dataclass(frozen=True)
class CompletionDay:
    path_id: uuid.UUID
    day: date
    count: int

async def completion_days_for_user(
    self, *, user_id: uuid.UUID, tz_offset_minutes: int
) -> list[CompletionDay]: ...
```

```sql
SELECT l.path_id,
       ((l.completed_at AT TIME ZONE 'UTC')
         - make_interval(mins => :tz_offset_minutes))::date AS day,
       count(*) AS n
FROM lessons l
JOIN paths p ON p.id = l.path_id
WHERE p.user_id = :user_id
  AND l.completed_at IS NOT NULL
GROUP BY l.path_id, day
```

Three things about this query are load-bearing:

1. **`AT TIME ZONE 'UTC'` is not decoration** (D3, §14 R1). `completed_at` is `timestamptz`;
   `timestamptz - interval` is still `timestamptz`; and `timestamptz::date` truncates **in the
   session's `TimeZone` setting**. Without the pin, the whole feature's correctness rides on a
   Postgres GUC that nothing in this repository sets, asserts, or documents. `AT TIME ZONE 'UTC'`
   yields a plain `timestamp`, after which the arithmetic and the cast are deterministic.
   `tests/integration/test_progress_api.py` sets `SET TIME ZONE 'America/Chicago'` on the session
   for one case, which fails against the PRD's expression and passes against this one.
2. **Ownership is in the query, not the service.** The `p.user_id` predicate is the same posture
   as `LessonRepository.get_for_user` — another learner's completions cannot enter the fold even
   if a caller forgets to check, because there is no code path where they are fetched and then
   filtered.
3. **The offset is a bound parameter, never interpolated.** It is validated at the DTO boundary
   (`ge=-900, le=900`), but it reaches SQL as a bind either way.

The expression is Postgres-specific (`make_interval`, `AT TIME ZONE`). That is fine and already
true of the codebase — Neon in production, real Postgres in `tests/integration/` — and it is
stated here so nobody discovers it while trying to run a unit test against SQLite.

### 5.3 Service (`services/progress_read.py`)

The `paths_read` shape: module-level frozen views, one async function, `session` first.

```python
@dataclass(frozen=True)
class PathStreakView:
    path_id: uuid.UUID
    current_streak: int
    best_streak: int
    completed_today: int

@dataclass(frozen=True)
class ProgressSummaryView:
    today: date
    current_streak: int
    best_streak: int
    completed_today: int
    activity: list[ActivityCell]
    paths: list[PathStreakView]

async def load_progress_summary(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tz_offset_minutes: int,
    now: datetime | None = None,
) -> ProgressSummaryView: ...
```

- **`now` is the test seam** — `now or datetime.now(UTC)`, then
  `today = (now - timedelta(minutes=tz_offset_minutes)).date()`. Injecting a clock beats freezing
  one; the service's unit tests pass a fixed `datetime` and never touch the module's imports.
  It is keyword-only and defaulted, so no production caller passes it.
- **The fold happens twice over one list.** Global: `{row.day for row in rows}`. Per path: group
  rows by `path_id`, `compute_streaks` each. The activity window sums counts across paths per day
  (the heatmap is global — D5).
- **`completed_today`** is the count of completions on `today`: globally, the summed count; per
  path, that path's count. It is what makes `1 lesson today` render and what the optimistic bump
  keys off (D10).
- **Paths with no completions are absent** (D5). The service does not read the path list to
  zero-fill them, which is the second DB round trip D4 exists to avoid.

The service is the only place that knows what "today" is. The repository takes an offset and
returns dates; the domain takes dates and a `today`; neither derives it. One answer, one owner.

### 5.4 Failure semantics

| Case | Wire result | Learner sees |
| --- | --- | --- |
| Not signed in | `401 unauthenticated` (the shared dependency) | The login redirect, as everywhere |
| `streaks` flag off for the caller | `404 not_found`, before any query runs (D7) | Nothing — the client does not fetch either (§8) |
| `tz_offset_minutes` out of `[-900, 900]` | `422 validation_error` through the shared envelope | Nothing; the client only ever sends `getTimezoneOffset()` |
| No completions at all | `200`, `current_streak: 0`, `activity` all zeros, `paths: []` | `Complete a lesson to start a streak` — an invitation, per PRD §3 |
| Query fails | `500` envelope | The streak line renders nothing; the paths list is unaffected |

That last row is a design constraint on §8, not just a table entry: **the streak line is
decoration on the home screen and must fail as decoration**. A failed summary query must never
prevent the paths list from rendering, because the paths list is the product.

## 6. API design

New router `routers/v1/progress.py`, prefix `/api/v1`, all conventions verbatim (cookie auth,
404-never-403, the `errors.py` envelope). `docs/api.md` gains a `## Progress` section.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/progress/summary?tz_offset_minutes=<int>` | The global streak, the activity window and the per-path breakdown. `tz_offset_minutes` optional, default `0`, `ge=-900 le=900` |

```jsonc
{
  "today": "2026-08-02",
  "current_streak": 5,
  "best_streak": 12,
  "completed_today": 1,
  "activity": [                       // exactly 45 entries, oldest first, zero-filled
    { "date": "2026-06-19", "count": 0 },
    { "date": "2026-06-20", "count": 2 }
  ],
  "paths": [                          // paths with at least one completion; absent means zero
    { "path_id": "…", "current_streak": 3, "best_streak": 7, "completed_today": 1 }
  ]
}
```

`401` unauthenticated · `404` flag off (D7) · `422` out-of-range offset.

DTOs (`dtos/progress.py`): `ProgressSummaryResponse`, `ActivityCellDTO`, `PathStreakDTO`, and the
module-level constrained alias the DTO convention wants:

```python
TzOffsetMinutes = Annotated[int, Field(ge=-900, le=900)]
```

`±900` is 15 hours — wider than any real UTC offset (UTC−12…UTC+14), narrow enough that a
garbage value cannot shift a day boundary arbitrarily far. Mapping is explicit construction, as
`_progress_dto` in `routers/v1/paths.py` does; no `from_attributes`.

**`paths` is a list, not a map.** The PRD's payload showed a list and the frontend indexes it by
`path_id` on arrival; a JSON object keyed by UUID would be marginally cheaper to consume and
noticeably worse to read in a response body, which is what `docs/api.md` is for.

## 7. Load, caching & rate limiting

**No rate limiter.** The endpoint is an unauthenticated-to-nobody, side-effect-free read whose
cost is bounded by the caller's own completion history; there is no model call, no spend, and no
amplification. `services/rate_limit.py` is untouched — recorded because every prior phase added a
knob here, and the honest answer this time is that a knob would exist only for symmetry.

**Fetch frequency** is the real load question. The summary is fetched on the home route, once,
with **no `refetchInterval`** — unlike the paths list, nothing about a streak arrives
asynchronously, so polling it would be pure cost. It refetches on exactly two triggers: a
completion (D10) and TanStack Query's default remount/refocus behaviour. A learner completing ten
lessons in a session issues ten summary queries, each a single grouped scan of their own rows.
That is acceptable and measurable; if it ever isn't, `staleTime` is the knob and it costs nothing
to add later.

**The offset in the key** (`["progress", "summary", tzOffset]`) means crossing a timezone or a DST
boundary produces a cache miss and a refetch rather than a stale day boundary. That is the correct
behaviour and it falls out of the key rather than needing logic.

## 8. Frontend

**`lib/api.ts`** — `ProgressSummary`, `ActivityCell`, `PathStreak` types mirroring §6, plus:

```ts
export const PROGRESS_QUERY_PREFIX = ["progress"] as const;
export function progressSummaryQueryKey(tzOffsetMinutes: number) {
  return [...PROGRESS_QUERY_PREFIX, "summary", tzOffsetMinutes] as const;
}
export function progressSummaryQueryOptions(enabled: boolean) { … }  // skipToken when !enabled
```

`new Date().getTimezoneOffset()` is called at **one** site — inside the options factory — so the
sign convention (D3) has exactly one place to be wrong, and one test that says it isn't.

**Components** (all under `components/`, Nocturne tokens from `tailwind.config.ts`):

- **`streak-line.tsx`** — `🔥 5-day streak · best 12 · 1 lesson today`; at zero,
  `Complete a lesson to start a streak` with no flame and no number. `best` renders only when it
  exceeds `current` — showing `best 5` beside `5-day streak` is noise, and showing `best 12` beside
  a broken streak is the one place this feature could scold, so it is stated as an aim, in `mist`,
  never in the flame's colour. (This surfaces a field PRD §3 did not render — §14, R5.)
- **`activity-strip.tsx`** — the 7×7 week grid (D12), weekday-aligned with leading pad cells,
  three teal intensities (`teal/dim` 1 lesson, `teal` 2–3, `teal/bright` 4+), empty days at
  `surface`. `aria-label` per cell carries the date and count; the grid as a whole is a `role="img"`
  with a summary label, because 45 individually-announced cells is a screen-reader denial of
  service.
- **`streak-chip.tsx`** — the neutral per-path chip, `3-day`, rendered **only at ≥ 2 days**
  (PRD §4.3), in `mist` on `elevated`, no flame, no colour escalation.

**Placement** — home only (§14, R6):

- `routes/index.tsx`: the streak line and the strip above `paths-list`; a chip inside each
  `PathRow`, read from the summary's `paths` array by `path_id` (absent → no chip, D5).
- `routes/paths.$pathId.tsx`: **no chip.** The PRD said the path view carried one beside the
  progress roll-up; the owner's scope call was home-only, and the path view is the busiest polling
  route in the app — adding a consumer of a new query there for a stat PRD §4.3 argues nobody acts
  on is the wrong trade. Recorded rather than dropped: it is one component and one query hook if
  it is wanted.
- `components/sidebar.tsx` `SwitcherSection`: **no chip.** It is secondary chrome that deliberately
  does not poll, and it exists only at `lg` — i.e. not on the phone this feature is designed for.

**Gating** — `useFeatureFlag("streaks")` feeds the options factory's `enabled`; off means
`skipToken`, i.e. no request and no rendered surface. No flag, no fetch.

**The completion moment** (D10) — in `lessons.$lessonId.tsx`'s mutation `onSuccess`:

```ts
// The day's first completion moves the number in this interaction, not a round trip later.
queryClient.setQueryData<ProgressSummary>(progressSummaryQueryKey(tz), (old) => {
  if (!old || old.completed_today > 0) return old;      // not the first today → nothing moves
  const current = old.current_streak + 1;
  return { ...old, completed_today: 1, current_streak: current,
           best_streak: Math.max(old.best_streak, current), … };
});
void queryClient.invalidateQueries({ queryKey: PROGRESS_QUERY_PREFIX });
```

Three properties this has to hold, each a test in §11: it is a **no-op when the cache is cold**
(flag off, or home never visited — an absent payload must not be fabricated); it is a **no-op on
the second completion of a day** (the streak is a day counter, not a lesson counter — PRD §3);
and the refetch that follows is authoritative, so any divergence self-corrects within one round
trip. The activity strip's last cell is bumped by the same patch so the grid and the number never
disagree mid-flight.

The `Day 6 🔥` beat fires off the optimistic value: a brief, non-blocking change of the streak
line, no overlay, no navigation interruption (PRD §3).

**MSW** — `mocks/progress.ts` exporting `progressHandlers`, `configureProgress({…})` and
`resetProgress()`, composed into `handlers.ts` and reset in `tests/setup.ts`, matching the
per-domain shape of `mocks/paths.ts`.

## 9. Instrumentation & observability

**No new event, and `EVENT_FIELDS` is untouched** (D9). The reasoning is PRD §5's own: streak
length is computable from `lesson_completed`, which has carried `account_id` and a timestamp since
Phase 1 — *including for the period before this ships*, which is the part that matters, because it
is the only way to have a before-cohort at all.

W22 and W23 therefore tag no event, which is precedent rather than a gap: W2, W4, W10, W11, W13,
W15, W16, W20 and W21 are all e2e-proved workflows that tag nothing.

**One saved query** — `queries/logfire/streak_return.sql`, alongside `return_rate.sql` and in its
style (a `--` header naming the section, the events used, and the cohort clamp):

> **Does the streak move Return?** The existing Return metric (activated learners back on a 2nd
> distinct day), split into cohorts by whether the account's first activity predates or follows
> the flag flip. The flip date is a constant in the query header — there is no event for it and
> inventing one would be a worse kind of precision than a dated comment.

Its day-bucketing carries the same UTC caveat `return_rate.sql` already states: the metric counts
UTC days while the *feature* counts learner-local days, so the two can disagree by one at the
margins. Making the queries local-day-aware needs a per-account offset that no event carries — a
follow-up, named here rather than silently inherited.

`docs/metrics.md` gains the query row. No event row, because there is no event.

If Return does not move, this slice is decoration and Phase 5's remaining scope should be
re-argued rather than built. That sentence is the PRD's, and it is repeated here because it is the
only thing in this document that could stop the rest of the phase.

## 10. Evals

**None.** This slice generates no content, calls no model, and binds no agent — there is nothing
for the harness to judge. Recorded as a section rather than omitted, because every prior phase TDD
has one and a missing §10 would read as an oversight rather than a decision.

## 11. Testing strategy

Red-green TDD, fakes over mocks (CLAUDE.md).

**Unit — `tests/unit/test_streaks.py`** (pure; the bulk of the value and the cheapest place to buy
it):

| Case | Expected |
| --- | --- |
| Empty input | `(0, 0)` |
| Single active day = today | `(1, 1)` |
| Run of 5 ending today | `(5, 5)` |
| Run of 5 ending yesterday, today empty | `(5, 5)` — PRD §4.4, the grace day |
| Today and yesterday both empty, a 9-run earlier | `(0, 9)` — `best` survives, `current` does not |
| Two runs, the longer one earlier | `best` is the longest, not the latest |
| A single gap day splits one run into two | Both runs measured independently |
| Out-of-order / duplicate input | Structurally impossible (a set) — asserted so the signature stays a set |
| Any input | `best >= current` |
| `activity_window` | Exactly `days` cells, oldest first, ending at `today`, zero-filled, out-of-window days dropped |

**Unit — service, against a fake repository behind a `Protocol`:** two paths active on the same
day fold to **one** global active day; per-path folds are independent; `completed_today` sums
globally and splits per path; a path present in `paths` but with no completions today reports
`completed_today: 0`; the injected `now` + offset produce the expected `today` in both hemispheres.

**Unit — the sign convention (D3),** the test this feature most needs: a completion stamped at
`23:30 UTC` is **tomorrow** for a learner at UTC+2 (`tz_offset_minutes = -120`) and **today** for
one at UTC−5 (`+300`). Both directions, named in the test, because "off by a day for one
hemisphere" is the failure mode that ships quietly.

**Integration — `tests/integration/test_progress_api.py`** (real Postgres), `@pytest.mark.workflow("W22")`
/ `("W23")` on the relevant cases:

- The endpoint against seeded completions across several days and two paths.
- `tz_offset_minutes` shifts a day boundary end to end.
- **The session-`TimeZone` case (D3, §14 R1):** `SET TIME ZONE 'America/Chicago'`, then assert the
  same payload as under UTC. This is the test that distinguishes the shipped expression from the
  PRD's.
- Another learner's completions never appear (the `p.user_id` predicate).
- Re-completing a lesson does not change the payload (D8's inherited idempotence).
- Applying and undoing a shaping Change leaves the payload untouched (D8's other half — Undo never
  touches progress, Phase 2B §5.7).
- Deleting a path removes its days from the global streak — **pinning PRD §4.6's accepted wart so
  it is a decision with a test, not a bug waiting to be filed.**
- Flag off → `404`; out-of-range offset → `422`.
- `test_schema.py` sees the new index (model/DDL agreement), `test_migrations.py` runs `0009`
  up and down.

**Frontend unit (vitest + MSW):** streak line at zero / at one / at many / with `best` above
`current`; chip hidden at 0 and 1 day, shown at 2; strip cell count, weekday alignment, intensity
buckets, and the `role="img"` labelling; the flag off → no request (assert via MSW's
`onUnhandledRequest: "error"` posture); and in **`src/app/completion-refresh.test.tsx`**, the three
D10 properties — cold cache no-op, second-completion-of-the-day no-op, first-completion bump
followed by an authoritative refetch.

**E2E (Playwright, `mobile-390x844`)** — `journeys/w22.spec.ts` (`@w22`) and `w23.spec.ts` (`@w23`),
each with the `//` prose header the existing journeys carry:

- **W22 — completing a lesson visibly advances the streak.** Create a path, complete the day's
  first lesson, assert the home streak line increments **in the same interaction** (the optimistic
  bump is what makes this assertable without a network wait), then assert it survives a reload —
  which is what proves the server agrees.
- **W23 — a streak survives a missed day boundary but breaks on a missed day.** Complete a lesson,
  `shiftCompletions({ pathId, days: 1 })`, reload → still `1-day streak` (PRD §4.4). Shift again by
  2 → `Complete a lesson to start a streak`.

**The clock (D11).** `scripts/e2e_backend.py` mounts a router the production factory never
constructs:

```python
# create_stub_app only — create_app has no reference to this module.
@e2e_router.post("/__e2e__/shift-completions")
async def shift_completions(body: ShiftRequest, session: Session) -> None:
    """Backdate a path's completions so a journey can observe yesterday."""
```

`UPDATE lessons SET completed_at = completed_at - make_interval(days => :days)
WHERE path_id = :path_id AND completed_at IS NOT NULL`. A *shift* primitive rather than a seeder:
it fabricates no lessons, needs no knowledge of the schema beyond one column, and cannot put the
database into a state the real app could not reach. A unit test asserts the production app exposes
no `/__e2e__` route — the guarantee is that the router is never imported, and the test is what keeps
that true.

**External:** none. Nothing here talks to a provider.

## 12. Deployment & ops

No new secrets, services, or `fly.toml` changes. Migration `0009` is one partial index —
additive, online-safe, and reversible by dropping it. Launch is one committed
`FEATURE_FLAG_DEFAULTS` entry, following the flagged-phase runbook
([`deploy.md`](../deploy.md#launching-a-flagged-phase-al-270--al-370)); until then the flag is on
for admins only (`ADMIN_DEFAULT_FLAGS`) and the endpoint 404s for everyone else.

Rollback is unusually clean, and it is worth saying why: **there is no data to lose.** D1's whole
payoff shows up here — no table to drop, no backfill to unwind, no reconciliation. Turning the
flag off returns the product to exactly its prior state, and dropping the index returns the
database to it.

## 13. Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `STREAK_ACTIVITY_WINDOW_DAYS` | `45` | The strip's window (§8, D12). The only knob this slice adds |
| `FeatureFlag.STREAKS` | `False` in `FLAG_DEFAULTS`, present in `ADMIN_DEFAULT_FLAGS` | D7; launch flips it via `FEATURE_FLAG_DEFAULTS` |

No model slot, no timeout, no semaphore, no rate limit. This table being short is the point of D1.

## 14. Corrections to the PRD's technical sections

The PRD carried §§5–13 as a combined design before the split. Splitting it surfaced six places where the shipped
design differs from what that text said. Each is listed here so the difference is a record rather
than a quiet rewrite; the PRD's remaining §§1–4 are unaffected except where noted.

| # | Correction | Where |
| --- | --- | --- |
| **R1** | **`AT TIME ZONE 'UTC'` added to the day expression.** The PRD's `(l.completed_at - make_interval(…))::date` is correct only while the session's `TimeZone` GUC is UTC, because `timestamptz::date` truncates in that setting — nothing in this repository sets, asserts, or documents it. Pinning to UTC first makes the arithmetic independent of server configuration, which is the entire premise of taking the offset from the request. An integration test under `America/Chicago` is the guard | D3, §5.2, §11 |
| **R2** | **`paths` omits paths with no completions.** The combined doc's §7 promised "one entry per live path, including 0-streak paths", which its own §6 `GROUP BY` over `lessons` cannot produce. Absent now means zero, and it costs no pixel: the chip is hidden below 2 days anyway. The alternatives were a `LEFT JOIN` that spoils the index's usefulness on its outer side, or a second query on an endpoint D4 justified partly by being one | D5, §6 |
| **R3** | **`progress_summary_viewed` dropped.** It would have fired per GET, and §8 refetches the summary on every completion — so the event would mostly have counted invalidation refetches while reading like engagement. `lesson_completed` answers the only question PRD §5 actually poses, retroactively. `EVENT_FIELDS` is untouched | D9, §9 |
| **R4** | **`best_streak` is all-time, so the query is unbounded by the 45-day window.** The PRD's payload implied both without reconciling them. Stated explicitly with its growth ceiling (~1 row per path-day, forever) rather than discovered later | §4 |
| **R5** | **`best_streak` renders.** The PRD shipped it in the payload and showed it nowhere. It now appears on the streak line — in `mist`, only when it exceeds the current streak, so it reads as an aim rather than a scoreboard of a broken run. **This changes PRD §3** | §8 |
| **R6** | **Chips are home-only.** PRD §3 gave the path view a chip beside its progress roll-up. Scoped to the home list: the path view is the busiest polling route in the app, and §4.3's own argument is that this stat is not one anybody acts on. **This changes PRD §3**; re-adding it is one component and one hook | §8 |

## 15. Risks & open questions

- **The timezone sign convention is the highest-consequence, lowest-visibility surface here.**
  Getting it backwards is invisible in the developer's own hemisphere and wrong by a day for half
  the world. The mitigations are structural rather than careful: one call site for
  `getTimezoneOffset()`, one owner of "today" (the service), one expression in SQL, and a unit
  test that names both hemispheres. If a report ever suggests a day is off, that test is where to
  start and it should be *made to fail* before anything is changed.
- **Deleting a path erases its days** (PRD §4.6). This is the real price of D1 and it is accepted,
  untold to the learner (the owner's call on PRD open Q2), and pinned by a test. The escape hatch
  is designed and additive: add a `streak_days` ledger, backfill it from `lessons.completed_at`
  — which is lossless, because derivation *is* the current definition — and swap the repository
  method behind an unchanged service interface. Nothing above the repository moves. That is the
  whole reason to start derived.
- **The optimistic bump can be briefly wrong** (D10): two devices, or a completion that races the
  server's own day boundary. Bounded by the refetch that immediately follows, and the divergence
  is one integer nobody is watching closely. If it ever reads badly, dropping to authoritative-only
  is deleting a function.
- **A travelling learner can gain or lose a day.** The offset is a property of the request (D3),
  so crossing timezones moves the boundary. Accepted; an account timezone column is a second
  source of truth that goes stale and needs a settings UI to fix, which is a worse trade at this
  scale.
- **`streak_return.sql` counts UTC days while the feature counts local days** (§9). The two can
  disagree at the margins, and closing the gap needs a per-account offset no event carries. Named,
  not fixed.
- **Open: does the path streak earn its place?** (PRD open Q3.) It is nearly free given D5, and it
  now has exactly one surface (§8). A few weeks of dogfooding decides; removing it is deleting one
  component and one field.
- **Open: is 45 the right window?** D12's grid makes 49 the natural number (7×7) and renders 45 of
  them. Either move the window to 49 and let the grid be exactly full, or keep 45 and accept four
  pad cells. This is a design question to settle before step 4 is built, not a
  technical one.
- **Open: does the streak line survive its own success?** PRD §2's honest counter-argument stands
  — Phases 3 and 4 produce the reasons to return that a streak merely counts. §9's Return
  comparison is the answer, and it is the one result that should be allowed to stop the rest of
  Phase 5.

## 16. Tickets

GitHub issues, cut from this document in a follow-up PR — issues are the source of truth (the
Phase 1 / 2 / 2B pattern).

- **Label:** `tdd-streaks`; parent epic carrying shared context and the dependency graph.
  `for-ai` / `for-human` split as before — expected `for-human` surface: the strip's
  geometry (§15's open window question) and the production ship verification.
- **Numbering:** AL-4xx, in dependency order.

| # | Commit | Scope |
| - | ------ | ----- |
| 0 | `docs:` | `CONTEXT.md` — **Daily streak**, **Path streak**, **Active day**, **Best streak** into *Progress & structure*; amend the phase-boundary bullet to "goal ring / daily minutes — Phase 5; **streaks shipped early**". `roadmap.md` — the Phase 5 sentence recording the pull-forward, as Phase 2's paragraph records 2B's. **Precedes the code**: the vocabulary is authoritative |
| 1 | `feat:` | `domains/streaks.py` + `tests/unit/test_streaks.py`. Pure, no dependencies, mergeable alone |
| 2 | `feat:` | Migration `0009` + the model's `__table_args__` + `completion_days_for_user` + its integration tests (including the `TimeZone` case) |
| 3 | `feat:` | `dtos/progress.py`, `services/progress_read.py`, `routers/v1/progress.py`, `FeatureFlag.STREAKS`, the API integration tests, `docs/api.md` |
| 4 | `feat:` | Frontend: api client, `streak-line`, `activity-strip`, `streak-chip`, home wiring, the D10 cache work, msw handlers, component tests |
| 5 | `feat:` | `queries/logfire/streak_return.sql` + `docs/metrics.md` |
| 6 | `test:` | The `/__e2e__` stub router + `shiftCompletions` fixture + W22/W23 |

Steps 1 and 2 are parallel; 3 depends on both; 4 depends on 3; 5 and 6 are independent of each
other. Launch (the flag flip) is a separate change after dogfooding.

**Rough size:** ~700 lines of production code and ~600 of tests, most of it the frontend. The
backend is genuinely small — that is the point of D1.

## Appendix — traceability (PRD's TDD-owned items)

| PRD delegation | Here |
| --- | --- |
| A day is the learner's local calendar day (§4.1) | D3, §5.2, §5.3, §11 |
| Completion is the only signal (§4.2) | D1, §5.2 — the query's `completed_at IS NOT NULL` is the whole predicate |
| The path streak is a stat, not a game (§4.3) | §8 — hidden below 2 days, `mist` not teal, one surface |
| The streak does not break at midnight (§4.4) | §5.1's `current` semantics, its two unit cases, W23 |
| The daily target is one lesson (§4.5) | D2 — the target is set membership, so there is no threshold to drift |
| Deleting a path erases its days (§4.6) | §11's pinning test, §15's escape hatch |
| What a learner sees (§3) | §8; changed by R5 and R6 |
| Restraint — never a push, a warning, a freeze, a leaderboard (§3) | Nothing in §6 or §8 can express any of them; the endpoint is a `GET` and the client has no scheduler |
