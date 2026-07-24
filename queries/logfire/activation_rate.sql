-- North-star Activation rate (PRD §7).
--
-- % of accounts created in the cohort that become *activated* within 7 days of
-- signup, where activated = MORE THAN 3 lessons (>=4) completed on a SINGLE path,
-- each completed lesson also carrying a recorded Quick-check Attempt
-- (CONTEXT.md "Activated learner"). Per account, per single path — 2+2 across two
-- paths is not activation.
--
-- Events used: account_created (cohort + signup timestamp), lesson_completed
-- (the completions, keyed by path so the >3 count is per single path),
-- quick_check_attempted (the Attempt gate, joined on lesson_id).
--
-- Cohort clamp (right-censoring): only accounts whose full 7-day window has
-- already closed are in the cohort. A signup from <7 days ago has not had its
-- chance to activate yet, so counting it in the denominator would bias the
-- north star low (a purely mechanical dilution). The clamp keeps the rate an
-- honest "of the accounts that COULD have activated, how many did".
WITH accounts AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        min(start_timestamp) AS signed_up_at
    FROM records
    WHERE span_name = 'account_created'
    GROUP BY 1
    HAVING min(start_timestamp) < now() - INTERVAL '7 days'
),
-- Lessons that were BOTH completed and attempted (the activation gate), joined on
-- lesson_id and keeping the path so the count below is per single path.
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
)
SELECT
    count(DISTINCT activated.account_id)::float
        / nullif(count(DISTINCT accounts.account_id), 0) AS activation_rate
FROM accounts
LEFT JOIN activated ON activated.account_id = accounts.account_id;
