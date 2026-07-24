-- Breadth (PRD §7 supporting metric).
--
-- % of activated learners running 2+ paths. "Activated" reuses the north-star
-- definition IN FULL (CONTEXT.md "Activated learner"): >3 attempted-and-completed
-- lessons on a single path, each completion landing within 7 days of signup. Path
-- count comes from path_created. Breadth across paths is a supporting signal,
-- deliberately NOT the (single-path) north star.
--
-- Events used: account_created (signup timestamp for the 7-day window),
-- lesson_completed + quick_check_attempted (the activation set), path_created
-- (per-account path count).
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
-- on a SINGLE path, each completion within 7 days of signup.
activated AS (
    SELECT acc.account_id
    FROM accounts AS acc
    JOIN activating_lessons AS l
        ON l.account_id = acc.account_id
       AND l.completed_at <= acc.signed_up_at + INTERVAL '7 days'
    GROUP BY acc.account_id, l.path_id
    HAVING count(DISTINCT l.lesson_id) > 3
),
path_counts AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        count(DISTINCT attributes ->> 'path_id') AS paths
    FROM records
    WHERE span_name = 'path_created'
    GROUP BY 1
)
SELECT
    count(DISTINCT activated.account_id) FILTER (WHERE p.paths >= 2)::float
        / nullif(count(DISTINCT activated.account_id), 0) AS breadth_rate
FROM activated
LEFT JOIN path_counts AS p ON p.account_id = activated.account_id;
