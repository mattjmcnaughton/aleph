# Metrics — product events → Logfire queries (AL-070)

Aleph emits **product events** (PRD §5.7) as structured Logfire log records, and
the PRD §7 success metrics are computed as **saved Logfire SQL queries** over
those records. This document is the metric → query map. The queries live in
[`queries/logfire/`](../queries/logfire/) and are meant to be imported as saved
queries / dashboard tiles in Logfire (AL-103).

> **"Computable is verified, not assumed."** The event field vocabulary is defined
> once in [`src/aleph/events.py`](../src/aleph/events.py) (`EVENT_FIELDS`), anchored
> to real emission by `tests/unit/test_events.py`, and every attribute each query
> references is checked against that manifest by
> `tests/unit/test_metrics_queries.py`. So a query can never reference a field no
> event actually emits.

## The events (PRD §5.7)

Each event is a Logfire log record whose `span_name` is the event name and whose
fields are record `attributes`. Every event carries `account_id` and a `workflow`
tag (§12), plus the ids that apply. The record's own timestamp is the event time.

| Event | Emitted from | Key fields (beyond `account_id`, `workflow`) | Workflow |
| --- | --- | --- | --- |
| `account_created` | `services/auth.py` — new-account provision only | — | W1 |
| `path_created` | `services/generation.py` `create_path` | `path_id`, `path_level` | W1 |
| `outline_generated` | `services/generation.py` — fenced outline resolution | `path_id`, `outcome` (ready/failed/refused), `success`, `duration_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens` | W1 / W8 / W7 |
| `lesson_generated` | `services/generation.py` — fenced lesson resolution | `path_id`, `lesson_id`, `position_in_path`, `outcome` (generated/failed), `success`, `duration_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens` | W1 / W8 |
| `lesson_viewed` | `routers/v1/lessons.py` `GET /lessons/{id}` | `path_id`, `lesson_id`, `position_in_path` | W1 |
| `quick_check_attempted` | `routers/v1/lessons.py` `attempt` | `path_id`, `lesson_id`, `position_in_path`, `outcome` (correct/incorrect), `is_correct` | W6 |
| `lesson_completed` | `routers/v1/lessons.py` `complete` — real transition only | `path_id`, `lesson_id`, `position_in_path` | W1 |
| `path_completed` | `routers/v1/lessons.py` `complete` — derives when no lesson is incomplete | `path_id`, `lesson_count` | W3 |
| `path_deleted` | `routers/v1/paths.py` `DELETE` | `path_id` | W5 |
| `tutor_conversation_started` | `services/tutor.py` — settle transaction, on the lazy `created` flag | `path_id`, `lesson_id`, `position_in_path` | W9 |
| `tutor_message_sent` | `services/tutor.py` `admit` — turn admitted | `path_id`, `lesson_id`, `position_in_path`, `source` (typed/suggestion) | W9 |
| `tutor_reply_completed` | `services/tutor.py` — every reply resolution | `path_id`, `lesson_id`, `position_in_path`, `outcome` (success/failure/stopped), `success`, `ttft_ms`, `duration_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens` | W9 / W14 |
| `tutor_check_shown` | `services/tutor.py` — `pose_tutor_check` observed mid-stream | `path_id`, `lesson_id`, `position_in_path` | W12 |
| `tutor_check_answered` | `routers/v1/tutor.py` `answer_tutor_check` | `path_id`, `lesson_id`, `position_in_path`, `outcome` (correct/incorrect), `is_correct`, `first_answer` | W12 |
| `shaping_conversation_started` | `services/shaping.py` — settle transaction, on the lazy `created` flag | `path_id` | W17 |
| `shaping_message_sent` | `services/shaping.py` `admit` — turn admitted | `path_id`, `source` (typed/suggestion) | W17 |
| `shaping_reply_completed` | `services/shaping.py` — every reply resolution | `path_id`, `outcome` (success/failure/stopped), `success`, `ttft_ms`, `duration_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `has_proposal` | W17 |
| `proposal_shown` | `services/shaping.py` — `propose_path_edit` observed mid-stream | `path_id`, `n_add_lessons`, `n_revisions`, `new_unit` | W17 / W18 |
| `change_applied` | `services/shaping.py` `apply_change` — after the commit | `path_id`, `change_id`, `n_add_lessons`, `n_revisions`, `new_unit`, `lesson_ids` | W17 / W18 |
| `change_undone` | `services/shaping.py` `undo_change` — after the commit | `path_id`, `change_id`, `minutes_since_apply` | W19 |
| `flashcards_drafted` | `services/flashcard_drafting.py` — every drafting run resolution *except* a missing-context run and a crashed worker (see below) | `path_id`, `lesson_id`, `position_in_path`, `drafted_count`, `outcome` (generated/failed), `success`, `duration_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens` | W24 / W8 |
| `flashcards_kept` | `services/flashcard_drafting.py` `keep_flashcard_drafts` — the keep request | `path_id`, `lesson_id`, `drafted_count`, `kept_count` | W24 |
| `review_graded` | `services/reviews.py` `grade_card` — every grade | `card_id`, `path_id`, `grade`, `rung_before`, `queue_size`, `queue_remaining` | W25 / W26 |

`outline_generated` / `lesson_generated` are emitted **only on a fenced-win mark**
(a lost claim's mark is a silent no-op), so each generation resolves the metric
exactly once. `lesson_completed` / `path_completed` fire only on the real
transition, never an idempotent re-complete.

The tutor's five (Phase 2 TDD §9) are stamped with the full lesson locator
because every §7 tutor metric is per-lesson. Two timings differ from Phase 1 on
purpose: `tutor_message_sent` fires at **admission**, not persistence (a reply
that later fails is our failure, not an un-asked question — and D2 persists
nothing on failure), while `tutor_conversation_started` fires **after the settle
commit**, off the lazy upsert's own `created` flag, so it is exactly one per
path.

Shaping's six (Phase 2B TDD §9) carry **no lesson locator at all** — a shaping
turn is about the path as a whole (PRD §5.1), which is the whole reason it is a
second conversation. What an *edit* touched rides `change_applied.lesson_ids`
instead: the ids of every lesson the Change created or revised, which is the
join key the primary metric needs. The three timing rules are 2A's, applied to
this surface: `shaping_message_sent` at **admission**; `shaping_conversation_started`
**after the settle commit** off the `created` flag; `change_applied` /
`change_undone` **after their own commit** (and outside the per-path apply lock),
so no event can claim structure a rolled-back transaction did not leave behind.

Two workflow tags are **derived from the payload**, not fixed: `proposal_shown`
and `change_applied` are `W17` when the edit adds anything and `W18` when it only
revises — the same dominance rule the `path_changes.kind` column uses, so one
Apply carrying both shapes is tagged the same way in the events and in the row.
W20 (a declined edit) deliberately tags nothing: a decline is an ordinary
successful reply, and W21 tags the guardrail *queries* rather than any record.

Phase 3's three (flashcards & spaced repetition, TDD §9) have **no session
events**: `review_session_started` / `_completed` are each derivable from
`review_graded` alone — a session started is an account's first grade of a
day, one finished is a grade with `queue_remaining = 0` — so `review_graded`
carries `queue_size`/`queue_remaining` and no separate event exists for
either. `flashcards_drafted` is emitted only on a **fenced win** (the
`lesson_generated` precedent), and **two** resolutions emit nothing at all —
read the row count against a failure-rate computed from this event with both
gaps in mind. The first is documented and intentional: a lesson whose content
vanished or was never generated before drafting was claimed emits nothing,
since there is no `account_id`/`path_id` to stamp it with. The second is not
a design choice — a **crashed worker** (a Fly machine restart, a task
cancelled at shutdown) leaves its `flashcard_draft_runs` row `generating`
forever; the row is only ever collapsed to `failed` on *read*
(`FlashcardRepository.get_effective_draft_run_state`), and nothing ever
resolves it, so no `flashcards_drafted` event is ever emitted for that run.
A drafting failure rate computed from this event therefore undercounts
exactly the failure mode that matters most. Two workflow tags are **derived from
the payload**, the same dominance style as `proposal_shown`/`change_applied`:
`flashcards_drafted`'s `outcome` picks `W24` (generated) or `W8` (failed, the
same generic tag `lesson_generated`'s own failed branch uses), and
`review_graded`'s `grade` picks `W26` (`again` — a lapse resurfacing) or `W25`
(`got_it` — the ordinary queue-draining case). `path_id` is **nullable** on
`flashcards_drafted` and `review_graded` alone among every event in this
document: an orphaned card (its source path deleted, D12) still drafts or
reviews, and `None` here is what keeps that case honest rather than
mis-attributed to a path the card no longer has. `flashcards_kept` carries
**both** `drafted_count` and `kept_count` on one record — the keep-rate ratio
lives inside a row rather than a join between two event streams (TDD §9).

## Metric → query

### North star

| Metric (PRD §7) | Query | Events consumed |
| --- | --- | --- |
| **Activation rate** — % of new accounts activated (>3 attempted-and-completed lessons on one path) within 7 days | [`activation_rate.sql`](../queries/logfire/activation_rate.sql) | `account_created`, `lesson_completed`, `quick_check_attempted` |

### Supporting

| Metric (PRD §7) | Query | Events consumed |
| --- | --- | --- |
| **First-lesson activation** — completes a lesson (with Attempt) in first session | [`first_lesson_activation.sql`](../queries/logfire/first_lesson_activation.sql) | `account_created`, `path_created`, `lesson_viewed`, `lesson_completed`, `quick_check_attempted` |
| **Path start rate** — generated paths whose learner starts lesson 1 | [`path_start_rate.sql`](../queries/logfire/path_start_rate.sql) | `outline_generated`, `lesson_viewed` |
| **Lesson-to-lesson continuation** — completed lessons followed by starting the next | [`continuation.sql`](../queries/logfire/continuation.sql) | `lesson_completed`, `lesson_viewed`, `path_completed` |
| **Return** — activated learners back on a 2nd distinct day | [`return_rate.sql`](../queries/logfire/return_rate.sql) | `account_created`, `lesson_completed`, `quick_check_attempted`, `lesson_viewed` |
| **Breadth** — activated learners running 2+ paths | [`breadth.sql`](../queries/logfire/breadth.sql) | `account_created`, `lesson_completed`, `quick_check_attempted`, `path_created` |

### Guardrails / counter-metrics

| Metric (PRD §7) | Query | Events consumed |
| --- | --- | --- |
| **Generation cost per path** (token proxy) | [`cost_per_path.sql`](../queries/logfire/cost_per_path.sql) | `outline_generated`, `lesson_generated` |
| **Generation failure rate + p95 latency** | [`generation_failure_latency.sql`](../queries/logfire/generation_failure_latency.sql) | `outline_generated`, `lesson_generated` |
| **Quick-check correctness rate** | [`quick_check_correctness.sql`](../queries/logfire/quick_check_correctness.sql) | `quick_check_attempted` |

### Phase 2 — the tutor (PRD §7)

Phase 2 gets no north star of its own: Phase 1's activation rate stays it, and
this phase's job is to *move* it. So the primary metric is the compounding claim,
stated so it can fail.

| Metric (Phase 2 PRD §7) | Query | Events consumed |
| --- | --- | --- |
| **Tutor-assisted continuation** (primary) — among activated learners, continuation for lessons *with* a tutor message vs. without | [`tutor_assisted_continuation.sql`](../queries/logfire/tutor_assisted_continuation.sql) | `account_created`, `lesson_completed`, `quick_check_attempted`, `path_completed`, `lesson_viewed`, `tutor_message_sent` |
| **Tutor adoption** — % of activated learners who send ≥1 tutor message | [`tutor_adoption.sql`](../queries/logfire/tutor_adoption.sql) | `account_created`, `lesson_completed`, `quick_check_attempted`, `tutor_message_sent` |
| **Repeat use** — % of tutor users who use it in more than one lesson | [`tutor_repeat_use.sql`](../queries/logfire/tutor_repeat_use.sql) | `tutor_message_sent` |
| **Depth** — median/p95 turns per conversation and per lesson-with-tutor-use (also the turns-per-conversation guardrail) | [`tutor_depth.sql`](../queries/logfire/tutor_depth.sql) | `tutor_message_sent` |
| **Entry mix** — share of messages from a suggestion vs. free text | [`tutor_entry_mix.sql`](../queries/logfire/tutor_entry_mix.sql) | `tutor_message_sent` |
| **Tutor check uptake** — % of tutor users who take ≥1 Tutor check | [`tutor_check_uptake.sql`](../queries/logfire/tutor_check_uptake.sql) | `tutor_message_sent`, `tutor_check_shown`, `tutor_check_answered` |
| **Not a crutch** (guardrail) — lesson completion rate with vs. without tutor use | [`tutor_completion_guardrail.sql`](../queries/logfire/tutor_completion_guardrail.sql) | `lesson_viewed`, `lesson_completed`, `tutor_message_sent` |
| **Reply failure rate + TTFT/duration p95** (guardrail) | [`tutor_reply_failure_latency.sql`](../queries/logfire/tutor_reply_failure_latency.sql) | `tutor_reply_completed` |

Phase 2 adds **no cost metric of its own** (PRD §7): Logfire already records
per-call tokens on every pydantic-ai model-call span, and
`tutor_reply_completed` carries the per-reply token triple for the same reading
from the events alone.

Two more §7 items are deliberately not new queries. **Quick-check correctness**
stays Phase 1's [`quick_check_correctness.sql`](../queries/logfire/quick_check_correctness.sql)
unchanged — Phase 2 watches it as the answer-leak counter-metric (a sharp rise
is a §10 leak to investigate, not a win), and it is not sliced by tutor use:
`quick_check_attempted` carries no tutor dimension, so the split is an ad-hoc
join against `tutor_message_sent` on `lesson_id` rather than a saved tile. And
**eval pass rate** is not event-derived at all — it comes from the eval harness
([`docs/evals.md`](evals.md)), not from Logfire records.

### Phase 2B — shaping (PRD §7)

Phase 2B gets no north star of its own either: Phase 1's activation rate stays
it. The primary metric is this phase's compounding claim — that learners who can
bend their path stick with it — stated so it can fail.

| Metric (Phase 2B PRD §7) | Query | Events consumed |
| --- | --- | --- |
| **Shaping yield** (primary) — of applied Changes, the share whose created/revised lessons the learner engages with within 7 days | [`shaping_yield.sql`](../queries/logfire/shaping_yield.sql) | `change_applied`, `quick_check_attempted`, `lesson_completed` |
| **Shaping adoption** — % of learners with a ready path who apply ≥1 Change | [`shaping_adoption.sql`](../queries/logfire/shaping_adoption.sql) | `outline_generated`, `change_applied` |
| **Proposal acceptance** — applied / proposed | [`proposal_acceptance.sql`](../queries/logfire/proposal_acceptance.sql) | `proposal_shown`, `change_applied` |
| **Edit-shape mix** — additions vs revisions, proposed and applied | [`edit_shape_mix.sql`](../queries/logfire/edit_shape_mix.sql) | `proposal_shown`, `change_applied` |
| **Undo rate + time to undo** (guardrail) | [`undo_rate.sql`](../queries/logfire/undo_rate.sql) | `change_applied`, `change_undone` |
| **Depth to proposal** — median messages before the first Proposal | [`depth_to_proposal.sql`](../queries/logfire/depth_to_proposal.sql) | `shaping_message_sent`, `proposal_shown` |
| **Not hoarding** (guardrail, W21) — lesson completion on shaped vs unshaped paths | [`shaped_path_completion_guardrail.sql`](../queries/logfire/shaped_path_completion_guardrail.sql) | `lesson_viewed`, `lesson_completed`, `change_applied` |
| **Shaping reply failure rate + TTFT/duration p95** (guardrail, W21) | [`shaping_reply_failure_latency.sql`](../queries/logfire/shaping_reply_failure_latency.sql) | `shaping_reply_completed` |

Three §7 items are deliberately **not** new queries here.

**Generation spend per path** stays the existing reading: additions and revisions
buy real Phase 1 generations, so they already land on
[`cost_per_path.sql`](../queries/logfire/cost_per_path.sql) and on the
per-call pydantic-ai model-call spans, bounded by the existing caps. The shaper's
own token use rides `shaping_reply_completed`'s triple for the same
event-only reading `tutor_reply_completed` gives 2A. **Quick-check correctness on
revised lessons** stays Phase 1's
[`quick_check_correctness.sql`](../queries/logfire/quick_check_correctness.sql),
sliced ad hoc by the revised lesson ids in `change_applied.lesson_ids` — a
revision that makes checks trivially easy would inflate a Phase 1 guardrail, and
the ids to slice on are already on the event. **Eval pass rate** is not
event-derived at all (see [`docs/evals.md`](evals.md)), and this phase's evals
run post-launch.

### Phase 5 — streaks (PRD §5, TDD §9)

Streaks gets no new event and no north star of its own: Phase 1's activation
rate stays it, and the one question this slice asks is whether it moves the
existing **Return** metric at all.

| Metric (Phase 5 PRD §5) | Query | Events consumed |
| --- | --- | --- |
| **Streak return** — the existing Return metric, split into before/after cohorts by the `streaks` flag flip date | [`streak_return.sql`](../queries/logfire/streak_return.sql) | `account_created`, `lesson_completed`, `quick_check_attempted`, `lesson_viewed` |

**No new event, on purpose** (D9): `lesson_completed` already carries
`account_id` and a timestamp since Phase 1, so streak length — and therefore a
before-cohort — is computable retroactively, including for every account that
signed up before this slice shipped. A `progress_summary_viewed` event would
mostly have counted invalidation refetches the client fires on every
completion, which reads as engagement without being it. The flag-flip date the
cohort split needs is a dated constant in `streak_return.sql`'s header, not a
column — there is no event for it, and inventing one would be a worse kind of
precision than a comment that says when to update it.

If Return does not move for the "after" cohort, this slice is decoration and
the rest of Phase 5's scope should be re-argued rather than built — the one
sentence in the TDD that could stop the phase.

### Phase 3 — flashcards & spaced repetition (PRD §5, TDD §9)

Phase 3 gets no north star of its own either: the one question worth asking
(PRD §5) is the same shape as Phase 5's — does the retention loop move the
existing **Return** metric — stated so it can fail the same way.

| Metric (PRD §5) | Query | Events consumed |
| --- | --- | --- |
| **Keep rate** — kept ÷ drafted | [`flashcard_keep_rate.sql`](../queries/logfire/flashcard_keep_rate.sql) | `flashcards_kept` |
| **Queue completion** — sessions finished ÷ started | [`review_queue_completion.sql`](../queries/logfire/review_queue_completion.sql) | `review_graded` |
| **Recall rate by rung** — Got it ÷ reviews, by ladder rung | [`review_recall_by_rung.sql`](../queries/logfire/review_recall_by_rung.sql) | `review_graded` |
| **Does the retention loop move Return?** — the existing Return metric, split into before/after cohorts by the `flashcards` flag flip date | [`flashcard_return.sql`](../queries/logfire/flashcard_return.sql) | `account_created`, `lesson_completed`, `quick_check_attempted`, `lesson_viewed` |

`flashcard_return.sql` is `streak_return.sql` with a **different flip-date
constant** in its header — deliberately a copy rather than a parameter (TDD
§9): the streak cohort split and the flashcards cohort split are different
questions about different flags, and a shared, parameterised query would make
it easy to answer one while reading the other. It inherits the same stated
caveat `streak_return.sql` carries: it buckets **UTC** days while the feature
itself counts learner-local days (`tz_offset_minutes`, D4), so the two can
disagree by one at the margins.

**Keep rate** and **recall rate by rung** are read per-lesson-run and
per-rung respectively, never pooled across the whole cohort at once — a low
number hiding inside a healthy pooled average is exactly the failure mode both
queries exist to catch early (PRD §5, TDD §10's "keep rate is the production
proxy" for the `flashcard_draft` eval).

**AL-400 changed what `flashcards_drafted` counts — but not keep rate.** The
trigger moved from the lesson's completion to its *opening* (TDD D5), so
`flashcards_drafted` now fires once per generated, unlocked lesson whose open
actually **won a claim**, rather than once per lesson completed. It is a claim
count, not an open count: revisiting a lesson whose run is already `generated`
is a D7 no-op that emits nothing, so a learner reopening the same lesson ten
times still contributes one event.

**Keep rate is unaffected**, and by construction rather than luck:
`flashcard_keep_rate.sql` reads `flashcards_kept` alone, and a
`flashcards_kept` record only exists where a learner reached a keep screen —
which still requires completing the lesson. The same "both counts on one row"
design that makes the metric immune to drafting-retry timing makes it immune
to this change.

What is genuinely new is the **gap between the two streams**: `flashcards_drafted`
now counts drafting runs that were never offered to anybody, because the
learner opened the lesson and never came back to finish it. That population did
not exist before — every drafted run used to belong to a completed lesson — and
it is real spend (a model call, and a unit of `flashcard_drafts_per_day`, which
since AL-400 bounds lessons **opened** rather than lessons completed). Read
`cost_per_path.sql` and any drafting-failure rate with that in mind: their
denominators grew, and the growth is concentrated in lessons nobody finished.

**The Phase 4 seam, named and not built** (PRD §5, TDD §9): lapses are
queryable per learner and per source lesson directly from
`flashcard_reviews JOIN flashcards ON flashcard_reviews.card_id =
flashcards.id WHERE grade = 'again'` — the append-only review log already
carries every fact Phase 4's "what does this learner keep getting wrong"
needs, keyed to the card's `source_lesson_id`. No aggregation, no surface, no
API, and no query file in this phase: the schema alone is the commitment PRD
§5 makes, and building anything on top of it now would be work with no
consumer.

## Importing the queries into Logfire

Every file in [`queries/logfire/`](../queries/logfire/) is a saved query / tile
to import by hand — there is no API-driven sync, so the import list is the
checklist in [deploy.md § Logfire saved queries](deploy.md#logfire-saved-queries-import-checklist),
which also records what to import at each phase launch and the one panel that
reads empty for its first week.

## Notes & known limits

- **Retention bounds cohort history** (TDD §9, accepted risk): long-window metrics
  cannot look back past Logfire's retention, and analytics history does not survive
  path deletion elsewhere. If this becomes limiting, the fallback is a Postgres
  events table behind the same `events.py` seam — the swap is additive.
- **"Day" is UTC here.** The queries `date_trunc('day', …)` in UTC; the
  learner-local-timezone refinement (PRD §5.7 / CONTEXT.md "Day") is a follow-up.
- **Dollar cost** proper comes from the pydantic-ai model-call spans (token/cost per
  call, `instrument_pydantic_ai`), grouped by the same `path_id`;
  `cost_per_path.sql` is the event-derived token proxy that makes the metric
  computable from the product events alone. It sums **every** generation outcome,
  not just successes: a refusal (W7) and any billed partial output on a failure
  (W8) are real spend, so a success-only filter would understate cost. Failed
  resolutions that carried no usage contribute 0 tokens.
- **Cost per _activated learner_** — the other half of the guardrail (spend against
  the value it buys) is **not yet a saved query**. It is computable by joining the
  per-path token sums to the `activated` CTE (activation_rate.sql) keyed by
  `account_id`; deferred as a cheap follow-up rather than shipped here.

## Metric semantics & caveats

The `activated` cohort definition (`activation_rate`, `breadth`, `return_rate`) is
**one definition, applied identically**: >3 completed-and-attempted lessons on a
**single path**, each completion **within 7 days of signup** (CONTEXT.md "Activated
learner"). All three queries join `account_created` for the signup timestamp and
apply the 7-day window — the executed replay test
(`tests/integration/test_metrics_replay.py`) pins that an out-of-window account
does not count in any of them.

Accepted, documented limitations:

- **Activation-rate cohort clamp.** `activation_rate.sql` counts only accounts whose
  7-day window has already closed (`signed_up_at < now() - 7 days`). Without it,
  accounts younger than 7 days — which *cannot* have activated yet — would dilute
  the denominator and bias the north star low. `breadth`/`return` do not clamp: they
  are ratios *within* the activated set, not against the full cohort.
- **"Day" is UTC.** `return_rate` and `first_lesson_activation` bucket time in UTC
  (`date_trunc('day', …)` / the session gap); the learner-local-timezone refinement
  (PRD §5.7 / CONTEXT.md "Day") is a follow-up. A learner active across a UTC
  midnight but within one local day can read as two days.
- **A view before content counts as a "start."** `lesson_viewed` fires on every
  poll of `GET /lessons/{id}`, including polls before the content has generated, so
  `path_start_rate` / `continuation` count a *view* (intent to start), not a
  confirmed content impression. Intentional — the metric is "did the learner move
  to the next lesson", and distinct-position counting makes repeat polls harmless —
  but it is a view, not a read.
- **Continuation excludes final lessons only for _completed_ paths.** A path's last
  lesson has no "next" and would deflate continuation as a denominator artefact.
  `continuation.sql` drops the final position using `path_completed.lesson_count`,
  which only exists once a path completes; an in-progress path's true last lesson is
  unknown from events alone and is still counted. This slightly deflates
  continuation for long-running incomplete paths.
- **Repeat Quick-check submits are not Attempts.** `quick_check_attempted` is emitted
  only on the first-wins Attempt (the route gates emission on the `created` flag —
  CONTEXT.md / AL-012), so a learner resubmitting the same Quick check does not
  inflate the `quick_check_correctness` denominator or the activation gate.
- **`workflow` is a coarse segmentation tag,** not a per-event unique key; several
  events share `W1`. It is for slicing dashboards by PRD workflow, not for counting.
- `path_completed` / `path_deleted` are required §5.7 events but feed no single §7
  metric (they support completion/reset operational views); they are emitted and
  tested, and available for ad-hoc queries.
- The `records` column names (`span_name`, `attributes`, `start_timestamp`) follow
  Logfire's SQL schema; log records emitted via structlog land with the event name
  as `span_name` and each field as a JSON attribute (see `tests/unit/test_logging.py`).

Tutor-specific caveats (Phase 2):

- **Tutor-assisted continuation is a correlation, not a causal claim.** Learners
  who ask questions are plausibly the learners who were going to continue
  anyway; nothing randomises assignment. Read it as "the tutor is not associated
  with abandonment, and is associated with continuing", always beside
  `tutor_adoption` (a large gap on 2% adoption is noise) and
  `tutor_completion_guardrail` (the "not a crutch" counter-metric). It inherits
  every caveat of Phase 1's `continuation.sql`, including the final-position
  exclusion applying only to *completed* paths.
- **A message counts from admission, a conversation from the commit.** A turn
  whose reply then fails still counts as a sent message (that is the honest
  denominator for the failure guardrail and for adoption — D2 persists nothing,
  so the event seam is the only record it happened), but it starts no
  conversation. So `tutor_message_sent` can exceed the persisted thread length,
  by exactly the failed and stopped replies. `tutor_depth` inherits that: it
  counts turns the learner *asked for*, which is the right denominator for "did
  one question become a dialogue" but makes its p95 read slightly **high**
  against `TUTOR_CONTEXT_TURNS` — the carried-context window only ever sees the
  persisted turns.
- **A refusal counts as a successful reply.** An over-the-boundary ask answered
  gracefully is a real, persisted turn and is deliberately not machine-tagged
  this phase (TDD D5, PRD §5.7b) — so `failure_rate` means "the tutor broke",
  never "the tutor declined". Lesson corrections (§5.7b) are likewise unflagged;
  both are eval-policed and reviewable on Logfire spans.
- **`stopped` is not a failure.** The rail's stop affordance and a plain
  disconnect are indistinguishable from the server, and both mean the learner
  ended their own turn. It is reported as its own rate beside the failure rate
  and tagged W9, not W14 — folding learner behaviour into the failure guardrail
  would make the number that says "are *we* breaking" unreadable.
- **TTFT is null, never zero, when no token arrived**, so `percentile_cont`
  skips those replies: `p95_ttft_ms` is "how long the learner waited to see
  something, when something came", and the never-answered replies are counted in
  `failure_rate` instead. Logfire cannot carry a null OTEL attribute, so it
  arrives as the JSON text `null` — hence the `nullif(…, 'null')` in the query.
  `p95_duration_ms` is over successes only, or it would report
  `TUTOR_REPLY_TIMEOUT` rather than how long a working reply takes.
- **`duration_ms` includes queue wait.** It is clocked from the moment the reply
  starts being produced — *before* the tutor concurrency permit
  (`MAX_CONCURRENT_TUTOR_REPLIES`) is acquired — so a saturated permit pool
  shows up in `p95_duration_ms` rather than hiding. Two consequences: it can
  exceed `TUTOR_REPLY_TIMEOUT`, which bounds only the model run, and
  `duration_ms - ttft_ms` is **not** "streaming time" (TTFT is clocked from
  inside the permit). This is deliberate — the guardrail measures learner-felt
  latency (ask → settled reply), not the model's own span.
- **The daily tutor cap is disabled, and clearing a thread would refund it
  (D8).** `RATE_LIMIT_TUTOR_MESSAGES_PER_DAY` defaults to **0**, so the limiter
  never counts and no §7 number is capped today. The count would be over *live*
  learner-message rows, which **New conversation** deletes — so a thread clear
  refunds quota. Recorded, not fixed: building the refund-proof append-only
  usage table is the **precondition for ever raising the cap above 0** (PRD
  §5.7, TDD D8), not work done while the cap is off. `tutor_message_sent` is
  unaffected either way — it is emitted at admission and never deleted, so
  adoption, depth and entry mix survive a thread clear.
- **A Tutor check shown on a failed reply still counts as shown.**
  `tutor_check_shown` is emitted where the card reaches the rail, mid-stream, so
  a reply that then fails leaves a shown check that persisted nothing and can
  never be answered. Uptake is immune (its denominator is tutor *users*), but
  the raw shown→answered funnel in `tutor_check_uptake.sql` reads slightly low
  because of it.
- **Tutor-check re-answers re-emit,** tagged `first_answer=false` — unlike the
  Quick check's first-wins Attempt, where a repeat submit writes nothing and
  emits nothing. A re-answer genuinely rewrites the stored payload, so the event
  records a real state change; every per-check count filters
  `first_answer = 'true'` so a learner cycling options cannot move a rate.
  `first_answer` is read from the payload before the write and the row is not
  locked, so two *concurrent* first answers on the same check can both see "not
  yet answered" and both emit `first_answer=true` — inflating the raw
  `checks_answered` count by one. Left unlocked deliberately: it needs the same
  learner double-submitting the same card in the same instant, the write is
  last-wins anyway (nothing is lost), and the uptake *rate* is immune because
  its numerator counts distinct accounts.
- **A Tutor check is outside progress.** It creates no Attempt and appears in no
  Phase 1 metric — activation, `quick_check_correctness` and the north star are
  untouched by anything in this section (PRD §5.5, TDD §3).

Shaping-specific caveats (Phase 2B):

- **`lesson_ids` is JSON text, not a JSON array column.** Logfire cannot carry a
  structured OTEL attribute, so a list is serialised on the way in and arrives as
  the *string* `["…","…"]`. Every query that unnests it therefore reads
  `(attributes ->> 'lesson_ids')::jsonb` and not `attributes -> 'lesson_ids'` —
  one spelling that works in Logfire and in the executed replay test alike.
- **Shaping yield clamps to closed windows.** `shaping_yield.sql` counts only
  Changes applied more than 7 days ago, because a Change applied an hour ago
  cannot have been engaged with yet and would dilute the primary metric downward
  exactly while adoption climbs (the same call `activation_rate.sql` makes). The
  cost is a panel that is empty for the first week after launch; removing the one
  clamp line is the deliberate way to read that week.
- **An undone Change stays in the yield denominator.** The learner did apply it,
  and an undone addition can no longer be engaged with — so it reads as
  unyielded. `undo_rate.sql` is what separates "regretted it" from "ignored it";
  folding the two together in the primary metric would hide the difference.
- **Proposal acceptance's denominator over-counts twice.** A Proposal shown on a
  reply that then *failed* still counts as shown (it is emitted mid-stream where
  the card reaches the rail — 2A's `tutor_check_shown` rule verbatim), and a
  Proposal that went *stale* counts as un-accepted. Both are deliberate; the
  coded `409` reasons that would tell them apart (§5.8) are not events this phase
  emits.
- **A declined edit is indistinguishable from an ordinary turn** in the events.
  W20's decline is not machine-tagged this phase (TDD D5, PRD §5.7b), so it reads
  as `shaping_reply_completed` with `outcome='success'` and `has_proposal=false`
  — which is also what a turn that was never about an edit looks like. Decline
  *quality* is eval-policed, not event-derived. The additive path back is a
  payload flag, exactly as it is for a 2A refusal.
- **Adoption's denominator is "has a ready path", not "activated".** There is
  nothing to shape until the outline exists (a 409 until then), so an account
  that never got one could not have adopted shaping; counting it would measure
  Phase 1's generation funnel. It reads *higher* than an activated-cohort
  denominator would, and it is not comparable with `tutor_adoption.sql`, whose
  denominator is the activated set.
- **Depth to proposal counts the ask that produced the card**, so a first-message
  proposal is 1 and never 0, and conversations that never produced a proposal are
  absent rather than infinite. Like `tutor_depth.sql` it counts at admission, so
  it runs slightly above the persisted thread length by exactly the failed and
  stopped replies.
- **The shaped/unshaped completion split is self-selected on both sides.**
  Learners who shape are plausibly the more engaged learners, and a path is only
  shapeable once it is ready and has progress on it — both bias the shaped side
  up. A shaped rate merely *equal* to the unshaped one is already a mild warning;
  the number to act on is a clear fall.
- **A path stays "shaped" after an undo.** The hoarding behaviour under test is
  the applying, and an undo does not retroactively make the learner someone who
  never curated.
- **The two rails share a permit pool (D11), so their latency panels must be
  read together.** `shaping_reply_failure_latency.sql` is
  `tutor_reply_failure_latency.sql` column for column on purpose: a rise in both
  is the shared `MAX_CONCURRENT_TUTOR_REPLIES` pool, a rise in one is that rail.
  Every 2A caveat about `ttft_ms` (null, never zero), `duration_ms` (includes
  queue wait, can exceed `TUTOR_REPLY_TIMEOUT`) and `stopped` (learner
  behaviour, not a fault) applies here unchanged.
- **The daily shaping cap is disabled**, exactly as 2A's is:
  `RATE_LIMIT_SHAPING_MESSAGES_PER_DAY` defaults to **0**, so no §7 number here
  is capped today.
- **Shaping never writes progress**, in either direction. Apply and undo touch
  `units`/`lessons` only, and the engagement boundary (D2) means a Change whose
  content the learner has met cannot be undone at all — so no Attempt and no
  completion is ever in reach of this phase's code. Activation, the north star
  and `quick_check_correctness` move only through Phase 1's own events.
