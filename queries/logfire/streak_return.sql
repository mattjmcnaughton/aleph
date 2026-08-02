-- Streak return (Phase 5 TDD §9, D9).
--
-- Does the streak move Return? Splits the existing Return metric
-- (return_rate.sql: activated learners back on a 2nd distinct day) into two
-- cohorts by whether the account's first activity predates or follows the
-- streaks flag flip — the one comparison this slice stands or falls on (PRD §5,
-- TDD §9's closing line: "if Return does not move, this slice is decoration").
--
-- There is no event for the flip (D9 dropped the event the combined doc wanted,
-- on the grounds that streak length is already computable from
-- lesson_completed retroactively — which is also the only reason this query can
-- build a "before" cohort at all). So the flip date is a dated constant below,
-- not a column: update it once, the day FeatureFlag.STREAKS's entry in
-- FLAG_DEFAULTS (src/aleph/services/feature_flags.py) flips to True (the launch
-- move, docs/deploy.md#launching-a-flagged-phase-al-270--al-370).
--
-- FLAG_FLIP_AT: set to a timestamp far in the future until launch, so every
-- account reads as "before" and the "after" cohort is honestly empty rather
-- than misleadingly populated with pre-launch accounts.
--
-- "Activated" reuses the north-star definition IN FULL (>3 attempted-and-
-- completed lessons on a single path, each completion within 7 days of signup —
-- CONTEXT.md "Activated learner"), exactly as return_rate.sql does — the same
-- cohort, just split in two. "Day" is a calendar day; computed here in UTC, the
-- same caveat return_rate.sql already carries (the learner-local-timezone
-- refinement, PRD §5.7 / CONTEXT.md "Day", is a follow-up): the metric counts
-- UTC days while the *feature* counts learner-local days, so the two can
-- disagree by one at the margins (TDD §9).
--
-- Events used: account_created, lesson_completed + quick_check_attempted (the
-- activation set), and any learner activity (lesson_viewed / lesson_completed /
-- quick_check_attempted) both for distinct-day counting and for "first
-- activity" (the cohort split).
WITH flag_flip AS (
    SELECT '2026-12-31T00:00:00Z'::timestamptz AS flipped_at
),
accounts AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        min(start_timestamp) AS signed_up_at
    FROM records
    WHERE span_name = 'account_created'
    GROUP BY 1
),
activity AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        start_timestamp
    FROM records
    WHERE span_name IN ('lesson_viewed', 'lesson_completed', 'quick_check_attempted')
),
-- The cohort split: an account belongs to "after" iff its earliest recorded
-- activity of any kind lands on or after the flip. An account with no activity
-- at all cannot be activated (below), so it never needs a cohort.
cohorts AS (
    SELECT
        first_activity.account_id,
        CASE
            WHEN first_activity.first_activity_at < flag_flip.flipped_at THEN 'before'
            ELSE 'after'
        END AS cohort
    FROM (
        SELECT account_id, min(start_timestamp) AS first_activity_at
        FROM activity
        GROUP BY 1
    ) AS first_activity
    CROSS JOIN flag_flip
),
activating_lessons AS (
    SELECT DISTINCT
        c.attributes ->> 'account_id' AS account_id,
        c.attributes ->> 'path_id' AS path_id,
        c.attributes ->> 'lesson_id' AS lesson_id,
        c.start_timestamp AS completed_at
    FROM records AS c
    JOIN records AS a
        ON a.span_name = 'quick_check_attempted'
       AND a.attributes ->> 'lesson_id' = c.attributes ->> 'lesson_id'
    WHERE c.span_name = 'lesson_completed'
),
-- Activated = the full CONTEXT.md definition: >3 completed-and-attempted lessons
-- on a SINGLE path, each completion within 7 days of signup (joins ``accounts``).
activated AS (
    SELECT acc.account_id
    FROM accounts AS acc
    JOIN activating_lessons AS l
        ON l.account_id = acc.account_id
       AND l.completed_at <= acc.signed_up_at + INTERVAL '7 days'
    GROUP BY acc.account_id, l.path_id
    HAVING count(DISTINCT l.lesson_id) > 3
),
active_days AS (
    SELECT
        account_id,
        count(DISTINCT date_trunc('day', start_timestamp)) AS distinct_days
    FROM activity
    GROUP BY 1
)
SELECT
    cohorts.cohort,
    count(DISTINCT activated.account_id) AS activated_accounts,
    count(DISTINCT activated.account_id) FILTER (WHERE d.distinct_days >= 2)::float
        / nullif(count(DISTINCT activated.account_id), 0) AS return_rate
FROM activated
JOIN cohorts ON cohorts.account_id = activated.account_id
LEFT JOIN active_days AS d ON d.account_id = activated.account_id
GROUP BY cohorts.cohort;
