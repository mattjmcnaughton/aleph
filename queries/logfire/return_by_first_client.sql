-- Return through the app, part 2 (iOS PRD §7 primary metric, TDD §12).
--
-- The Return rate (a second distinct Day), split by the client the learner's
-- FIRST path was started on: 'ios' (the app) versus 'web'. One row per client.
-- Return is return_rate.sql's metric exactly, so the cohort is its activated
-- cohort, IN FULL (>3 attempted-and-completed lessons on a single path, each
-- completion within 7 days of signup — CONTEXT.md "Activated learner"), and a
-- returning learner is one with learner activity on 2+ distinct Days (UTC).
-- If the app's row does not beat the web's, the app is a second rendering of
-- the same loop (PRD §7).
--
-- The first path's client is its path_created ``client`` (server-derived;
-- cookie -> web, the mapped iOS bearer client -> ios, else unknown). Accounts
-- whose first path predates the dimension carry no client and are left out
-- rather than guessed.
--
-- Events used: account_created, lesson_completed + quick_check_attempted (the
-- activation set), lesson_viewed (distinct days), path_created (first client).
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
),
paths_in_order AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        attributes ->> 'client' AS client,
        row_number() OVER (
            PARTITION BY attributes ->> 'account_id' ORDER BY start_timestamp
        ) AS nth
    FROM records
    WHERE span_name = 'path_created'
),
first_client AS (
    SELECT account_id, client
    FROM paths_in_order
    WHERE nth = 1 AND client IS NOT NULL
)
SELECT
    f.client AS first_path_client,
    count(DISTINCT activated.account_id) AS activated_learners,
    count(DISTINCT activated.account_id) FILTER (WHERE d.distinct_days >= 2)::float
        / nullif(count(DISTINCT activated.account_id), 0) AS return_rate
FROM activated
JOIN first_client AS f ON f.account_id = activated.account_id
LEFT JOIN active_days AS d ON d.account_id = activated.account_id
GROUP BY f.client
ORDER BY f.client;
