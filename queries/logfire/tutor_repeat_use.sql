-- Tutor repeat use (Phase 2 PRD §7 supporting metric).
--
-- % of tutor users who used the tutor in MORE THAN ONE lesson. Adoption says
-- they tried it; this says it earned a second lesson — the difference between a
-- novelty tap and a habit.
--
-- Denominator is tutor users (anyone with a sent message), not activated
-- learners: this is a ratio *within* the adopting set, the same shape breadth
-- and return take within the activated set.
--
-- Events used: tutor_message_sent (account_id, lesson_id).
WITH tutor_lessons AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        count(DISTINCT attributes ->> 'lesson_id') AS lessons
    FROM records
    WHERE span_name = 'tutor_message_sent'
    GROUP BY 1
)
SELECT
    count(*) FILTER (WHERE lessons > 1)::float
        / nullif(count(*), 0) AS repeat_use_rate
FROM tutor_lessons;
