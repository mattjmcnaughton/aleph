-- Tutor adoption (Phase 2 PRD §7 supporting metric).
--
-- % of activated learners who sent AT LEAST ONE tutor message. Read alongside
-- the primary tutor_assisted_continuation gap: a large continuation gap on 2%
-- adoption is noise, and this is the number that says which it is.
--
-- "Activated" reuses the learner-level definition (CONTEXT.md "Activated
-- learner"): >3 attempted-and-completed lessons on a single path, each
-- completion within 7 days of signup. Deliberately the denominator rather than
-- "all accounts": the tutor lives inside a lesson, so an account that never
-- reached one could not have adopted it, and counting them would measure
-- Phase 1's funnel, not Phase 2's.
--
-- ONE DELIBERATE DIVERGENCE from activation_rate.sql: the `accounts` CTE below
-- drops that query's cohort maturity clamp (`HAVING min(start_timestamp) <
-- now() - INTERVAL '7 days'`). The clamp exists to stop young accounts diluting
-- a rate measured *against the full cohort*; adoption is a ratio *within* the
-- already-activated set, so it cannot be diluted that way — and clamping would
-- blind the panel to the first week of any rollout, which is exactly when
-- adoption is the number being watched. Same call breadth.sql / return_rate.sql
-- make for the same reason. The 7-day *activation window* (the per-completion
-- clause below) is kept in full.
--
-- Events used: account_created (signup timestamp), lesson_completed +
-- quick_check_attempted (the activated set), tutor_message_sent (adoption).
WITH accounts AS (
    -- No maturity clamp here — see the divergence note above.
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
tutor_users AS (
    SELECT DISTINCT attributes ->> 'account_id' AS account_id
    FROM records
    WHERE span_name = 'tutor_message_sent'
)
SELECT
    count(DISTINCT activated.account_id)
        FILTER (WHERE t.account_id IS NOT NULL)::float
        / nullif(count(DISTINCT activated.account_id), 0) AS adoption_rate
FROM activated
LEFT JOIN tutor_users AS t ON t.account_id = activated.account_id;
