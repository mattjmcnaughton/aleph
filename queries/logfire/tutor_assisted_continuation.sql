-- Tutor-assisted continuation — Phase 2's PRIMARY metric (PRD §7).
--
-- Among activated learners, the lesson-to-lesson continuation rate for lessons
-- where the learner sent at least one tutor message, compared against lessons
-- where they did not. Phase 2 has no north star of its own: Phase 1's activation
-- rate stays it, and this is the compounding claim, stated so it can fail. If
-- tutor use does not correlate with continuing, the tutor is decoration.
--
-- It is a COMPARISON, not a threshold: two rows out, `with_tutor` true and
-- false, and the shape we want is a positive gap — read alongside
-- tutor_adoption.sql, because a large gap on 2% adoption is noise.
--
-- Correlation, not causation. Learners who ask questions are plausibly the
-- learners who were going to continue anyway; nothing here randomises. The
-- honest reading is "the tutor is not associated with abandonment, and is
-- associated with continuing", and the counter-metric that keeps it honest is
-- tutor_completion_guardrail.sql.
--
-- Continuation is Phase 1's definition reused verbatim (continuation.sql): of
-- the completed lessons that HAVE a next lesson, the share followed by the
-- learner starting position N+1 on the same path, with a completed path's final
-- position excluded as a denominator artefact.
--
-- The activated cohort reuses the learner-level definition (>3
-- attempted-and-completed lessons on a single path, each completion within 7
-- days of signup) but, like tutor_adoption.sql, deliberately drops
-- activation_rate.sql's cohort maturity clamp (`min(start_timestamp) < now() -
-- INTERVAL '7 days'`): this is a rate *within* the activated set, so young
-- accounts cannot dilute it, and clamping would delay the signal by a week at
-- exactly the moment a rollout is being read. The 7-day activation window
-- itself is kept in full.
--
-- Events used: account_created + lesson_completed + quick_check_attempted (the
-- activated cohort), path_completed (lesson_count, to drop final positions),
-- lesson_viewed (the continuation), tutor_message_sent (the split).
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
-- One row per activated account: `activated` groups per (account, path), and
-- joining that directly would count a two-path activator's lessons twice.
activated_accounts AS (
    SELECT DISTINCT account_id FROM activated
),
path_len AS (
    SELECT
        attributes ->> 'path_id' AS path_id,
        max((attributes ->> 'lesson_count')::int) AS lesson_count
    FROM records
    WHERE span_name = 'path_completed'
    GROUP BY 1
),
completed AS (
    SELECT
        c.attributes ->> 'account_id' AS account_id,
        c.attributes ->> 'path_id' AS path_id,
        c.attributes ->> 'lesson_id' AS lesson_id,
        (c.attributes ->> 'position_in_path')::int AS position_in_path
    FROM records AS c
    LEFT JOIN path_len AS pl ON pl.path_id = c.attributes ->> 'path_id'
    WHERE c.span_name = 'lesson_completed'
      AND (
          pl.lesson_count IS NULL
          OR (c.attributes ->> 'position_in_path')::int < pl.lesson_count
      )
),
started AS (
    SELECT DISTINCT
        attributes ->> 'path_id' AS path_id,
        (attributes ->> 'position_in_path')::int AS position_in_path
    FROM records
    WHERE span_name = 'lesson_viewed'
),
tutor_lessons AS (
    SELECT DISTINCT
        attributes ->> 'path_id' AS path_id,
        attributes ->> 'lesson_id' AS lesson_id
    FROM records
    WHERE span_name = 'tutor_message_sent'
)
SELECT
    tl.lesson_id IS NOT NULL AS with_tutor,
    count(*) AS lessons,
    count(*) FILTER (WHERE s.path_id IS NOT NULL)::float
        / nullif(count(*), 0) AS continuation_rate
FROM completed AS comp
JOIN activated_accounts AS act ON act.account_id = comp.account_id
LEFT JOIN tutor_lessons AS tl
    ON tl.path_id = comp.path_id AND tl.lesson_id = comp.lesson_id
LEFT JOIN started AS s
    ON s.path_id = comp.path_id
   AND s.position_in_path = comp.position_in_path + 1
GROUP BY 1;
