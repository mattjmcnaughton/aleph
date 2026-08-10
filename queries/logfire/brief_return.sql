-- The north star (Phase 6 PRD §5, TDD §9/§15): does a Brief bring a learner
-- back on a day nothing else would have?
--
-- Among learners who deployed at least one Beat, two numbers:
--
--   * brief_first_share — of their Active days ON OR AFTER their first Beat
--     deployment, the share whose FIRST same-day product event is opening a
--     Brief (marker='opened'). "Active day" reuses return_rate.sql's
--     activity set (lesson_viewed / lesson_completed / quick_check_attempted),
--     widened by brief_read opened — CONTEXT.md's Phase 6 widening of
--     Active day, applied here even though the streak itself does not read
--     brief_read yet (§15: "nothing reads that third signal yet" — this
--     query is allowed to, since it is not the streak).
--   * return_rate_post_beat vs return_rate_pre_beat — this cohort's OWN
--     Return (CONTEXT.md/return_rate.sql: >=2 distinct Active days),
--     computed separately over the days before and the days on/after each
--     learner's first beat_deployed — the "exceeds their own pre-Beat
--     baseline" comparison PRD §5 asks for. A learner deployed on day one
--     contributes no pre-Beat day, and correctly reports NULL there rather
--     than a false zero.
--
-- Events used: beat_deployed (the split point), lesson_viewed /
-- lesson_completed / quick_check_attempted / brief_read (the day + first-
-- action material).
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
events_by_day AS (
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
),
-- One row per (account, day): that day's FIRST product event, and whether
-- the day fell on/after this account's first Beat deployment.
first_action_per_day AS (
    SELECT DISTINCT ON (e.account_id, e.day)
        e.account_id,
        e.day,
        e.span_name,
        e.marker,
        (e.day >= date_trunc('day', fb.deployed_at)) AS is_post_beat
    FROM events_by_day AS e
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
