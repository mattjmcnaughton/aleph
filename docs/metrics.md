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
  by exactly the failed and stopped replies.
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
