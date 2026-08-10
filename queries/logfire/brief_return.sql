-- The north star (Phase 6 PRD §5, TDD §9/§15): does a Brief bring a learner
-- back on a day nothing else would have?
--
-- Among learners who deployed at least one Beat, two numbers:
--
--   * brief_first_share — of their Active days ON OR AFTER their first Beat
--     deployment, the share whose FIRST same-day product event is opening a
--     Brief (marker='opened').
--   * return_rate_post_beat vs return_rate_pre_beat — this cohort's OWN
--     Return (CONTEXT.md/return_rate.sql: >=2 distinct Active days),
--     computed separately over the days before and the days on/after each
--     learner's first beat_deployed — the "exceeds their own pre-Beat
--     baseline" comparison PRD §5 asks for. A learner deployed on day one
--     contributes no pre-Beat day, and correctly reports NULL there rather
--     than a false zero.
--
-- **The Active-day vocabulary that decides "is this day active at all" is
-- HELD FIXED on both sides of the split** — `lesson_viewed`,
-- `lesson_completed`, `quick_check_attempted` only (return_rate.sql's own
-- set) — corrected here to close a confound an earlier version of this
-- query had (FIX 5, code-review): that version widened the Active-day
-- vocabulary with `brief_read`, but `brief_read` **can only occur
-- post-Beat** — no Brief exists before a Beat is deployed. Adding it to only
-- one side of a pre/post comparison is not a widening, it is an asymmetry:
-- the post side was being computed over a strictly larger event vocabulary
-- than the pre side could ever draw from, so any lift `return_rate_post_beat`
-- showed over `return_rate_pre_beat` was confounded by construction, not
-- evidence a Brief moved anything. (The branch's own fixture proved it: an
-- account whose ENTIRE post-Beat activity was two `brief_read` pings on one
-- calendar day reported `post = 1.0` purely from that padding — removing
-- `brief_read` from the day-defining set collapses it to `post = 0.0`,
-- identical to `pre`, with zero genuine lesson activity anywhere.)
--
-- `brief_first_share` is untouched by this fix and stays the column that
-- legitimately measures Brief-driven return: within days the FIXED
-- vocabulary already calls Active, `brief_read` still decides which of them
-- opened with a Brief — that is a question about WHICH action came first on
-- an already-active day, not a question about whether a Brief can manufacture
-- an active day on its own.
--
-- Events used: beat_deployed (the split point), lesson_viewed /
-- lesson_completed / quick_check_attempted (Active-day membership, on BOTH
-- sides), brief_read (first-action material only, within already-active
-- days).
--
-- UTC, not local day — inherits return_rate.sql's standing UTC-vs-local-day
-- caveat (the learner-local-timezone refinement is a follow-up, for every
-- query in this file, not only this one).
WITH first_beat AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        min(start_timestamp) AS deployed_at
    FROM records
    WHERE span_name = 'beat_deployed'
    GROUP BY 1
),
-- The FIXED activity vocabulary that decides whether a day counts as
-- Active at all -- identical on both sides of the pre/post split (FIX 5).
lesson_events_by_day AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        date_trunc('day', start_timestamp) AS day
    FROM records
    WHERE span_name IN ('lesson_viewed', 'lesson_completed', 'quick_check_attempted')
    GROUP BY 1, 2
),
-- The FIRST same-day product event, widened to include brief_read
-- (marker='opened') -- but only within days `lesson_events_by_day` already
-- calls Active, so a Brief open can decide WHICH action was first on an
-- active day without being able to manufacture an active day by itself.
first_action_per_day AS (
    SELECT DISTINCT ON (e.account_id, e.day)
        e.account_id,
        e.day,
        e.span_name,
        e.marker,
        (e.day >= date_trunc('day', fb.deployed_at)) AS is_post_beat
    FROM (
        SELECT
            attributes ->> 'account_id' AS account_id,
            date_trunc('day', start_timestamp) AS day,
            start_timestamp,
            span_name,
            attributes ->> 'marker' AS marker
        FROM records
        WHERE span_name IN (
            'lesson_viewed', 'lesson_completed', 'quick_check_attempted', 'brief_read'
        )
    ) AS e
    JOIN lesson_events_by_day AS led
        ON led.account_id = e.account_id AND led.day = e.day
    JOIN first_beat AS fb ON fb.account_id = e.account_id
    ORDER BY e.account_id, e.day, e.start_timestamp
),
per_account_return AS (
    SELECT
        account_id,
        is_post_beat,
        (count(DISTINCT day) >= 2) AS returned
    FROM first_action_per_day
    GROUP BY account_id, is_post_beat
)
SELECT
    count(*) FILTER (
        WHERE is_post_beat AND span_name = 'brief_read' AND marker = 'opened'
    )::float / nullif(count(*) FILTER (WHERE is_post_beat), 0)
        AS brief_first_share,
    (SELECT avg(returned::int) FILTER (WHERE is_post_beat)
     FROM per_account_return) AS return_rate_post_beat,
    (SELECT avg(returned::int) FILTER (WHERE NOT is_post_beat)
     FROM per_account_return) AS return_rate_pre_beat
FROM first_action_per_day;
