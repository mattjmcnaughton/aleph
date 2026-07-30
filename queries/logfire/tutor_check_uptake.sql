-- Tutor check uptake (Phase 2 PRD §7 supporting metric).
--
-- % of tutor users who took AT LEAST ONE Tutor check. A Tutor check is
-- non-scoring and outside progress (PRD §5.5), so this is the only signal that
-- says whether the "quiz me on this" affordance is a feature or an ornament.
--
-- The denominator is tutor **users** (distinct accounts), never checks shown —
-- which is what makes the metric immune to the two places the counts are not
-- one-to-one:
--   * a check shown mid-stream on a reply that then failed was really shown but
--     persisted nothing, so it can never be answered (D2);
--   * a re-answer re-emits tutor_check_answered (AL-240 — the write is
--     last-wins and really does rewrite the payload), so per-check counts would
--     drift with re-answers.
-- The two raw counts alongside the rate use `first_answer = 'true'` for exactly
-- that second reason: shown -> first-answered is the honest funnel.
--
-- Events used: tutor_message_sent (the tutor-user denominator),
-- tutor_check_answered (first_answer), tutor_check_shown.
WITH tutor_users AS (
    SELECT DISTINCT attributes ->> 'account_id' AS account_id
    FROM records
    WHERE span_name = 'tutor_message_sent'
),
-- One row per FIRST answer. The re-answer filter is spelled once, here, so the
-- rate's numerator (the distinct accounts) and the funnel's answered leg (the
-- row count) are the same population by construction.
first_answers AS (
    SELECT attributes ->> 'account_id' AS account_id
    FROM records
    WHERE span_name = 'tutor_check_answered'
      AND attributes ->> 'first_answer' = 'true'
),
-- The rate and the two raw counts, each an ungrouped aggregate — so every CTE
-- below is exactly one row and the cross join is one row out whatever the slice
-- holds; an empty one reads as a null rate over no checks.
uptake AS (
    SELECT
        count(*) FILTER (WHERE a.account_id IS NOT NULL)::float
            / nullif(count(*), 0) AS check_uptake_rate
    FROM tutor_users AS u
    LEFT JOIN (SELECT DISTINCT account_id FROM first_answers) AS a
        ON a.account_id = u.account_id
),
shown AS (
    SELECT count(*) AS checks_shown
    FROM records
    WHERE span_name = 'tutor_check_shown'
),
answered AS (
    SELECT count(*) AS checks_answered FROM first_answers
)
SELECT * FROM uptake CROSS JOIN shown CROSS JOIN answered;
