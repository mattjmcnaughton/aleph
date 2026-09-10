# TDD — Flow

> **Status: plan, ready to build.** This document is the implementer's brief. It is written for an
> engineer (or agent) starting cold: it carries every decision already taken, every codebase fact the
> work depends on, and the ticket order. Where it says *settled*, the owner has decided; do not
> re-open it. Where it says *decided here*, the author chose and the owner can overturn it in
> review. The companion drawing is [`docs/mocks/aleph-flow-mode.html`](../mocks/aleph-flow-mode.html);
> where this document and the mock disagree, this document wins.

## 0. Brief for the implementer

**What Flow is.** Today every finished lesson ends in a choice — *Next lesson*, *Back to your
path*, *Home* (`routes/lessons.$lessonId.tsx`, `CompletedState`). A **Flow** is a bounded run of
lessons where that choice was made once, at the door: the learner picks how many lessons and from
which paths, and then each completed lesson opens the next on a short count. It ends when the count
is reached, when every chosen path runs dry, or when the learner ends it — and it lands on a receipt.

**What it is not.** Not a new kind of lesson, not a queue, not a schedule. Every completion inside
a flow is an ordinary **Mark complete**; streaks, Active day, Activation, and every metric are
untouched. Nothing about a flow is stored on the server.

**Read before writing anything** (in this order):

1. [`CLAUDE.md`](../../CLAUDE.md) — commands, layering, commit conventions.
2. [`docs/CONTEXT.md`](../CONTEXT.md) — the vocabulary is authoritative. Use **path** (never
   "course"), **lesson**, **Quick check**, **Mark complete**, **Draft**, **Kept card**, **Session**,
   **Prefetch (+N)**. Ticket 0 adds **Flow**, **Flow length**, **Flow scope**.
3. [`docs/mocks/aleph-flow-mode.html`](../mocks/aleph-flow-mode.html) — open it in a browser; the
   four screens are the visual spec and screens 02/03 are interactive.
4. [`docs/architecture.md`](../architecture.md) §Frontend — the CSS-only shell rule (no
   `matchMedia`, no width-conditional rendering), the one Markdown renderer, the three "rail" names.
5. This document, then the files named in §3 as you reach each ticket.

**How to work.** Red-green TDD, fakes over mocks (MSW handlers in `src/mocks/` are the fakes).
`just gate` must be green before every push; `just test-e2e` needs Keycloak
(`just compose-keycloak-up`). Conventional Commits, one commit per ticket; `feat`/`fix` deploy,
`docs`/`test`/`chore` do not. Do not put a model name in any commit or artifact.

