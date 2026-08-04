-- Review queue completion (Phase 3 PRD §5 supporting metric).
--
-- Sessions finished ÷ sessions started, derived entirely from `review_graded`
-- — no session event exists (TDD §9's "no session events" argument): a
-- session STARTS with the first grade an account logs on a given day, and it
-- FINISHES the moment any grade that day carries `queue_remaining = 0` (the
-- day's selected set fully satisfied). Carrying `queue_size`/`queue_remaining`
-- on every grade buys both ends of this metric for the cost of two integers.
--
-- "Day" is bucketed in UTC (`date_trunc('day', …)`) here, the same caveat
-- every other query in this file carries (docs/metrics.md: "Day" is UTC) —
-- the feature's own day boundary is learner-local (`tz_offset_minutes`, TDD
-- D4), so a grade near a learner's local midnight can bucket into the
-- "wrong" UTC day and, rarely, split one real session into two rows here.
--
-- Consistently ABANDONED queues (many started, few finished) says the cap
-- (10, TDD §5.1) is too many; consistently EXHAUSTED queues (most finished)
-- alongside a still-growing backlog says it is too few (PRD §5) — read this
-- beside the due-count trend, not alone.
--
-- Events used: review_graded.
WITH sessions AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        date_trunc('day', start_timestamp) AS session_day,
        bool_or((attributes ->> 'queue_remaining')::int = 0) AS finished
    FROM records
    WHERE span_name = 'review_graded'
    GROUP BY 1, 2
)
SELECT
    count(*) AS sessions_started,
    count(*) FILTER (WHERE finished) AS sessions_finished,
    count(*) FILTER (WHERE finished)::float
        / nullif(count(*), 0) AS queue_completion_rate
FROM sessions;
