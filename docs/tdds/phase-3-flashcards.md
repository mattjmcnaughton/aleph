# TDD — Phase 3: Flashcards and spaced repetition

**Status:** Draft · **Owner:** solo builder · **Companion to:** [Phase 3 PRD](../prds/phase-3-flashcards.md)
**References:** [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) · [Phase 1 TDD](phase-1-path-generation.md) · [Phase 2 TDD](phase-2-tutor.md) · [Phase 2B TDD](phase-2b-shape-your-path.md) · [Phase 5 streaks TDD](phase-5-streaks.md) · [`metrics.md`](../metrics.md) · [`evals.md`](../evals.md) · mock: [phase-3 flashcards](../mocks/aleph-phase-3-flashcards.html) · prior art: habagou `domains/scheduling.py`, [ADR 0008](https://github.com/mattjmcnaughton/habagou/blob/main/docs/adrs/0008-review-state-as-rebuildable-projection.md)

> The PRD owns the product boundary — what a card is, what the learner sees, the cap and its
> split, what is deliberately never built. This TDD owns everything else: the schema and what is
> deliberately *not* in it, the pure scheduler, the derivation that pins the day's queue without
> storing it, the drafting agent and its delivery, the API, the streak union, the frontend, the
> evals, and the delivery plan.

Decision numbers restart at D1, scoped to this document. References into earlier TDDs are always
qualified ("Phase 5 D1", "Phase 1 D5", "Phase 2B §5.6").

> **The one-sentence version.** Phase 3 stores exactly two new things — **the cards, and the
> reviews of them** — and derives everything else, the pinned daily queue included; the card's
> ladder position is a projection over the review log, rebuildable by replay, and that log is the
> only place in Aleph where a scheduling fact lives.

## 1. Decision record

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| D1 | Source of truth | **`flashcard_reviews` is append-only and authoritative; `flashcards.rung` / `flashcards.due_on` is a projection** over it, written in the same transaction as the review row and rebuildable in full by replaying the log through the same pure ladder. A unit test asserts `replay(reviews) == projection` | habagou [ADR 0008](https://github.com/mattjmcnaughton/habagou/blob/main/docs/adrs/0008-review-state-as-rebuildable-projection.md) verbatim, and the narrowest possible break from Phase 5 D1's *derived, never stored*. §5's *recall rate by rung* and the PRD §5 Phase-4 lapse seam mean the log is written either way; making it authoritative costs one test and buys a real answer to "the scheduler shipped a bug, now what" — drop the projection, replay. The queue read wants an indexed `due_on`, not a per-card fold over history, which is exactly the trade ADR 0008 documents |
| D2 | Pure domain | **`domains/scheduling.py`** — the ladder (`initial_state`, `apply_grade`, `next_interval_days`) *and* the day's selection (`select_daily_queue`), stdlib only, no ORM | A port of habagou's module under `domains/__init__.py`'s contract. One module because both halves are the same concern and neither is useful without the other; two functions rather than one because the ladder never sees a queue and the selection never sees a grade — the Phase 5 D2 discipline (a parameter a function does not need is a parameter that can drift) |
| D3 | The daily queue | **Derived, not stored.** No queue table. The day's ten are a pure function of `(candidates, today, user_id)` where the candidate population is `due_on <= today` **∪** `reviewed today`, and the "3 at random" are the three lowest `sha256(user_id:day:card_id)` | The population is **stable across the day** (§5.3's invariant): grading moves a card's `due_on` into the future but the `reviewed today` arm holds it in the set, so the same inputs re-derive the same ten on every request. That is what §4.5 asks for, with nothing stored and no `GET` that writes. The hash order replaces `random.Random`, whose `sample()` is stable within a Python version and promises nothing across one — a runtime upgrade must not reroll every learner's day mid-session |
| D4 | Day boundary | **Reuse Phase 5 D3 unchanged**: the client sends `tz_offset_minutes` (`getTimezoneOffset()` verbatim), the service subtracts it from `now(UTC)` to reach `today`. `flashcards.due_on` is a **`date`**, not a timestamp | The ladder's intervals are whole days and the cap is a day, so a timestamp would carry a precision the product has no rule for. A `date` makes the hot query `due_on <= :today` with no timezone expression at all — the tz arithmetic happens once, in the service, in the one place Phase 5 already put it. Price: a learner who reviews in Tokyo and then in Berlin can see a card a day early, the same accepted risk as the streak (§15) |
| D5 | Drafting delivery | **Trigger + poll, on its own endpoint.** `POST /lessons/{id}/flashcard-drafts` → `202`, background task, client polls `GET`. **`complete_lesson` is not touched** | Phase 1 D5's transport, and the completion path stays bit-identical — which matters more than it looks: Phase 5 D8's inherited idempotence rests on `mark_completed_and_finalize`'s `completed_at IS NULL` guard, and the streak's correctness rests on that method having no second write beside it. With the flag off, completion is unchanged *by construction* rather than by a branch |
| D6 | Drafts | **Rows in `flashcards` with `kept_at IS NULL`.** Keep is one atomic `POST …/keep` carrying the ids to keep; every unlisted draft for that lesson is deleted in the same transaction. "Skip — keep none" is the same request with `[]` | PRD §3's "discarded drafts are not saved anywhere" is satisfied by the delete. The alternative — drafts live only in the response and the client POSTs back the front/back strings — makes card text **learner-supplied at the trust boundary** and makes the eval artifact (§10) something we sample from a request body rather than from what the agent actually produced. One table, not two: a keep is a flag flip, so a card's id is stable from draft to schedule, and the hot query excludes drafts through a partial index rather than a second table |
| D7 | Draft run state | **`flashcard_draft_runs`**, one sparse row per lesson (`lesson_id` PK), holding `state` / `started_at` / `error`. Claimed by insert-on-conflict, stale-recovered on the Phase 1 §5.4 timings | *Generating* has no card rows yet, so the state cannot live on the drafts themselves. Three nullable columns on `lessons` (the Phase 2B `revision_instruction` precedent) would be wide for every lesson forever; most lessons are never completed, so a row that exists only once drafting is triggered is the smaller thing. It also gives the abandoned-drafts answer for free (§14 Q4): the run stays `generated`, the unkept drafts stay, and revisiting the lesson re-serves them |
| D8 | The lapse | **Derived, unbounded.** A queue card is *satisfied* when its most recent review today is `got_it`; `again` leaves it unsatisfied and it is re-served after every never-attempted card. No cap on re-shows | PRD §4.7 — the cap counts distinct cards, so a lapse cannot cost a slot, and that falls out of the derivation rather than needing a rule. Unbounded is the owner's call: the day's set is done when all ten have been answered once, the learner can always stop by leaving, and the card is demoted and rescheduled either way. Durable across a reload because it is read from the log, not held in the client |
| D9 | The count's endpoint | **`GET /reviews/summary`, its own route on the flashcards router — *not* an extension of `GET /progress/summary`** | Reverses this document's own first draft (§14, R1). Phase 5 D4 invited the progress envelope to grow, but the two payloads have different lifetimes and different kill switches: the streak line is home-only, the due pill is **on every route**, and a `flashcards` kill must not require killing `streaks` (or vice versa) because one router-level gate cannot resolve two flags without conditional fields in one response |
| D10 | Rollout | **`FeatureFlag.FLASHCARDS`**, `False` in `FLAG_DEFAULTS`, present in `ADMIN_DEFAULT_FLAGS`; **one** flag over drafting, the queue, review and the pill, gated router-level. Off → `404`, and the streak union silently loses its second signal | The `tutor` / `shaping` / `streaks` machinery verbatim (Phase 5 D7). One flag because the three surfaces are one feature — a queue with no drafting is an empty queue, and drafting with no queue is a card sink. The whole point of a router-level gate is that §7's future additions inherit it |
| D11 | The streak union | **A second repository method** (`FlashcardReviewRepository.review_days_for_user`) unioned in `services/progress_read.py`; the global fold takes the union, the **per-path fold is untouched** | Phase 5 D1's amendment note, made real. One query returning both signals with a discriminator would mean both folds filter a column back out, and the per-path fold must never see a review (PRD §4.9). The review day is **recomputed** from `reviewed_at` with the same UTC-pinned expression as `completed_at`, not read from the stored `local_day` — so the two halves of one union cannot disagree about what a day is (§5.5) |
| D12 | Citation | **Denormalized titles + two `ON DELETE SET NULL` FKs + a `source_generated_at` stamp.** The citation is a link iff the lesson row still exists *and* its `generated_at` equals the stamp taken at draft time | PRD §4.11's three triggers — revised, removed, path deleted — collapse to two checks. Deletion nulls the FK; any regeneration (a 2B Revision, a Phase 4 edit, a retry) moves `generated_at`. The titles are copied onto the card so the *text* survives the row: nothing is silently deleted, nothing dangles, and the degraded line needs no join to render |
| D13 | Model & spend | **New `model_flashcard` slot** in `MODEL_SLOTS`, defaulting to the same model as everything else; **`flashcard_drafts_per_day`** on the existing `DailyRateLimiter` | Answers PRD open Q4 (§14). The slot costs one `Settings` field and one stub entry, and it is the only thing that makes "this is a haiku job" an env var rather than a code change. The limiter is not symmetry — this is the phase's one learner-triggered model call, and Phase 5 skipping the limiter was justified *because* it had none |
| D14 | Evals | **`flashcard_draft` as a third `ArtifactKind`**, four dimensions split between deterministic pre-filters and judge items, predicates shared with the agent's own output validator | PRD §6. Recorded honestly: `evals/rubric.py` still reads `Literal["outline", "lesson"]` — `tutor_reply` (Phase 2 D11) and `path_proposal` (Phase 2B D13) are *specified* precedents, not shipped ones, so this is the **first** actual extension of the kind axis and is sized as such (§16) |
| D15 | E2E clock | The stub backend gains **`POST /__e2e__/shift-flashcard-due`**, a *shift* primitive beside Phase 5 D11's `shift-completions`. No card seeder | Phase 5 D11's discipline: a shift cannot put the database into a state the real app could not reach, a seeder can. W25 needs >10 due cards, which the journey earns by completing three lessons and keeping twelve cards through the real UI, then shifting them back |

## 2. Extension map

| Concern | Existing asset | Flashcards change |
| --- | --- | --- |
| Agent purity | `agents/lesson.py` / `outline.py` / `shaper.py` — no bound model, no config import, caps as run-time deps, validators as importable predicates | **New** `agents/flashcard.py` in the identical shape; auto-covered by the layering test |
| Model routing | `MODEL_SLOTS`, `services/openrouter.py`, the production stub guard | **Extend:** one slot, `model_flashcard`. Listing it in `MODEL_SLOTS` is what puts it behind the `ENV=production` stub guard |
| Generation orchestration | `services/generation.py` + `repositories/_generation.py` — claim, timeout, stale recovery, retry | **Reuse the pattern, not the code:** drafting claims one `flashcard_draft_runs` row on the same timings. It is a much smaller machine (no prefetch, no continuity, no chain) |
| Trigger + poll | `POST /lessons/{id}/generate` → `202`, client polls `GET /lessons/{id}` | **Reuse verbatim** for drafting (D5) |
| Completion write | `LessonRepository.mark_completed_and_finalize`, `completed_at IS NULL` guard | **Untouched.** The single most important property of D5 — the streak's idempotence is inherited from it (Phase 5 D8) |
| Pure derivation | `domains/streaks.py`, `progression.py`, `engagement.py` | **New** `domains/scheduling.py` under the same rules |
| Read-side services | `services/paths_read.py`, `lessons_read.py`, `progress_read.py` | **New** `services/reviews.py` (reads + the grade write) and `services/flashcard_drafting.py` |
| Streak fold | `services/progress_read.py::_summarize`, seamed on a `CompletionDaysReader` `Protocol` | **Extend:** a second `Protocol` (`ReviewDaysReader`), one union, per-path fold unchanged (D11) |
| Router conventions | `routers/v1/`, `CurrentUser` / `Session` aliases, `require_*_enabled`, 404-never-403, the `errors.py` envelope | **New** `routers/v1/flashcards.py`; conventions verbatim |
| Feature flags | `FeatureFlag` enum, `FLAG_DEFAULTS`, `ADMIN_DEFAULT_FLAGS`, the router dependency | **Extend:** one enum member, two registry entries, one dependency copied from `require_streaks_enabled` |
| Rate limiting | `services/rate_limit.py::DailyRateLimiter`, per-action caps, `repositories/usage.py` | **Extend:** one cap, `flashcard_drafts_per_day` |
| Events | `events.py` `EVENT_FIELDS` + `tests/unit/test_events`, `test_metrics_queries` | **Extend:** three events (§9) |
| Evals | `evals/rubric.py` (`ArtifactKind`, `APPLICABLE_ITEMS`, `ARTIFACT_NOTES`), `judge.py`, `seed_set.yaml` | **Extend:** the third kind, a card seed set, the card judge prompt (§10) |
| Stub model | `services/stub_model.py` — prompt markers, sentinels, forced failures | **Extend:** a `flashcard_drafts=<N>` marker read like `position_in_path`, and a `[force-draft-failure]` topic sentinel |
| Frontend HTTP seam | `lib/api.ts` — `apiFetch`, `queryOptions` pairs, `skipToken` | **Extend:** three query-option factories + their types |
| App chrome | `components/app-header.tsx` | **Extend:** the due pill — the one piece of persistent navigation this phase adds |
| Home surface | `routes/index.tsx` — streak line, activity strip, `PathRow` | **Extend:** a *Due today* card above the path list, a `Review N` chip inside each row |
| Lesson surface | `routes/lessons.$lessonId.tsx` — the completion mutation and its cache work | **Extend:** the drafting block below the completion state |
| Refresh discipline | `src/app/completion-refresh.test.tsx` (Phase 5 D10) | **New sibling** `review-refresh.test.tsx` — the day's first review moves the streak line the same way a completion does |
| MSW | `mocks/handlers.ts` composing per-domain modules | **New** `mocks/flashcards.ts` in the same shape |
| E2E harness | `scripts/e2e_backend.py::create_stub_app`, `tests/e2e/fixtures/`, `journeys/w<N>.spec.ts` | **Extend:** one `/__e2e__` route (D15), a `shiftFlashcardDue` fixture, `w24`–`w27` |

**Built new:** migration `0010` (§4), `models/flashcard.py`, `repositories/flashcards.py`, `domains/scheduling.py` (§5.1), `agents/flashcard.py` (§5.2), `services/flashcard_drafting.py` (§5.2), `services/reviews.py` (§5.3–5.4), `dtos/flashcards.py` + `routers/v1/flashcards.py` (§6), the frontend `/review` route and its components (§8), three saved queries (§9), the `flashcard_draft` eval kind (§10).

**Not built, and named so the absence is a decision:** no queue table (D3), no card-management surface, no scheduler process or cron of any kind, no notification transport, no leech table, no ease factor, no second streak, no aggregation over lapses (the Phase 4 seam is the log itself), and no change whatsoever to the lesson completion path.

## 3. Architecture overview

Layering unchanged: `routers → services → (agents, repositories)`, `domains/` pure beneath.

```
src/aleph/
  domains/
    scheduling.py            # ladder + daily selection — pure, stdlib only
  agents/
    flashcard.py             # FlashcardDrafts schema, deps, caps, shared validators
  models/
    flashcard.py             # Flashcard, FlashcardReview, FlashcardDraftRun
  repositories/
    flashcards.py            # the three queries that matter (§5.3, §5.5)
  services/
    flashcard_drafting.py    # claim → run the agent → persist drafts; keep/discard
    reviews.py               # the queue read, the grade write; owns "today"
  routers/v1/
    flashcards.py            # every route, one router-level flag gate
  dtos/
    flashcards.py
  services/
    feature_flags.py         # + FeatureFlag.FLASHCARDS
    progress_read.py         # + the streak union (D11)
```

The two paths, end to end:

```
POST /lessons/{id}/flashcard-drafts        → 202
  → require_flashcards_enabled             (404 if off, before any work)
  → assert owned + completed                (404 / 409)
  → rate limiter                            (429)
  → claim flashcard_draft_runs row          (insert-on-conflict; already generating → 202, no-op)
  → background: flashcard agent → N drafts → rows with kept_at IS NULL → run.state = generated

GET /reviews/queue?tz_offset_minutes=-120&path_id=…
  → today = (now(UTC) - offset).date()      (the service is the sole owner of "today")
  → FlashcardRepository.due_candidates(user_id, today)   → [(card_id, due_on_at_start_of_day,
                                                            satisfied, last_reviewed_at)]
  → select_daily_queue(candidates, cap=10, overdue_slots=7, seed=f"{user_id}:{today}")
  → filter by path_id for display; serve order = unsatisfied, least-recently-attempted first
  → QueueResponse
```

The structural claim: **nothing outside `services/reviews.py::grade_card` and
`services/flashcard_drafting.py` writes a flashcard row**, and neither is reachable from a `GET`.
The queue is a read *because* D3 refused to store it — had the day's set been a table, the first
`GET` of every day would have been a write, and the whole surface would have lost the property
that makes it safe to refetch on focus.

## 4. Data model & storage schema (migration `0010`)

`down_revision = "0009_lesson_completed_at_index"`, in the `0005_shaping` style (three
`op.create_table` calls plus indexes). Additive and reversible by dropping the three tables.

```
flashcards
  id                    uuid PK
  user_id               uuid NOT NULL  FK users(id) ON DELETE CASCADE
  front                 text NOT NULL
  back                  text NOT NULL
  kept_at               timestamptz NULL         -- NULL = a Draft (D6)
  rung                  int NULL                 -- projection over flashcard_reviews (D1)
  due_on                date NULL                --   "        "        "
  source_lesson_id      uuid NULL  FK lessons(id) ON DELETE SET NULL
  source_path_id        uuid NULL  FK paths(id)   ON DELETE SET NULL
  source_lesson_title   text NOT NULL            -- copied at draft time (D12)
  source_path_title     text NOT NULL            --   "      "     "
  source_generated_at   timestamptz NOT NULL     -- the lesson's generated_at when drafted
  created_at/updated_at                          -- UUIDAuditMixin
  INDEX ix_flashcards_user_id_due_on (user_id, due_on) WHERE kept_at IS NOT NULL
  INDEX ix_flashcards_source_lesson_id (source_lesson_id)

flashcard_reviews                                -- append-only; the source of truth (D1)
  id            uuid PK
  card_id       uuid NOT NULL  FK flashcards(id) ON DELETE CASCADE
  user_id       uuid NOT NULL  FK users(id)      ON DELETE CASCADE
  grade         enum('again','got_it') NOT NULL
  reviewed_at   timestamptz NOT NULL
  local_day     date NOT NULL                    -- the learner's day at write time
  rung_before   int NOT NULL
  rung_after    int NOT NULL
  due_on_before date NOT NULL                    -- what makes §5.3's pin derivable
  due_on_after  date NOT NULL
  created_at/updated_at
  INDEX ix_flashcard_reviews_card_id_reviewed_at (card_id, reviewed_at)
  INDEX ix_flashcard_reviews_user_id_local_day   (user_id, local_day)
  INDEX ix_flashcard_reviews_user_id_reviewed_at (user_id, reviewed_at)

flashcard_draft_runs                             -- sparse: one row per *drafted* lesson (D7)
  lesson_id     uuid PK  FK lessons(id) ON DELETE CASCADE
  state         enum('generating','generated','failed') NOT NULL
  started_at    timestamptz NOT NULL
  error         text NULL
  created_at/updated_at
```

Four things about this schema are load-bearing:

1. **The partial index is the hot path.** `ix_flashcards_user_id_due_on … WHERE kept_at IS NOT NULL`
   is exactly the Phase 5 D6 shape (`lessons … WHERE completed_at IS NOT NULL`) and for exactly the
   same reason: drafts are transient and the queue never wants them, so excluding them from the
   index keeps it proportional to a learner's real deck rather than to everything the agent ever
   proposed. It covers both the predicate and the ordering the selection needs.
2. **`due_on_before` is not audit decoration.** It is what lets §5.3 derive a *pinned* queue from a
   population whose members are being mutated all day: once a card is graded, its live `due_on`
   is useless for selection, and its start-of-day value is recoverable only from the log. This is
   the concrete payoff of D1 that a "reviews are analytics" design would not have.
3. **`user_id` is denormalized onto both card and review.** A card is the learner's, not the
   lesson's (PRD §4.1), and it outlives the lesson and the path (§4.11) — so ownership *cannot*
   be reached by joining upward the way `LessonRepository.get_for_user` does. The predicate has to
   be on the row itself or an orphaned card becomes unreachable and unscopeable at once.
4. **Both source FKs are `SET NULL`, and both titles are copied.** Deleting a path cascades to its
   lessons, so a single delete nulls both FKs; the copied titles are what keep the card's own
   citation line renderable with no join. `source_generated_at` is the revision detector (D12).

**Growth.** One row per kept card, forever, plus one review row per grade. A learner keeping 4
cards a day for two years with a 10/day cap holds ~2 900 cards and ~7 000 reviews. That is the
honest ceiling and it is not a problem; the first thing to bound if it ever is would be review
history older than the metrics window, which is a product decision and not a technical one.

**`test_schema.py`** (model/DDL agreement) and **`test_migrations.py`** (up and down) cover the
new tables as they cover every other one.

## 5. The pipelines

### 5.1 Pure domain (`domains/scheduling.py`)

Stdlib only, frozen inputs, no ORM, no clock — the `domains/__init__.py` contract verbatim.

```python
LadderDays = tuple[int, ...]                       # from settings, never imported here

class Grade(StrEnum):
    AGAIN = "again"
    GOT_IT = "got_it"

@dataclass(frozen=True)
class CardState:
    rung: int
    due_on: date

@dataclass(frozen=True)
class Candidate:
    card_id: uuid.UUID
    due_on: date          # as of the START of today — see §5.3's invariant
    satisfied: bool       # most recent review today was `got_it`

def initial_state(*, kept_on: date, ladder: LadderDays) -> CardState: ...
def apply_grade(state: CardState, grade: Grade, *, today: date, ladder: LadderDays) -> CardState: ...
def next_interval_days(state: CardState, *, ladder: LadderDays) -> int: ...
def select_daily_queue(
    candidates: Sequence[Candidate], *, seed: str, cap: int, overdue_slots: int
) -> tuple[uuid.UUID, ...]: ...
```

**Ladder semantics**, stated once here and pinned by the unit tests:

- A rung *r* means "the next interval is `ladder[r]` days". `initial_state` is **rung 0**, due
  `kept_on + ladder[0]` — with the default ladder `(1, 3, 7, 14, 30)` that is **tomorrow**, which
  is the whole of the answer to "can a card you just made come back tonight" (§14, Q2): no, and
  there is no special case in the code that says so — it falls out of entering at rung 0.
- `GOT_IT` promotes: `rung = min(rung + 1, len(ladder) - 1)`, `due_on = today + ladder[new_rung]`.
  The top rung is a fixed point, so a mature card settles at a 30-day interval rather than growing
  without bound. Note the interval is measured from **today**, not from the card's old `due_on` —
  with a cap, "due" is advisory (PRD §4.6) and anchoring to a date the learner may have missed by
  a week would compound lateness into the schedule.
- `AGAIN` demotes: `rung = max(rung - 1, 0)`, and **`due_on = today`** — not `today + ladder[0]`.
  That is what makes the lapse return *later the same session* (PRD §4.7) rather than tomorrow,
  and it is why the mock's button reads `Again · later today`. If the learner never gets back to
  it, the card is simply one day overdue tomorrow, which is correct.
- The ladder is a **parameter, never a module constant**. Config owns the numbers (§13); the
  domain owns the arithmetic. A short ladder, a one-rung ladder and an empty ladder are all
  rejected at `Settings` construction, not here.

**`select_daily_queue` semantics:**

- If `len(candidates) <= cap`, every candidate is selected. The 7/3 split never applies — the
  ordinary case for a learner who reviews most days (PRD §4.4).
- Otherwise: the **`overdue_slots` most overdue** by `(due_on, card_id)`, then **`cap - overdue_slots`**
  drawn from the rest by ascending `sha256(f"{seed}:{card_id}")`. The random count is *derived*,
  never configured, so 7/3/10 cannot be set into disagreement.
- The returned order is `(due_on, card_id)` over the selected set — most at risk first, and
  deterministic, which is most of what W25 asserts. Cards from different paths interleave naturally
  because their due dates do (PRD §4.3's interleaving argument needs no shuffle to hold).
- `satisfied` is **not** read by the selection. It exists so the caller can build the counter and
  the serve order without a second pass, and keeping the selection blind to it is what guarantees
  the day's set cannot shrink as the learner works through it.

### 5.2 Drafting (`agents/flashcard.py`, `services/flashcard_drafting.py`)

The agent follows `agents/lesson.py` exactly: no bound model, no config import, caps as a
run-time dep, validators as importable predicates shared with the eval pre-filters (§10).

```python
class FlashcardDraft(BaseModel):
    front: str
    back: str

class FlashcardDrafts(BaseModel):
    cards: list[FlashcardDraft]

@dataclass(frozen=True)
class FlashcardCaps:
    count_min: int = 3
    count_max: int = 5
    front_words_max: int = 25
    back_words_max: int = 60

@dataclass(frozen=True)
class FlashcardDeps:
    topic: str
    level: Level
    unit_title: str
    lesson_title: str
    read_passage: str
    quick_check_stem: str
    caps: FlashcardCaps
```

**The prompt** carries the Read passage verbatim, the lesson and unit titles, topic and level, and
the Quick-check **stem only** — the stem is in the prompt so the agent can be told not to restate
it (PRD §6), and the options and explanation are left out because a card is not a Quick check and
seeing the distractors invites writing one. It also carries `flashcard_drafts=<N>`, the marker the
stub reads (§11), placed once and ahead of everything else on the AL-032 `position_in_path`
precedent.

**Shared validators** (layer 2 on the agent, layer 1 pre-filters in evals — never duplicated):
`count_within_band`, `is_non_empty` (both sides), `within_word_cap`, `sides_differ`, and
`restates_stem` — a normalized token-overlap check against the Quick-check stem, which is the one
dimension of PRD §6's four that is honestly deterministic.

**The service** claims, runs, persists:

1. `POST` handler asserts the lesson is owned and `completed_at IS NOT NULL` (→ `409` otherwise),
   then the rate limiter (→ `429`).
2. Claim `flashcard_draft_runs` by `INSERT … ON CONFLICT (lesson_id) DO UPDATE … WHERE state = 'failed'
   OR started_at < :stale_cutoff`. An already-`generating` run is a no-op `202`; an already-`generated`
   run is a no-op `202` and the client's poll finds the existing drafts. **Drafting a lesson twice is
   structurally impossible**, which is what makes the endpoint safe to fire from a mutation
   `onSuccess` that React may run twice.
3. Background task runs the agent with `model=resolve_model(settings.model_flashcard)` under
   `generation_timeout`, persists `cards` as `flashcards` rows with `kept_at IS NULL`,
   `rung`/`due_on` `NULL`, and the four `source_*` columns copied from the lesson and its path.
4. `state = 'generated'`, or `'failed'` + `error` on timeout/refusal/validation exhaustion. Failure
   is retryable by re-`POST` (the `WHERE state = 'failed'` arm above) and shows a retry, not a dead
   spinner — `state-card.tsx` already renders exactly this shape for lesson generation.

**Keep** is one transaction: `UPDATE flashcards SET kept_at = now(), rung = 0, due_on = :today + ladder[0]
WHERE id IN :kept AND source_lesson_id = :lesson AND kept_at IS NULL`, then
`DELETE FROM flashcards WHERE source_lesson_id = :lesson AND kept_at IS NULL`. Both statements are
scoped to the lesson, so the delete cannot reach another lesson's pending drafts; a `kept_id` that
is not a draft of this lesson is a `404`, never a silent skip.

### 5.3 The queue read (`services/reviews.py`) — and the invariant it rests on

```python
@dataclass(frozen=True)
class QueueCardView:
    card_id: uuid.UUID
    front: str
    back: str
    rung: int
    got_it_interval_days: int
    source: CitationView          # linked | degraded (D12)
    path_id: uuid.UUID | None

@dataclass(frozen=True)
class ReviewQueueView:
    today: date
    cards: list[QueueCardView]    # unsatisfied only, in serve order
    total: int                    # the day's set size — the `of 10` denominator
    completed: int                # distinct satisfied — the counter's numerator
    scope_path_id: uuid.UUID | None
    other_due_count: int          # for the end-of-filtered-session widen offer (PRD §4.10)
```

The repository returns one row per candidate:

```sql
WITH first_today AS (
  SELECT DISTINCT ON (r.card_id) r.card_id, r.due_on_before
  FROM flashcard_reviews r
  WHERE r.user_id = :user_id AND r.local_day = :today
  ORDER BY r.card_id, r.reviewed_at
),
latest_today AS (
  SELECT DISTINCT ON (r.card_id) r.card_id, r.grade, r.reviewed_at
  FROM flashcard_reviews r
  WHERE r.user_id = :user_id AND r.local_day = :today
  ORDER BY r.card_id, r.reviewed_at DESC
)
SELECT f.id,
       COALESCE(ft.due_on_before, f.due_on) AS due_on,     -- start-of-day value
       (lt.grade = 'got_it')                AS satisfied,
       lt.reviewed_at                       AS last_reviewed_at
FROM flashcards f
LEFT JOIN first_today  ft ON ft.card_id = f.id
LEFT JOIN latest_today lt ON lt.card_id = f.id
WHERE f.user_id = :user_id
  AND f.kept_at IS NOT NULL
  AND (f.due_on <= :today OR ft.card_id IS NOT NULL)
```

**The invariant, stated because it is the correctness claim of D3 and deserves a test named after
it:**

> For a fixed `(user_id, today)`, the candidate set and each candidate's `due_on` are **invariant
> under grading**. Grading moves `flashcards.due_on` into the future, which drops the card out of
> the `due_on <= :today` arm — and the `ft.card_id IS NOT NULL` arm puts it straight back, with
> `COALESCE` restoring the value it had before the grade. `select_daily_queue` is a pure function
> of that set, so the day's ten are the same ten on every request of the day.

Two things fall outside the invariant and are recorded rather than defended: a card **kept** later
in the day never joins today's set (it is due tomorrow, D3/§5.1 — the desired behaviour), and a
card whose `due_on` is moved by anything other than a review would reroll the day, which is why
nothing else in this design writes `due_on`.

`total` is the size of the selected set; `completed` is the count of selected cards with
`satisfied`; `cards` is the unsatisfied remainder ordered by `(last_reviewed_at NULLS FIRST,
due_on, card_id)` — never-attempted first, then lapses least-recently-seen first, which is D8's
"later in the session" with no session object anywhere.

**`path_id` filtering is display-only.** The selection always runs globally (PRD §4.3: a path entry
filters the queue, it does not open another), and `?path_id=` filters the result. The consequence
worth naming: **the per-path chips on home count each path's share of the global ten, and they sum
to the global count** — `Review 7` beside `10 cards` means seven of today's ten came from that
path, not that the path has seven of its own due. An orphaned card (D12) has `path_id: null`, so
it appears under *All paths* and in no filtered queue.

### 5.4 Grading (`services/reviews.py::grade_card`)

`POST /api/v1/reviews` with `{card_id, grade, rung_before, tz_offset_minutes}`, one transaction:

1. Load the card `FOR UPDATE`, scoped by `user_id` (→ `404`).
2. Re-derive today's queue and assert the card is in it and unsatisfied (→ `409 not_due`). This is
   what stops a learner grading a card that is not today's business, and it costs nothing because
   the derivation is the same one the `GET` just ran.
3. Assert `card.rung == rung_before` (→ `409 stale_rung`). Optimistic concurrency on the
   projection: a double-tapped button, or a retry of a request that actually succeeded, becomes a
   no-op `409` rather than a double promotion. The client already holds `rung` — it renders the
   interval preview from it — so this adds no round trip.
4. Append the `flashcard_reviews` row (`rung_before`/`due_on_before` from the card as loaded,
   `rung_after`/`due_on_after` from `apply_grade`, `local_day = today`).
5. Update the projection on `flashcards` from the same `apply_grade` result.

Steps 4 and 5 are one transaction and must move together — a partial write is a bug, not tolerated
skew, and it is recoverable by replay (ADR 0008's own consequence, restated because it is the price
of D1).

### 5.5 The streak union (`services/progress_read.py`)

`_summarize` gains a second reader behind its own `Protocol`, and one line changes:

```python
completion_days = {row.day for row in rows}
review_days = set(await reviews.review_days_for_user(
    user_id=user_id, tz_offset_minutes=tz_offset_minutes))
global_streaks = compute_streaks(completion_days | review_days, today=today)
```

The per-path fold is untouched — `rows_by_path` never sees a review (PRD §4.9, and `CONTEXT.md`'s
**Path streak** row says so in the vocabulary itself). `activity_window` takes the union's counts
too, so the strip and the number cannot disagree.

**The review day is recomputed, not read.** `flashcard_reviews` stores `local_day`, and the streak
query deliberately does not use it:

```sql
SELECT DISTINCT ((r.reviewed_at AT TIME ZONE 'UTC')
                  - make_interval(mins => :tz_offset_minutes))::date AS day
FROM flashcard_reviews r WHERE r.user_id = :user_id
```

Two columns, two purposes. `local_day` is a **scheduling** fact frozen at write time — the queue's
"reviewed today" arm needs the day the learner was actually in, and re-deriving it would move
yesterday's queue when a learner flies east. `reviewed_at` is the **streak** fact, recomputed with
the identical UTC-pinned expression `completion_days_for_user` uses (Phase 5 D3, §14 R1), so the
two halves of one union agree about what a day is by construction rather than by coincidence. A
learner who crosses a date line can therefore see one queue day and one streak day disagree; that
is the same accepted trade Phase 5 §15 already took, and it is the cheaper of the two errors.

With the `flashcards` flag off the second reader is never called and the streak is exactly what it
is today — which is what makes D10's kill switch honest.

### 5.6 Failure semantics

| Case | Wire result | Learner sees |
| --- | --- | --- |
| Not signed in | `401 unauthenticated` | The login redirect, as everywhere |
| `flashcards` flag off | `404 not_found`, before any work | Nothing — no pill, no drafts, no fetch (§8) |
| Drafting a lesson that is not complete | `409 lesson_not_complete` | Nothing; the client only fires after a completion |
| Drafting over the daily cap | `429` through the shared envelope | The completion stands; the drafts block says drafting is unavailable today |
| Drafting fails (timeout, refusal, validation) | run `failed`; `GET` returns `{state: "failed"}` | A retry affordance in the existing `state-card` shape — never a dead spinner |
| Grading a card not in today's queue | `409 not_due` | Nothing; the client cannot construct the request |
| Grading with a stale `rung_before` | `409 stale_rung` | Nothing — a double-tap is absorbed |
| No cards due | `200`, `cards: []`, `total: 0` | *Nothing due today* — an invitation, not a debt (PRD §4.8) |
| Queue query fails | `500` envelope | The pill renders nothing; **home and the lesson are unaffected** |

That last row is a constraint on §8, not a table entry: **the due pill and the *Due today* card are
decoration on surfaces that are the product, and must fail as decoration.** It is the same rule
Phase 5 §5.4 wrote for the streak line, and it now applies to a component mounted in the app header
on every route — which raises the stakes rather than changing the rule.

## 6. API design

New router `routers/v1/flashcards.py`, prefix `/api/v1`, every convention verbatim (cookie auth,
router-level flag gate, 404-never-403, the `errors.py` envelope). `docs/api.md` gains a
`## Flashcards` section.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/lessons/{lesson_id}/flashcard-drafts` | Trigger drafting for a completed lesson. `202`; idempotent (D7) |
| `GET /api/v1/lessons/{lesson_id}/flashcard-drafts` | Poll: `{state, cards: [{id, front, back}]}` |
| `POST /api/v1/lessons/{lesson_id}/flashcard-drafts/keep` | `{kept_ids: […]}` → keep those, delete the rest. `[]` is "Skip — keep none" |
| `GET /api/v1/reviews/summary?tz_offset_minutes=` | `{today, due_count, estimated_minutes, paths: [{path_id, due_count}]}` — home's card, the app-bar pill, the per-path chips |
| `GET /api/v1/reviews/queue?tz_offset_minutes=&path_id=` | The day's cards in serve order, plus the counter and `other_due_count` |
| `POST /api/v1/reviews` | `{card_id, grade, rung_before, tz_offset_minutes}` → the card's new state |

```jsonc
// GET /api/v1/reviews/queue
{
  "today": "2026-08-03",
  "total": 10,                          // the `of 10` denominator — the day's set
  "completed": 3,                       // distinct cards already answered Got it
  "scope_path_id": null,
  "other_due_count": 0,                 // > 0 only when scope_path_id is set
  "cards": [
    {
      "card_id": "…",
      "front": "What does `extends` mean in `<T extends X>`?",
      "back": "It constrains T — T must be assignable to X. It is not class inheritance.",
      "rung": 2,
      "got_it_interval_days": 7,        // what the Got it button previews
      "path_id": "…",
      "source": {                       // D12
        "kind": "linked",               // "linked" | "degraded"
        "lesson_id": "…",
        "lesson_title": "Generic constraints",
        "path_title": "Learn TypeScript"
      }
    }
  ]
}
```

`401` unauthenticated · `404` flag off, or an unowned/unknown lesson or card · `409` not complete /
not due / stale rung · `422` out-of-range offset · `429` drafting over cap.

DTOs (`dtos/flashcards.py`) reuse `TzOffsetMinutes` from `dtos/progress.py` rather than redeclaring
the `ge=-900, le=900` band — one constrained alias, one place to be wrong. Mapping is explicit
construction, as `_progress_summary_response` does; no `from_attributes`.

**`source` is an object, not three flat fields.** The degraded case has no `lesson_id` at all, and
a discriminated shape makes that unrepresentable rather than merely undocumented — the frontend
renders a link or plain text off `kind` and can never dereference a null.

## 7. Load, caching & rate limiting

**Rate limiting, only on drafting.** `flashcard_drafts_per_day` joins the existing
`DailyRateLimiter` beside `lesson_generations_per_day` and the two message caps. Drafting is a
learner-triggered model call and therefore spend; the queue, the summary and grading are
side-effect-bounded reads and one small write, with no model call and no amplification, so they
get no knob. Phase 5 recorded that a limiter there would exist only for symmetry — the same
sentence with the opposite conclusion applies here, because this phase has the model call Phase 5
did not.

**Fetch frequency.** No `refetchInterval` anywhere in this phase except the drafting poll, which is
the Phase 1 `polling.ts` machinery at its existing cadence and stops the moment the run resolves.
The summary refetches on remount/refocus and on exactly two mutations (a keep, a grade); the queue
refetches on a grade. A learner grading ten cards issues ten summary invalidations, each a
single indexed scan of their own rows — acceptable and measurable, and `staleTime` is the knob if
it ever isn't.

**The queue's cost** is one indexed scan plus two `DISTINCT ON` sub-scans over *today's* reviews
only, which is bounded by the cap: at most ten distinct cards and, with lapses, a small multiple of
that. The expensive-looking part of §5.3's SQL is bounded by the day, not by history.

**Cache keys** carry the offset for the same reason Phase 5 D10's does — crossing a timezone or a
DST boundary is a cache miss and a refetch rather than a stale day boundary, and that falls out of
the key rather than needing logic:

```
["flashcards", "summary", tzOffset]
["flashcards", "queue", tzOffset, pathId ?? null]
["flashcards", "drafts", lessonId]
```

## 8. Frontend

**`lib/api.ts`** — `ReviewSummary`, `ReviewQueue`, `QueueCard`, `FlashcardDrafts` types mirroring
§6, plus `reviewSummaryQueryOptions(enabled)`, `reviewQueueQueryOptions(enabled, pathId)`,
`flashcardDraftsQueryOptions(lessonId, enabled)`. `getTimezoneOffset()` is called at the **one**
existing site (Phase 5 §8) and reused — a second call site would be a second place for the sign
convention to be wrong.

**Components** (Nocturne tokens, phone first):

- **`components/review/review-pill.tsx`** — `10 due` in the app header, the one piece of persistent
  navigation this phase adds. Hidden entirely at zero (a `0 due` pill is a debt notification, which
  PRD §3's restraint list forbids in every other form) and hidden when the summary query fails.
- **`components/review/due-today-card.tsx`** — home's `10 cards · ~4 min`, the `Review` action and
  the one-line provenance breakdown, above the path list and below the streak line.
- **`components/review/draft-list.tsx`** — the four drafts with per-card keep/discard, all keeping
  by default, the primary action naming its own count (`Keep 3 cards`) and `Skip — keep none`
  equally reachable. Rendered below the completion state in `lessons.$lessonId.tsx`.
- **`components/review/review-card.tsx`** — front, tap to reveal, then `Again · later today` and
  `Got it · in {got_it_interval_days} days`. The interval text comes from the payload, never from
  a ladder constant duplicated in TypeScript — the ladder is config (§13) and the client must not
  hold a second copy of it.
- **`components/review/session-complete.tsx`** — the end of the day's set. No "study more" button
  (PRD §4.4); the widen offer appears **only** when `scope_path_id` is set and `other_due_count > 0`.
- **`routes/review.tsx`** — `/review?path=…`. The scope chip renders `All paths` or the path title
  and offers no switcher (PRD §4.10).

**`routes/index.tsx`** gains the *Due today* card and a `Review N` chip in each `PathRow`, read from
the summary's `paths` array by `path_id` (absent → no chip, exactly as the streak chip already
behaves).

**Gating** — `useFeatureFlag("flashcards")` feeds every options factory's `enabled`; off means
`skipToken`, i.e. no request, no pill, no drafts block. No flag, no fetch.

**The two moments that must not wait for a round trip:**

1. **A grade** optimistically decrements the pill and advances the counter, then invalidates
   `["flashcards"]`. The authoritative refetch follows within one round trip, so any divergence
   self-corrects.
2. **The day's first review** advances the streak line — the same `Day 7 🔥` beat, in the same
   place, with the same restraint as the day's first completion. This is a `setQueryData` against
   the **progress** key from a **flashcards** mutation, which is the one piece of cross-domain cache
   wiring in the phase and therefore the one most likely to be silently dropped in a refactor. It
   gets its own test file, `src/app/review-refresh.test.tsx`, as the sibling of the
   `completion-refresh.test.tsx` that exists for exactly this class of bug, and it holds the same
   three properties: a no-op on a cold cache, a no-op on the second review of a day
   (`completed_today > 0`), and an authoritative refetch behind the bump.

**MSW** — `mocks/flashcards.ts` exporting `flashcardHandlers`, `configureFlashcards({…})` and
`resetFlashcards()`, composed into `handlers.ts` and reset in `tests/setup.ts`.

## 9. Instrumentation & observability

Three new events, `EVENT_FIELDS` extended, `tests/unit/test_events` anchoring each to real emission.

| Event | Fields (beyond `workflow`) | Fires |
| --- | --- | --- |
| `flashcards_drafted` | `account_id`, `path_id`, `lesson_id`, `position_in_path`, `drafted_count`, `outcome`, `success`, `duration_ms`, token triple | Every drafting run resolution, failures included |
| `flashcards_kept` | `account_id`, `path_id`, `lesson_id`, `drafted_count`, `kept_count` | The keep request — **both numbers on one event**, so *keep rate* is a ratio inside a row rather than a join between two event streams |
| `review_graded` | `account_id`, `card_id`, `path_id`, `grade`, `rung_before`, `queue_size`, `queue_remaining` | Every grade |

**No session events.** `review_session_started` / `_completed` would each be derivable from
`review_graded`: a session started is the first grade of a day, and one finished is a grade with
`queue_remaining = 0`. Carrying `queue_size` and `queue_remaining` on the grade buys both for the
cost of two integers, which is Phase 5 D9's posture — do not add an event you can compute — applied
where it actually saves something rather than used to justify adding nothing.

**Three saved queries**, in `queries/logfire/` and in `return_rate.sql`'s style:

| Metric (PRD §5) | Query | Events |
| --- | --- | --- |
| **Keep rate** | `flashcard_keep_rate.sql` | `flashcards_kept` |
| **Queue completion** | `review_queue_completion.sql` | `review_graded` |
| **Recall rate over rung** | `review_recall_by_rung.sql` | `review_graded` |
| **Does the retention loop move Return?** | `flashcard_return.sql` | `account_created`, `lesson_completed`, `quick_check_attempted`, `lesson_viewed` |

`flashcard_return.sql` is `streak_return.sql` with a different flip-date constant in its header —
deliberately a copy rather than a parameter, because the two cohort splits are different questions
about different flips and a shared query would make it easy to answer one while reading the other.
It inherits the same stated caveat: it buckets **UTC** days while the feature counts learner-local
days, so the two can disagree by one at the margins.

`docs/metrics.md` gains a *Phase 3* section with the four rows.

**The Phase 4 seam, named and not built:** lapses are queryable per learner and per source lesson
directly from `flashcard_reviews JOIN flashcards ON …` with `grade = 'again'`. No aggregation, no
surface, no API — the commitment PRD §5 makes is satisfied by the schema alone, which is the point.

## 10. Evals

**`flashcard_draft` is the third `ArtifactKind`** — and, recorded honestly, the **first actual one**:
`evals/rubric.py` still reads `Literal["outline", "lesson"]`, so the `tutor_reply` (Phase 2 D11) and
`path_proposal` (Phase 2B D13) precedents this phase inherits are specified rather than shipped.
Extending the kind axis is real work here, and §16 sizes it as such rather than as a one-liner
riding two existing extensions.

PRD §6's four dimensions split across the harness's two layers:

| PRD §6 dimension | Where | How |
| --- | --- | --- |
| **Non-triviality** (must not restate the Quick check's stem) | **Layer 1**, deterministic | `restates_stem` — normalized token overlap against the stem, shared with the agent's output validator (§5.2). The only one of the four that is honestly mechanical |
| **Scope** (one fact per card) | Layer 2, `in_scope` + an `ARTIFACT_NOTES` reading | Word caps pre-filter the worst cases; "one fact" is a judgement |
| **Grounding** (answerable from the Read passage, nothing invented) | Layer 2, `accurate` | The judge sees the passage and the card |
| **Independence** (the back stands alone, since the card outlives the lesson) | Layer 2, `in_scope` note | §4.11 is what makes this a correctness property rather than a style preference |

`APPLICABLE_ITEMS["flashcard_draft"] = ("accurate", "level_appropriate", "in_scope", "safe")` —
`continuous` and `check_valid` do not apply to a card and are omitted rather than auto-passed.
No new `RubricItem`: the `Literal` is shared across kinds, so adding an item would change what the
outline and lesson judges are asked, and `ARTIFACT_NOTES` exists precisely to say what an existing
item means for a new artifact.

A `flashcard_seed_set.yaml` reuses the Phase 1 seed topics so the passages under test are the ones
the lesson evals already judge, and **keep rate (§9) is the production proxy** that makes this
calibratable against real behaviour later rather than judge-only.

Opt-in as always: `just evals`, never part of `just gate` or the CI gate.

## 11. Testing strategy

Red-green TDD, fakes over mocks (CLAUDE.md).

**Unit — `tests/unit/test_scheduling.py`** (pure; the bulk of the value and the cheapest place to
buy it):

| Case | Expected |
| --- | --- |
| `initial_state` | rung 0, `due_on == kept_on + ladder[0]` — **never today** |
| `GOT_IT` from each rung | promotes one rung, `due_on == today + ladder[new]`; the top rung is a fixed point |
| `GOT_IT` on a card a week overdue | interval measured from **today**, not from the stale `due_on` |
| `AGAIN` from rung 0 / mid / top | demotes one, floors at 0, `due_on == today` in every case |
| `len(candidates) <= cap` | every candidate selected; the 7/3 split never runs |
| `len(candidates) > cap` | exactly `overdue_slots` most overdue + `cap - overdue_slots` others |
| Same inputs, repeated calls | identical output — the hash draw, not an RNG |
| Same candidates, different `seed` | a different random three (the draw is actually varying) |
| Different day, same backlog | the three are redrawn (PRD §4.4's "made fresh") |
| `satisfied` flipped on any subset | **the selected set is unchanged** — the D3 invariant, as a unit test |
| Ladder of length 1 | promotion is a no-op, nothing indexes out of range |

**Unit — the service, against fakes behind `Protocol`s:** the queue's serve order (never-attempted
before lapsed, lapsed least-recently-seen first); `completed`/`total` under lapses (a lapse never
changes `total`); `path_id` filtering leaves `total` alone — the denominator is the global set even
in a filtered session; an orphaned card appears globally and in no filter; `other_due_count` is
zero in an unfiltered session.

**Unit — the replay property (D1's whole justification):** given an arbitrary sequence of grades,
folding `apply_grade` over the review log reproduces `flashcards.rung` / `due_on` exactly. This is
the test that makes "drop the projection and rebuild" a true statement rather than an aspiration.

**Unit — the pin, end to end in memory:** derive today's ten, grade three, re-derive → the same ten,
in the same order, with three satisfied. Then grade one `again` → still the same ten, that card back
in the serve order behind the untouched ones.

**Unit — the drafting agent:** each shared validator; the prompt carries the stem but not the
options; caps rejected at construction when inverted (the `LessonCaps` precedent).

**Integration — `tests/integration/test_flashcards_api.py`** (real Postgres),
`@pytest.mark.workflow("W24"…"W27")` on the relevant cases:

- Drafting: trigger → poll → keep two → exactly two rows survive with `kept_at` set and
  `due_on = today + 1`; the discarded two are **gone**, not soft-deleted.
- Double `POST` while generating is a no-op (D7's claim), and a `failed` run is re-claimable.
- Keeping a draft id belonging to another lesson is `404` and mutates nothing.
- The queue caps at ten with eleven due, and returns the identical payload on a second request.
- Grading with a stale `rung_before` is `409` and appends **no** review row.
- Grading a card that is not in today's set is `409`.
- Another learner's cards never appear (the `user_id` predicate on the row itself, §4 item 3).
- Deleting the source path leaves the card reviewable with `source.kind == "degraded"`;
  regenerating the source lesson does the same (the `generated_at` stamp, D12).
- **The streak union:** a review-only day is an Active day globally and **is not** one for the path
  streak — one test, both halves, because the two are only correct together.
- `SET TIME ZONE 'America/Chicago'` on the session leaves the review-day expression unchanged
  (Phase 5 §14 R1's guard, extended to the new query).
- Flag off → `404` on every route, and the progress summary reverts to lesson completions alone.

**Frontend unit (vitest + MSW):** the pill hidden at zero and on error; the drafts block's default-kept
state and the primary action's live count; the reveal→grade flow; the interval label read from the
payload; the widen offer shown only in a filtered session with others due; and in
`review-refresh.test.tsx`, the three cross-domain cache properties (§8).

**E2E (Playwright, `mobile-390x844`)** — one spec per PRD workflow, each with the `//` prose header
the existing journeys carry:

- **W24 — finishing a lesson produces a due card.** Complete a lesson, keep two of four drafts,
  `shiftFlashcardDue({ days: 1 })`, reload → the pill reads `2 due` and both cards review.
- **W25 — the daily queue caps and holds.** Complete three lessons keeping four cards each (twelve),
  shift them due, open review → `Card 1 of 10`; reload → the same first card and the same ten.
- **W26 — a lapse resurfaces without costing a slot.** Grade `Again` on card 1 → the counter still
  reads `of 10`, the card returns after the untouched ones, and the session ends at ten distinct
  cards.
- **W27 — a card survives its source lesson.** Delete the path a kept card came from → the card
  still reviews and its source line is plain text, not a link.

**The clock (D15).** `scripts/e2e_backend.py` mounts one more route on the module-level `/__e2e__`
router the production factory never constructs:

```python
# create_stub_app only — create_app has no reference to this module.
@e2e_router.post("/__e2e__/shift-flashcard-due")
async def shift_flashcard_due(body: ShiftRequest, session: Session) -> None:
    """Backdate a learner's kept cards so a journey can observe a due queue."""
```

`UPDATE flashcards SET due_on = due_on - make_interval(days => :days) WHERE user_id = :user_id AND
kept_at IS NOT NULL`. A shift, not a seeder: it fabricates no cards, so the journeys earn their
twelve through the real drafting flow, and the database cannot reach a state the app could not.
`tests/unit/test_smoke.py`'s existing guard — the production app exposes no `/__e2e__` route —
covers it with no change.

**External:** none new. Drafting's live-provider contract is the `external/` shape the other agents
already use, and is a follow-up rather than a launch blocker.

## 12. Deployment & ops

No new secrets, services or `fly.toml` changes; one new env var with a default (`MODEL_FLASHCARD`).
Migration `0010` is three new tables — additive, online-safe on Neon, and reversible by dropping
them, with no change to any existing table.

Launch is one committed `FEATURE_FLAG_DEFAULTS` entry, following the flagged-phase runbook
([`deploy.md`](../deploy.md#launching-a-flagged-phase-al-270--al-370)); until then the flag is on for
admins only and every route `404`s for everyone else.

**Rollback is not as clean as Phase 5's, and it is worth saying exactly how.** Turning the flag off
returns the product to its prior state instantly — no route, no pill, no drafting, and the streak
falls back to lesson completions alone (§5.5). But **the cards and reviews remain**, and they
should: the failure mode this feature has is "the schedule is wrong", and the recovery for that is
to fix the ladder and replay (D1), not to drop the data. Dropping the tables is a data-loss
operation on learner-authored keeps and is not part of the rollback procedure.

## 13. Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `FLASHCARD_DAILY_CAP` | `10` | PRD §4.4 and open Q3 — a deploy, not a migration |
| `FLASHCARD_OVERDUE_SLOTS` | `7` | The random count is **derived** as `cap - overdue_slots`; validated `0 <= overdue_slots <= cap` at startup so the two can never disagree |
| `FLASHCARD_LADDER_DAYS` | `"1,3,7,14,30"` | Parsed like `MODEL_ALLOWLIST`; validated non-empty and strictly positive. habagou's shipped ladder |
| `FLASHCARD_DRAFTS_MIN` / `_MAX` | `3` / `5` | The count band the prompt targets and the validator gates (PRD open Q5) |
| `FLASHCARD_SECONDS_PER_CARD` | `25` | Only feeds `estimated_minutes` — the mock's `10 cards · ~4 min` |
| `FLASHCARD_DRAFTS_PER_DAY` | `50` | The rate-limiter cap (D13) |
| `MODEL_FLASHCARD` | `anthropic/claude-sonnet-5` | The new slot; listed in `MODEL_SLOTS`, which is what puts it behind the production stub guard |
| `FeatureFlag.FLASHCARDS` | `False` in `FLAG_DEFAULTS`, present in `ADMIN_DEFAULT_FLAGS` | D10 |

Drafting reuses `generation_timeout_seconds` / `generation_stale_after_seconds` rather than adding a
second pair — one answer to "how long may a model call run" is worth more than a knob per caller.

## 14. Answers to the PRD's open questions, and the one reversal

**The PRD's §8 open questions, as this document settles them:**

| # | PRD §8 | Settled |
| --- | --- | --- |
| 1 | The streak bar — one card, or the whole queue? | **Unchanged: one card.** §9's `queue_completion` is what would justify raising it, and it does not exist yet. Recorded as still open, because it is a product question this document should not close |
| 2 | Is the debt ever visible? | **No, and nothing here computes it.** `due_count` is the day's set, capped. The true outstanding total is not in any payload, so making it visible later is an additive change and cannot leak early |
| 3 | Is 10 right, and 7/3? | **Configuration** (§13), with the random count derived so the three numbers cannot disagree. §4.4 asks for the wait at realistic backlogs: with a backlog of *B* beyond the top seven, a mid-aged card's expected wait for a random slot is **≈ B/3 days** — about 8 days at *B* = 25, about 24 at *B* = 73. The three slots bound the wait; they do not make it short, and the honest reading is that a learner with a 70-card backlog will not see some of it for three weeks |
| 4 | Its own model slot? | **Yes** (D13) |
| 5 | How many cards per lesson? | **A 3–5 band the prompt targets and a validator enforces** (§13), not a function of passage length — the passage band is only 200–500 words, so there is very little to scale over, and a band the agent can use judgement inside is a better fit than an arithmetic rule |

**Three product rules this document had to decide, all confirmed with the owner:**

- **A freshly kept card is due tomorrow, never today.** It falls out of entering at rung 0 with
  `ladder[0] = 1` — there is no special case (§5.1). Four lessons in an evening therefore cannot
  hand a learner sixteen cards on a screen that promises ten.
- **A lapse re-shows without bound** — the day's set is done when all ten have been answered once
  (D8). The learner can always stop by leaving, and the card is demoted and rescheduled either way.
- **Abandoned drafts wait.** A learner who completes a lesson and navigates away without tapping
  finds the drafts still there. The run row stays `generated`, the unkept rows persist, and the
  keep step is resumable from the completed lesson (D7).

**R1 — one reversal inside this document, recorded rather than quietly rewritten.** The due count
was first designed as an extension of `GET /progress/summary`, on Phase 5 D4's explicit invitation
for that payload to grow. It moved to its own route (D9) for two reasons that only became visible
once the surfaces were drawn: the pill is mounted on **every** route while the streak line is
home-only, so a single query would refetch a streak on screens that never show one; and one
router-level gate cannot resolve two independent flags, so a shared payload would need
flag-conditional fields — which is exactly how a kill switch stops being a kill switch.

**No corrections to the PRD.** Its §§1–4 survive this design unchanged; §4.5's "chosen on the first
request of the learner's local day" turned out to describe a derivation rather than a write, which
is a happier outcome than the mechanism it appeared to require.

## 15. Risks & open questions

- **The pin's invariant is the highest-consequence, lowest-visibility surface here** (§5.3). If the
  candidate population ever stops being stable across a day — a card deleted mid-day, a `due_on`
  written by something other than a grade, a bug in the `COALESCE` — the day's ten silently reroll
  under a learner mid-session, and the symptom ("I swear there were different cards a minute ago")
  is one nobody files. The mitigations are structural rather than careful: one writer of `due_on`,
  a unit test that flips `satisfied` on every subset and asserts the selection is unchanged, and an
  integration test that grades and re-reads. If a report ever suggests the queue changed, that test
  is where to start and it should be *made to fail* first.
- **The projection and the log can skew.** D1 makes the log authoritative, which is only meaningful
  because §5.4 writes both in one transaction and §11 proves replay reproduces the projection. A
  partial write is a bug, not tolerated skew — recorded here as ADR 0008's consequence, inherited
  along with its design.
- **Two definitions of "day" now coexist** (§5.5): the frozen `local_day` the scheduler uses and the
  recomputed streak day. A travelling learner can see them disagree by one. Accepted, for the same
  reason Phase 5 accepted its version — the alternative is an account timezone column that goes
  stale and needs a settings UI to fix.
- **Cards accumulate and nothing prunes them.** No leech detection, no suspend, no delete (PRD §7).
  A learner who keeps everything will build a backlog whose tail they see every few weeks (§14 Q3),
  and this phase's only answer is that the cap protects the *session* rather than the deck. If the
  queue-completion metric says learners abandon sessions, the cap is the wrong knob and leech
  handling is the right feature — that is a Phase 3 follow-on, not a tuning exercise.
- **Drafting is the phase's only spend and its quality is unmeasured until launch.** Keep rate (§9)
  is the production proxy and the evals (§10) are the pre-launch proxy, but the judge has never
  been calibrated on this artifact and `docs/evals.md` records that no live run has happened on any
  artifact yet. A low keep rate is an AI-quality problem, and the first place to look is the
  count band and the stem-restatement pre-filter, not the UI.
- **Open: does `queue_remaining = 0` really mean "finished"?** It is what §9's completion metric
  counts, and it is satisfied by a learner who grades their tenth card — but not by one who
  reviewed seven and left, which reads as abandonment when it may be a perfectly good session. If
  the metric looks pessimistic in practice, the fix is a second number (cards graded ÷ set size)
  rather than a new event.
- **Open: should the *Due today* card survive a finished queue?** Today it disappears when the set
  is done, which is the restrained choice, and it means the home screen gives a returning learner
  no evidence they did anything. `completed_today` already exists on the streak line, so the cheap
  answer is to let the streak line carry it — recorded rather than built.

## 16. Tickets

GitHub issues, cut from this document in a follow-up PR — issues are the source of truth (the
Phase 1 / 2 / 2B / 5 pattern).

- **Label:** `tdd-flashcards`; parent epic carrying shared context and the dependency graph.
  `for-ai` / `for-human` split as before — expected `for-human` surface: the drafting prompt's
  voice, the eval rubric notes, and the production ship verification.
- **Numbering:** AL-5xx, in dependency order.

| # | Commit | Scope |
| - | ------ | ----- |
| 0 | `docs:` | `CONTEXT.md` — the *Retention* section (**Flashcard**, **Draft**, **Kept card**, **Due**, **Daily queue**, **Review**, **Lapse**) and the phase-boundary bullet moved off "specified, not built". `roadmap.md` — Phase 3's status blockquote. **Precedes the code**: the vocabulary is authoritative |
| 1 | `feat:` | `domains/scheduling.py` + `tests/unit/test_scheduling.py` + the config band. Pure, no dependencies, mergeable alone |
| 2 | `feat:` | Migration `0010`, `models/flashcard.py`, `repositories/flashcards.py`, the replay test |
| 3 | `feat:` | `agents/flashcard.py` + shared validators + the stub's drafting branch |
| 4 | `feat:` | `services/flashcard_drafting.py`, the three draft routes, `FeatureFlag.FLASHCARDS`, the rate-limit cap, integration tests, `docs/api.md` |
| 5 | `feat:` | `services/reviews.py`, the queue/summary/grade routes, the pin's integration tests |
| 6 | `feat:` | The streak union (D11) — the second reader, the union, the both-halves test |
| 7 | `feat:` | Frontend: api client, the pill, the *Due today* card, the drafts block, `/review`, msw handlers, component tests, `review-refresh.test.tsx` |
| 8 | `feat:` | The `flashcard_draft` eval kind, seed set, judge prompt, layer-1 pre-filters |
| 9 | `feat:` | Three events + three saved queries + `docs/metrics.md` |
| 10 | `test:` | `shift-flashcard-due` + the fixture + W24–W27 |

Steps 1, 2 and 3 are parallel; 4 depends on 2 and 3; 5 depends on 1 and 2; 6 depends on 2; 7 depends
on 4 and 5; 8 depends on 3; 9 and 10 are last. Launch (the flag flip) is a separate change after
dogfooding.

**Rough size:** ~1 500 lines of production code and ~1 300 of tests — roughly twice the streaks
slice, and the largest phase since Phase 1. The backend is genuinely bigger this time: three tables,
an agent, a scheduler and a derivation with an invariant to defend.

## Appendix — traceability (PRD's TDD-owned items)

| PRD delegation | Here |
| --- | --- |
| A card belongs to the learner and outlives its lesson (§4.1) | §4 — `user_id` on the row, both source FKs `SET NULL`, titles copied |
| Aleph proposes, the learner disposes (§4.2) | D6, §5.2 — nothing enters the schedule without `kept_at`; the partial index makes that structural |
| One schedule, global; path is a filter (§4.3) | §5.3 — the selection always runs globally and `path_id` filters the result; the chips sum to the global ten |
| Ten a day, 7 overdue + 3 random (§4.4) | D2/§5.1's `select_daily_queue`, §13's config, §14 Q3's wait arithmetic |
| The day's queue is decided once (§4.5) | **D3 and §5.3's invariant** — derived, not stored, and pinned by construction |
| Two grades on a fixed ladder (§4.6) | §5.1's ladder semantics; `Grade` is a two-member enum, so a third grade is a schema change and not a config mistake |
| A lapse does not cost another card its slot (§4.7) | D8 — `total` is the selected set's size and `satisfied` counts distinct cards |
| Home shows today's ten, not the backlog (§4.8) | §6 — no payload anywhere carries the outstanding total |
| A review keeps the streak alive (§4.9) | D11, §5.5 — the union, and the per-path fold left untouched |
| Scope is chosen at the door (§4.10) | §8 — `/review?path=` and no switcher; the widen offer exists only at the end of a filtered session |
| The citation degrades honestly (§4.11) | D12 — FK `SET NULL` + the `generated_at` stamp + copied titles; a discriminated `source` object |
| Evals on the drafted card (§6) | §10 — the third artifact kind, four dimensions across two layers |
| The Phase 4 seam, named not built (§5) | §9 — the log satisfies it with no aggregation, no surface, no API |
| Restraint — never a push, a warning, a debt count (§3) | Nothing in §6 can express any of them: every route is request-scoped, there is no scheduler or transport anywhere in the phase, and the pill hides at zero |