## 1. Decision record

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| D1 | Where a flow lives | **100% client-side.** *Settled by the owner.* No table, no route, no migration. A flow is a small record in the browser (D4). | Every completion is already an ordinary `POST /lessons/{id}/complete`; the server needs nothing else to keep streaks and progress right. |
| D2 | Scope is set at the door | Length, paths and order are chosen on `/flow/new` and never change mid-flow. The only in-flow control is **End flow**. Widening is offered at the end (*Go again*). | The Phase 3 rule for review scope (Phase 3 PRD §4.10), reused: "3 of 5" is a contract, and the denominator does not move under the learner. |
| D3 | Orders across paths | Three: **Interleave** (default; paths take turns in selection order), **Random** (a path drawn afresh for every lesson), **One path at a time** (finish the first before touching the next). *Random settled by the owner.* Within a path the order is always Progression's: that path's `next_lesson`. | Interleaving retains better (the argument the global review queue was built on). Random is the owner's ask. A path's lessons are linear by definition — Random picks the *path*, never a lesson out of order. |
| D4 | State container | `sessionStorage`, one key (`aleph.flow.v1`), one JSON record (§4), behind a typed module `lib/flow.ts` with `useSyncExternalStore`. *Decided here.* | Path ids are UUIDs and the receipt needs the list of completed lesson ids — too much for a readable URL. `sessionStorage` is per-tab and dies with the tab, which is what "a flow is a Session" means. First use of web storage in the codebase — see §4 for the rules that keep it honest. |
| D5 | When a flow ends | Reaching the length; every scoped path dry; **End flow**; or leaving — any navigation to a route other than `/lessons/*` or `/flow/*` clears the record (§5.4). | A flow that silently survives a trip to home would resume itself hours later. Leaving *is* ending. |
| D6 | The advance | After a successful Mark complete, a **5-second count** with **Go now** and **Pause**; at zero the next lesson opens. The count is paused while the tutor rail is open and when the completion finished its path. | The count exists so the Quick check explanation can be read. Flow removes the *choice*, not the *completion*: Mark complete stays the learner's tap. |
| D7 | Drafts wait for the end | Auto-draft keeps drafting as each lesson opens (unchanged). The per-lesson `DraftList` is **not rendered** inside a flow; the receipt shows every drafted set from the flow as one batch, kept per lesson. An ended-early flow still gets the batch. | The keep/discard moment would break the one thing a flow is for. Drafts are re-served on any later fetch (api.md: abandoned drafts wait), so nothing is lost by deferring. |
| D8 | Generation look-ahead | The client pre-draws the *next* lesson when the current one opens and fires the existing poll-as-trigger, `GET /lessons/{nextId}`, once — via `queryClient.prefetchQuery(lessonQueryOptions(nextId))`. No `POST …/generate`. *Decided here.* | Backend Prefetch (+N) is per-path (`services/generation.py`, `ensure_prefetch_window`); an interleaved flow's next lesson is on a *different* path. The GET is idempotent, spawns the resume, refills that path's window, and is not counted by the generation rate limit the way `POST …/generate` is. |
| D9 | A failed or stalled lesson mid-flow | The lesson route renders its existing `FailedState`/`StalledState`; the flow bar stays; nothing auto-skips. | api.md §Lessons documents the "complete past a failed head" dead-end. An auto-advancing flow must never walk into it. |
| D10 | Feature flag | Register `FeatureFlag.FLOW` **dark** (`FLAG_DEFAULTS[FLOW] = False`, in `ADMIN_DEFAULT_FLAGS`) and gate purely in the client with `useFeatureFlag("flow")`. Launch is a later one-line flip. *Decided here — the one backend touch; three lines, no data, no route.* | Every launched surface ran the dark-then-flip playbook (deploy.md §Launching a flagged phase) and keeps a kill switch. This is the first client-only gate (no router `404`); acceptable because there is no router. |
| D11 | Due cards | Never inside a flow. The receipt links to `/review` when cards are due. | Lessons and reviews are counted, scheduled and celebrated differently; a flow is the wrong first place to blur them. |
| D12 | Analytics | No new event. | There is no client event ingest (metrics.md); `lesson_viewed`/`lesson_completed` already reconstruct flow-shaped bursts within a Session. Adding an event would need a purpose-built route — out of scope. |
| D13 | Length options | `3`, `5`, `8`, **Until I stop** (`null`). No minutes estimate. *Settled by the owner.* | Aleph has no per-lesson duration; a made-up estimate is a small lie. |
| D14 | The setup surface | A route, `/flow/new` (with `?path=` to preselect), not an overlay. | There is no dialog/sheet primitive in the codebase (the rail column is the only bottom-sheet and it is tied to `Workspace`'s rail slot). A route is deep-linkable, testable through the real router like every other surface, and reads as a sheet on a phone anyway. |

## 2. Extension map

| Concern | Existing asset | Flow change |
| --- | --- | --- |
| Flag registry | `src/aleph/services/feature_flags.py` — `FeatureFlag(StrEnum)` :87, `FLAG_DEFAULTS` :123, `ADMIN_DEFAULT_FLAGS` :169 | Add `FLOW = "flow"`, default `False`, admin-on. Pin with a unit test beside `test_streaks_is_registered…` in `tests/unit/test_feature_flags.py`; add `flow_flag_enabled`/`flow_flag_disabled` fixtures to `tests/integration/conftest.py` following `_enable_flag_globally` (:514). |
| Flag on the client | `src/lib/feature-flags.ts` — `useFeatureFlag(key)` reads the session probe; unknown ⇒ `false` | Call sites use the literal `"flow"` (a `const FLOW_FLAG = "flow"` in `lib/flow.ts`, the `use-tutor-rail.ts:64` precedent). |
| Paths list | `GET /api/v1/paths` → `PathSummary[]` (`lib/api.ts` :280): `next_lesson: {id,title,position_in_path} \| null`, `progress.{total_lessons,completed_lessons}`, `status`; `pathsListQueryOptions` :436 (a value, key `["paths","list"]`) | The **only data a flow needs**: eligibility (`status === "ready" && next_lesson !== null`), "N left" (`total_lessons − completed_lessons`), and the next lesson per path. Never a second fetch shape. |
| Lesson route | `src/routes/lessons.$lessonId.tsx` — `completeMutation.onSuccess` :238–320 (patches cache, invalidates `PATHS_QUERY_PREFIX`), `CompletedState` :859 (rendered :657 when `unlock_state === "complete"` and no `pathCompletion`), `nextLesson` :373, `DraftList` gate `draftsEnabled` :139–154, `useTutorRail` :382, `Breadcrumbs` at the top of `<Workspace>` | Read the flow; render `FlowBar` above the breadcrumbs; render `FlowAdvanceCard` in place of `CompletedState` when a flow is active; suppress `DraftList` when a flow is active (drafting still triggers on open); fire the look-ahead prefetch (D8); clear the flow if this lesson is not the flow's current lesson (§5.4). |
| Completion → next | `completeLesson(id)` :620 → `LessonCompleted{unlock_state, path_completion}` | After success, `await queryClient.fetchQuery(pathsListQueryOptions)` (fresh — the list's `next_lesson` has just moved) then `advance()` (§5.3). |
| Look-ahead trigger | `lessonQueryOptions(id)` :635; `GET /lessons/{id}` is itself the generation trigger (`isLessonViewTerminal` docstring :649) | `queryClient.prefetchQuery(lessonQueryOptions(nextId))` once per current lesson. |
| Home | `src/routes/index.tsx` — `ContinueCard` + `pickResumeTarget` (`components/continue-card.tsx`) | `FlowDoor` link directly under `ContinueCard`, rendered when the flag is on and at least one path has a `next_lesson`. |
| Path view | `src/routes/paths.$pathId.tsx` — desktop `ContinueCard` :411 with `path-continue-link` | A quiet `Start a flow` link to `/flow/new?path=$pathId` beside it (both widths). |
| Drafts | `POST/GET /lessons/{id}/flashcard-drafts`, `POST …/keep`; `flashcardDraftsQueryOptions(lessonId, enabled)` :1024; `components/review/draft-list.tsx` `DraftList({drafts,onKeep,keeping,keepErrored,onRetry,retrying,triggerRateLimited,triggerErrored,onDraft,drafting})` | Receipt renders one `DraftList` per completed lesson that has drafts (§5.5). Not a new component; a new *container* that owns the per-lesson queries and keep mutations. |
| Settings | `useSettings().auto_draft_flashcards` (`lib/settings.ts`) | Unchanged. With Auto-draft off, the receipt's per-lesson list offers `Draft flashcards` exactly as the lesson does (`onDraft`). |
| Shell | `components/workspace.tsx` — `Workspace({testid,width,sidebar,tutorRail})`, `WIDTH_CAP.lesson` | `/flow/new` and `/flow/done` use `<Workspace width="lesson">` with no sidebar, no rail. |
| CTAs | `components/state-card.tsx` — `PRIMARY_CTA_BASE`, `PRIMARY_CTA`, `SECONDARY_CTA` | Reuse; no new button styles. The flow bar's segments and the count ring are the only new visual elements (mock screen 03). |
| Router | TanStack file routes; `routeTree.gen.ts` is generated by `tsr generate` inside `typecheck`/`build`, git-ignored | Two new files, `src/routes/flow.new.tsx` and `src/routes/flow.done.tsx`. Nothing to register by hand. Search params via the house `validateSearch` shape (`routes/review.tsx:23`). |
| Unit tests | `src/app/*.test.tsx` render `<App/>` after `window.history.pushState`; MSW fixtures in `src/mocks/paths.ts` (`seedPath`, `FRESH_PATH_UNITS`, `MID_PATH_UNITS`) and `src/mocks/lessons.ts` (`seedLesson`); flags set by overriding the session handler (see `flashcards-drafts.test.tsx:23`) | New `src/app/flow-setup.test.tsx`, `flow-lesson.test.tsx`, `flow-receipt.test.tsx`; `tests/setup.ts` gains `window.sessionStorage.clear()` in `afterEach`. |
| E2E | `tests/e2e/journeys/w*.spec.ts`, phone project runs journeys; helpers in `tests/e2e/fixtures/journey.ts` | One journey, **W32** (next free tag after W31), on `mobile-390x844`. |
| Docs | `docs/CONTEXT.md`, `docs/roadmap.md` | Ticket 0 (§8). |

## 3. Architecture overview

```
routes/index.tsx ──────────── FlowDoor ─────▶ routes/flow.new.tsx  (setup: length · paths · order)
routes/paths.$pathId.tsx ──── Start a flow ─▶        │ startFlow(record) → sessionStorage
                                                     ▼
                                    routes/lessons.$lessonId.tsx   ◀─── advance() navigates here
                                      ├─ FlowBar        (k of n · segments · End flow)
                                      ├─ …existing lesson states, unchanged…
                                      ├─ FlowAdvanceCard  (replaces CompletedState in a flow)
                                      │     count 5→0 · Go now · Pause · End flow
                                      └─ DraftList suppressed in a flow (D7)
                                                     │ length reached / scope dry / End flow
                                                     ▼
                                    routes/flow.done.tsx  (receipt: stats · where it landed ·
                                                          drafts batch · Go again · Home)
lib/flow.ts        — the record, the store (sessionStorage + useSyncExternalStore), start/advance/end
lib/flow-order.ts  — pure: eligible(paths) · pickNextPath(flow, eligible, rng) · leftIn(path)
```

Every arrow in that diagram is a plain `navigate()`; there is no global provider. The lesson route
is the only consumer that *mutates* the record mid-flow, and it does so in exactly two places:
when it opens (look-ahead draw, D8) and when Mark complete succeeds (advance, §5.3).

## 4. The record (`lib/flow.ts`)

```ts
export const FLOW_FLAG = "flow";
export const FLOW_STORAGE_KEY = "aleph.flow.v1";
export const FLOW_LENGTHS = [3, 5, 8] as const;           // `null` = until I stop (D13)
export const FLOW_ADVANCE_SECONDS = 5;                     // D6

export type FlowOrder = "interleave" | "random" | "serial";

export interface FlowCompletion {
  lessonId: string;
  pathId: string;
  title: string;                 // lesson title, for the receipt's ledger
  outcome: "correct" | "incorrect" | null;   // the Quick check outcome, for "4/5"
}

export interface FlowRecord {
  version: 1;
  startedAt: string;             // ISO; receipt only
  length: number | null;         // Flow length; null = until I stop
  order: FlowOrder;
  paths: string[];               // Flow scope: path ids in the order the learner picked them
  cursor: number;                // interleave/serial: index into `paths` of the path to draw from next
  current: { lessonId: string; pathId: string } | null;   // the lesson the flow is on
  next: { lessonId: string; pathId: string } | null;      // pre-drawn look-ahead (D8), or null
  completed: FlowCompletion[];   // in order; `completed.length` is "k" in "k of n"
  endedReason: "length" | "dry" | "ended" | null;         // set once; the receipt reads it
}
```

**Store rules.**

- `readFlow(): FlowRecord | null` parses and validates (`version === 1`, arrays of strings, etc.);
  anything malformed is treated as `null` *and removed*. Wrap every `sessionStorage` access in
  `try/catch` — a browser with storage blocked must behave as "no flow", never throw.
- `writeFlow(record)`, `clearFlow()`. Both notify subscribers; `useFlow()` is
  `useSyncExternalStore(subscribe, readFlow, () => null)`. Subscribers are in-tab listeners plus the
  `storage` event (harmless; `sessionStorage` is per-tab).
- `startFlow({ length, order, paths }, summaries)` builds the record, draws the first `current`
  (§5.2 with `cursor = 0`), pre-draws `next`, writes, and returns the first lesson id for navigation.
  Throws if no path is eligible — the sheet's button is disabled in that state, so this is a guard.
- Nothing else writes storage. All state transitions are pure functions over `FlowRecord` in
  `lib/flow-order.ts`, returning a new record; the lesson route calls them and then `writeFlow`.

## 5. Behaviour

### 5.1 Eligibility and "N left" (`lib/flow-order.ts`, pure)

```ts
export function eligiblePaths(summaries: PathSummary[], scope: string[]): PathSummary[]
// scope order preserved; keeps p where p.status === "ready" && p.next_lesson !== null
export function lessonsLeft(p: PathSummary): number
// p.progress.total_lessons - p.progress.completed_lessons
```

A Beat is not a path and never appears (the setup sheet reads `GET /paths` only). A finished path
(`next_lesson === null`) is **listed but disabled** on the sheet with the caption
"Finished — nothing left to flow", and is excluded from *Select all* (mock screen 02, pin 3).

### 5.2 Picking the next path (`lib/flow-order.ts`, pure)

```ts
export function pickNext(
  flow: FlowRecord,
  eligible: PathSummary[],          // already filtered to flow.paths ∩ eligible, scope order
  rng: () => number,                // injected; Math.random in the app, seeded in tests
): { next: { lessonId: string; pathId: string }; cursor: number } | null
```

- `null` when `eligible` is empty (scope dry → end with `endedReason: "dry"`).
- **interleave:** starting at `flow.cursor`, take the first path in scope order (wrapping) that is
  eligible; new `cursor` = that path's scope index + 1.
- **serial:** the first eligible path in scope order; `cursor` = its scope index.
- **random:** uniform draw over `eligible`; when two or more are eligible, exclude the path of
  `flow.current` so the same path is never drawn twice running. (A single eligible path is drawn
  every time — that is not a bug, the scope has run down to it.)

The chosen lesson is always `path.next_lesson.id` — Progression's next available lesson.

### 5.3 The advance (lesson route)

On `completeMutation.onSuccess`, after the existing cache surgery, when `useFlow()` is non-null and
`flow.current?.lessonId === lessonId`:

1. Append `{ lessonId, pathId, title: detail.title, outcome: detail.attempt?.outcome ?? null }` to
   `completed`.
2. `const list = await queryClient.fetchQuery(pathsListQueryOptions)` — a *fresh* list, because the
   list's `next_lesson` for this path moved on the completion the server just recorded.
3. If `length !== null && completed.length >= length` → `endedReason = "length"`, write, navigate
   to `/flow/done`.
4. Else resolve the next: prefer `flow.next` if it is still eligible in `list` (the pre-drawn
   look-ahead); otherwise `pickNext`. If `null` → `endedReason = "dry"`, write, navigate to
   `/flow/done`.
5. Else set `current = next`, `next = null`, write — and show `FlowAdvanceCard`. The card owns the
   count (`FLOW_ADVANCE_SECONDS`, `setInterval` 1 s, cleared on unmount; honours
   `prefers-reduced-motion` by dropping the ring animation, never the count). At zero, or on
   **Go now**, `navigate({ to: "/lessons/$lessonId", params: { lessonId: current.lessonId } })`.
   **Pause** halts the interval and flips to *Resume*. The count starts **paused** when
   `result.path_completion !== null` (the path-complete celebration deserves the tap) and while
   `tutor.open` is true. Its own **End flow** control (`flow-advance-end`) reads exactly that, not
   "back to your path" — `onEnd` always lands here on the receipt, since a completion has just been
   recorded (`completed.length > 0`, §5.7) — matching the flow bar's own label.

The steps above happen in `onSuccess` because a completion that succeeded but whose advance was
interrupted (tab closed) must leave the record consistent: `completed` written before navigation.

### 5.4 Look-ahead and leaving (lesson route effects)

- **On open** (effect keyed by `lessonId`): the record is on-track here when `flow.current?.lessonId
  === lessonId` (the working case) *or* when `lessonId` is the **last** entry of `flow.completed` —
  the advance screen, right after Mark complete succeeded, where `current` has already moved on to
  the next lesson while this route is still mounted on the one that just finished. On the
  advance-screen branch, do nothing (no clear, no draw) — a reload there must not end the flow. Any
  other mismatch still `clearFlow()`s — the learner navigated somewhere the flow did not send them
  (path rail, sidebar, browser back to an *earlier* completed lesson); the flow is over (D5). Only
  the *last* completion is ever on-track. Otherwise, if `flow.next === null`, draw it with `pickNext`
  against the cached `pathsListQueryOptions` data (the fresh fetch in §5.3 already primed it,
  including once that fetch's own `pathsListQueryOptions` query has landed for the first time on a
  cold open — the effect also re-runs when that happens, not only on a `lessonId` change). If the
  draw lands on `current`'s own path, write `next: null` (the cursor still advances) rather than the
  picked lesson — a same-path draw is just the lesson already on screen, not a look-ahead: D8's reason
  for pre-drawing the next lesson is that an interleaved flow's next lesson is on a *different* path,
  and same-path is already covered by backend Prefetch (+N). Otherwise write the picked lesson and
  `queryClient.prefetchQuery(lessonQueryOptions(flow.next.lessonId))` (D8). Fire-and-forget; never
  awaited, never surfaced.
- **Root layout** (`routes/__root.tsx`, `RootLayout`): an effect on `useLocation().pathname` calls
  `clearFlow()` when a flow exists and the path is neither `/lessons/…` nor `/flow/…`. This is what
  makes *Home* in the header, sign-out, or a typed URL end a flow.
- `/flow/new` always starts fresh (overwrites any record). `/flow/done` reads the record and clears
  it when the learner leaves via *Home* or *Go again* (the root effect covers the former; *Go again*
  navigates to `/flow/new` with the previous `length`/`order`/`paths` as search params so the sheet
  is prefilled — see §6).

### 5.5 The receipt (`/flow/done`)

Reads the record. If none, redirect to `/`. Renders, in order (mock screen 04):

- Kicker `Flow complete` (or `Flow ended` when `endedReason === "ended"`, `Flow ran dry` when
  `"dry"`); headline names the count in words for 1–9, digits above.
- Three stats: lessons, paths (distinct `pathId`s in `completed`), Quick checks
  (`correct / with an outcome`; hidden if no completion had an outcome).
- `StreakLine` fed by `progressSummaryQueryOptions(streaksEnabled)` — decoration, unchanged contract.
- "Where it landed": one row per path — title from the paths list, `k lessons · done of total`.
- Drafts batch (D7): for each `completed` lesson, `useQueries` over
  `flashcardDraftsQueryOptions(lessonId, flashcardsEnabled)`; render a `DraftList` per lesson with
  drafts in state `generated` (keep mutation per lesson, same as the lesson route's). Header
  `Aleph drafted N cards` sums `cards.length`. With Auto-draft off, lessons with `not_started`
  drafts render the `Draft flashcards` affordance (`onDraft`). Nothing here while `completed` is
  empty.
- Doors: **Go again · n more** (primary; `n = length ?? completed.length || 3`), **Home**
  (secondary). If `reviewSummary.due_count > 0`, a quiet link "N cards due · Review" (D11).

### 5.6 The setup sheet (`/flow/new`)

Search: `{ path?: string; length?: string; order?: string; paths?: string }` (all optional;
`paths` comma-separated — used only by *Go again*). Reads `pathsListQueryOptions`.

- **How many lessons** — four segmented buttons `3 · 5 · 8 · Until I stop`, `aria-pressed`;
  default `5`.
- **From which paths** — one toggle row per path (`aria-pressed`), showing title, `Next: <title>`,
  `N left`; finished paths disabled. **Select all** text button in the header, flipping to
  **Clear** when every selectable path is selected. Default selection: `?path=` if given, else the
  `pickResumeTarget` path, else the first eligible.
- **Order** — three stacked radio-styled buttons, shown only when two or more paths are selected;
  default Interleave.
- Footer: `First up: <title>` (for Random with ≥2 paths: "whichever path the draw lands on");
  primary button **Start flow · n lessons** where `n = min(length, Σ left)` with the suffix
  ` (all that's left)` when capped, or **Start flow · until I stop**; disabled and reading
  `Pick a path to start` with nothing selected. **Not now** link → `/`.
- Start: `startFlow(...)` then navigate to the first lesson.
- Empty state (no eligible path at all): a `StateCard` saying so with a *New path* link.

The mock's screen 02 script is the reference for the button copy rules; port its behaviour, not
its DOM.

### 5.7 The flow bar (lesson route)

`FlowBar` sits above `Breadcrumbs`, inside the lesson `main`, sticky under the app header (reuse
`--app-header-h`). It takes a 1-based `position`, computed in the route as `flow.completed.length +
(this lesson is the last entry of completed ? 0 : 1)` (the same on-track check as §5.4) — not
`completed.length` directly, which read `Flow · 0 of 3` on the very first lesson. Contents: `Flow ·
position of n` (`Flow · lesson position` when open-ended), a segment strip of `n` cells (`done` for
`0..completed.length-1`, `now` for cell `position-1`, empty for the rest; capped at 12 cells for
open-ended, showing the last `position` of them), and an **End flow** link. The bar reads `1 of 3` on
the first lesson, `1 of 3` again on that lesson's own advance screen, then `2 of 3` once the next
lesson opens. End flow: if `completed.length > 0`, set `endedReason = "ended"`, write, go to
`/flow/done`; else `clearFlow()` and go to `/paths/$pathId`.

## 6. Routes and files

| File | New / changed | Purpose |
| --- | --- | --- |
| `src/aleph/services/feature_flags.py` | changed | `FLOW = "flow"`, default `False`, admin-on (D10). |
| `tests/unit/test_feature_flags.py`, `tests/integration/conftest.py` | changed | Registration pin; `flow_flag_enabled/disabled` fixtures. |
| `src/lib/flow.ts` | new | Record, store, `useFlow`, `startFlow`, `clearFlow`, constants. |
| `src/lib/flow-order.ts` | new | Pure: `eligiblePaths`, `lessonsLeft`, `pickNext`, `advanceRecord`. |
| `src/lib/flow.test.ts`, `src/lib/flow-order.test.ts` | new | Unit tests with a seeded rng. |
| `src/routes/flow.new.tsx` | new | The setup sheet (§5.6). `data-testid="flow-setup"`. |
| `src/routes/flow.done.tsx` | new | The receipt (§5.5). `data-testid="flow-receipt"`. |
| `src/components/flow/flow-door.tsx` | new | Home's door (mock screen 01). `data-testid="flow-door"`. |
| `src/components/flow/flow-bar.tsx` | new | §5.7. `flow-bar`, `flow-bar-end`. |
| `src/components/flow/flow-advance-card.tsx` | new | §5.3 step 5. `flow-advance`, `flow-advance-count`, `flow-advance-go`, `flow-advance-pause`, `flow-advance-end`. |
| `src/components/flow/flow-drafts.tsx` | new | The receipt's drafts batch container (§5.5). |
| `src/routes/lessons.$lessonId.tsx` | changed | Wire §5.3, §5.4, §5.7; suppress `DraftList` in a flow. |
| `src/routes/index.tsx` | changed | `FlowDoor` under `ContinueCard`. |
| `src/routes/paths.$pathId.tsx` | changed | `Start a flow` link (`flow-path-link`). |
| `src/routes/__root.tsx` | changed | Leaving clears (§5.4). |
| `tests/setup.ts` | changed | `window.sessionStorage.clear()` in `afterEach`. |
| `src/mocks/paths.ts` | changed | If needed: a `seedPath` option to set `next_lesson` explicitly; today it is derived from `units` — check before adding. |
| `src/app/flow-setup.test.tsx`, `flow-lesson.test.tsx`, `flow-receipt.test.tsx`, `home-flow-door.test.tsx` | new | Route tests (§7). |
| `tests/e2e/journeys/w32.spec.ts` | new | The journey (§7). |
| `docs/CONTEXT.md`, `docs/roadmap.md` | changed | Ticket 0 (§8). |

Test ids on the sheet: `flow-length-3` / `-5` / `-8` / `-open`, `flow-path-{pathId}`,
`flow-select-all`, `flow-order-interleave` / `-random` / `-serial`, `flow-first-up`, `flow-start`,
`flow-not-now`. On the receipt: `flow-receipt-lessons`, `flow-receipt-paths`,
`flow-receipt-checks`, `flow-receipt-ledger`, `flow-receipt-drafts`, `flow-receipt-again`,
`flow-receipt-home`, `flow-receipt-review`.

Copy is the mock's. Voice: the learner's side of the screen ("Start a flow", "End flow", "Go now",
"Pause", "Go again · 5 more", "Not now"). Never "session", "mode", or "queue" in learner-facing text.

## 7. Testing strategy

**Pure (Vitest, `src/lib/*.test.ts`).** `pickNext` for each order, including: interleave wraps and
skips a dry path; serial sticks until dry; random with a seeded rng never repeats the current path
when two are eligible and *does* repeat when one is; `advanceRecord` ends on length and on dry;
`readFlow` rejects a malformed record and a wrong `version`, and survives a throwing storage.

**Routes (Vitest + MSW, `src/app/*.test.tsx`).** Render `<App/>` after `pushState`, flag on via a
session handler override (`feature_flags: { flow: true }` — copy `flashcards-drafts.test.tsx:23`).

- *Home:* door present with the flag on and a resumable path; absent with the flag off; absent when
  every path is finished.
- *Setup:* two seeded paths (`FRESH_PATH_UNITS`, `MID_PATH_UNITS`) plus one complete
  (`COMPLETE_PATH_UNITS`, disabled). Button copy follows the count and selection (cap suffix, `Pick
  a path to start`); *Select all* selects the two live rows and flips to *Clear*; order control
  appears at two selections; Start writes the record and lands on the first lesson's route
  (`lesson-view-id`).
- *Lesson in a flow:* seed the record directly with `writeFlow` before render. `flow-bar` shows
  `1 of 3`; `DraftList` absent although drafts are `generated`; after Mark complete the advance card
  appears with `5`; `vi.useFakeTimers()` in `try/finally`, advance 5 × 1000 ms → URL is the next
  lesson; *Pause* holds the count; *Go now* navigates immediately; a completion that finishes a path
  starts paused; length reached → `/flow/done`; a dry scope → `/flow/done` with `endedReason
  "dry"`; opening a lesson that is not `current` clears the record; the look-ahead GET for `next`
  is observed (count `GET /lessons/{next}` requests in the lessons mock).
- *Receipt:* stats and ledger from a seeded record; drafts batch renders one list per lesson with
  `generated` drafts and keeps per lesson (assert the keep request bodies); *Go again* lands on
  `/flow/new` prefilled; *Home* clears the record (assert `readFlow() === null` after navigation).

**E2E (`w32.spec.ts`, `@w32`, phone project).** Two paths via `createPath`; open `/flow/new`, pick
`3`, *Select all*, keep Interleave, Start; for each of three lessons: `waitForLessonGenerated`,
`answerQuickCheck`, `markComplete`, then click `flow-advance-go` (never wait the count in e2e);
assert `flow-bar` text advances `1 of 3 → 3 of 3`; assert the receipt shows `3` lessons and `2`
paths and the ledger names both topics. Assert structure, never model text. Isolate by unique topic.

**Backend.** `tests/unit/test_feature_flags.py` pins `FeatureFlag.FLOW == "flow"` and
`FLAG_DEFAULTS[FLOW] is False`; nothing else changes server-side.

**Gate.** `just gate` before every push; `just test-e2e` before the e2e ticket's push. Frontend
lint is Biome; a new file that imports `../lib/api` types must keep the `import type` split Biome
enforces.

## 8. Tickets

GitHub issues, cut from this document; issues are the source of truth (the Phase 1 / 2 / 2B
pattern).

- **Label:** `tdd-flow`; a parent epic carrying this document's link and the dependency graph.
- **Numbering:** **AL-6xx**, in dependency order (Phase 6 used AL-5xx; confirm the next free band
  against the issue tracker before cutting).

| # | Commit | Scope |
| --- | --- | --- |
| 0 | `docs:` | `CONTEXT.md` — a `## Flow` section with **Flow**, **Flow length**, **Flow scope** (take the wording from the mock's Vocabulary table, tightened to D3/D5); a phase-boundary bullet saying Flow is client-side, flag-gated, and drafts-deferred. `roadmap.md` — a row in the status table (`🟡 In progress — behind the `flow` flag`, Specs: `[TDD](tdds/flow.md) · [mock](mocks/aleph-flow-mode.html)`) and a short paragraph. **Precedes the code**: the vocabulary is authoritative. |
| 1 | `feat(flags):` | Register `FeatureFlag.FLOW` dark (D10) with the unit pin and the integration fixture pair. Deploys nothing visible. |
| 2 | `feat(flow):` | `lib/flow.ts` + `lib/flow-order.ts` with their unit tests, and the `tests/setup.ts` storage reset. No UI yet. |
| 3 | `feat(flow):` | `/flow/new`, the home door, the path-view link, and their route tests. Starting a flow lands on the first lesson, which still behaves exactly as today (the bar and advance come next) — so this ticket is shippable dark on its own. |
| 4 | `feat(flow):` | The lesson route: `FlowBar`, `FlowAdvanceCard`, the advance (§5.3), look-ahead and leaving (§5.4), drafts suppression (D7), root-layout clearing. Fake-timer route tests. |
| 5 | `feat(flow):` | `/flow/done` with the drafts batch, *Go again* prefill, the review link; route tests. |
| 6 | `test(e2e):` | `w32.spec.ts`. |
| 7 | `fix(flags):` | **Launch**: flip `FLAG_DEFAULTS[FLOW]` to `True` after dogfooding as an admin in production (deploy.md playbook; verify as a non-admin that `GET /api/v1/auth/session` carries `feature_flags.flow = true`). A separate change, never bundled with 1–6. |

Tickets 1 and 2 are independent and can land in either order; 3 depends on 2; 4 and 5 depend on 3
and can be built in parallel; 6 depends on 4 and 5.

**Rough size:** ~900 lines of production TypeScript (two routes, four components, two lib modules)
+ ~1,100 lines of tests + ~10 lines of Python. No migration, no new endpoint, no new dependency.

## 9. Risks and edges (read before ticket 4)

- **Stale `next_lesson` after completion.** The list is invalidated by the existing `onSuccess`;
  §5.3 *awaits a fresh fetch* rather than reading the cache, because the invalidation's refetch may
  not have landed by the time the advance runs. Do not shortcut this.
- **The pre-drawn `next` can go stale.** The learner might complete that lesson elsewhere (another
  tab). §5.3 step 4 re-checks eligibility against the fresh list before using it.
- **Path completion inside a flow.** `PathCompleteCard` still renders (it replaces `CompletedState`
  today); render `FlowAdvanceCard` *beneath* it, paused (D6). The dry path drops out of scope on
  the next pick automatically because its `next_lesson` becomes `null`.
- **A generating next lesson.** Navigating to it is fine: the lesson route polls and shows
  `GeneratingState`. The look-ahead (D8) is what makes this rare, not a guarantee.
- **Rate limits.** `POST …/generate` is limited per day; the look-ahead deliberately uses the GET.
  If a learner does hit `429` on a retry mid-flow, `FailedState`'s existing notice handles it.
- **Reduced motion.** The count ring animation is decorative; the numeral is the information. Gate
  the transition on `prefers-reduced-motion` via CSS only (the shell rule: no `matchMedia` in JS —
  `tests/setup.ts` stubs it, but do not add a new caller).
- **Storage blocked.** Every storage access is wrapped; with storage unavailable the door still
  shows, Start navigates to the first lesson, and the lesson behaves as if no flow exists. Acceptable
  degradation; not an error state.
- **Two tabs.** `sessionStorage` is per-tab, so two flows cannot collide. Progress is shared through
  the server as it is today.

## 10. Out of scope

- Due cards inside a flow (D11); minutes or time-based length (D13); mid-flow scope changes (D2);
  a server-side flow record, history, or "flows completed" stat (D1); a new metric event (D12);
  auto-skipping a failed lesson (D9); Beats or Briefs in a flow (a Beat is not a path).
