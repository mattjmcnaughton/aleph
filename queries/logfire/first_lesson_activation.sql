-- First-lesson activation (PRD §7 supporting metric).
--
-- % of new accounts that complete >=1 lesson (with a recorded Attempt) during
-- their FIRST session. Session = a run of activity with no gap longer than 30
-- minutes (PRD §5.7 / CONTEXT.md). Diagnoses whether the very first visit lands.
--
-- Events used: account_created, path_created, lesson_viewed, lesson_completed,
-- quick_check_attempted — sessionized per account by the 30-minute gap rule.
-- ``path_created`` is included so a first session is anchored at the learner's
-- true first action (naming the topic), not the first lesson view.
WITH events AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        attributes ->> 'lesson_id' AS lesson_id,
        start_timestamp AS at,
        span_name
    FROM records
    WHERE span_name IN (
        'account_created', 'path_created', 'lesson_viewed',
        'lesson_completed', 'quick_check_attempted'
    )
),
-- A new session starts at the first event and after any gap over 30 minutes.
marked AS (
    SELECT
        account_id,
        lesson_id,
        at,
        span_name,
        CASE
            WHEN lag(at) OVER w IS NULL
              OR at - lag(at) OVER w > INTERVAL '30 minutes'
            THEN 1 ELSE 0
        END AS is_new_session
    FROM events
    WINDOW w AS (PARTITION BY account_id ORDER BY at)
),
sessions AS (
    SELECT
        account_id,
        lesson_id,
        at,
        span_name,
        sum(is_new_session) OVER (PARTITION BY account_id ORDER BY at) AS session_no
    FROM marked
),
first_session AS (
    SELECT account_id, lesson_id, at, span_name
    FROM sessions
    WHERE session_no = 1
),
accounts AS (
    SELECT DISTINCT attributes ->> 'account_id' AS account_id
    FROM records
    WHERE span_name = 'account_created'
),
-- Accounts whose first session contains a completed lesson whose SAME lesson also
-- carries an Attempt (the activation gate is per-lesson, keyed on lesson_id —
-- consistent with activation_rate, not "any attempt anywhere in the session").
activated_first AS (
    SELECT fs.account_id
    FROM first_session AS fs
    WHERE fs.span_name = 'lesson_completed'
      AND EXISTS (
          SELECT 1 FROM first_session AS q
          WHERE q.account_id = fs.account_id
            AND q.span_name = 'quick_check_attempted'
            AND q.lesson_id = fs.lesson_id
      )
    GROUP BY fs.account_id
)
SELECT
    count(DISTINCT activated_first.account_id)::float
        / nullif(count(DISTINCT accounts.account_id), 0) AS first_lesson_activation
FROM accounts
LEFT JOIN activated_first ON activated_first.account_id = accounts.account_id;
