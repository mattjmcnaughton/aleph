-- Return (PRD §7 supporting metric).
--
-- % of activated learners who come back on a SECOND distinct day. "Activated"
-- reuses the north-star definition IN FULL (>3 attempted-and-completed lessons on
-- a single path, each completion within 7 days of signup — CONTEXT.md "Activated
-- learner"). "Day" is a calendar day; computed here in UTC — the
-- learner-local-timezone refinement (PRD §5.7 / CONTEXT.md "Day") is a follow-up.
--
-- Events used: account_created, lesson_completed + quick_check_attempted (the
-- activation set), and any learner activity (lesson_viewed / lesson_completed /
-- quick_check_attempted) for distinct-day counting.
WITH accounts AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        min(start_timestamp) AS signed_up_at
    FROM records
    WHERE span_name = 'account_created'
    GROUP BY 1
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
        attributes ->> 'account_id' AS account_id,
        count(DISTINCT date_trunc('day', start_timestamp)) AS distinct_days
    FROM records
    WHERE span_name IN ('lesson_viewed', 'lesson_completed', 'quick_check_attempted')
    GROUP BY 1
)
SELECT
    count(DISTINCT activated.account_id) FILTER (WHERE d.distinct_days >= 2)::float
        / nullif(count(DISTINCT activated.account_id), 0) AS return_rate
FROM activated
LEFT JOIN active_days AS d ON d.account_id = activated.account_id;
