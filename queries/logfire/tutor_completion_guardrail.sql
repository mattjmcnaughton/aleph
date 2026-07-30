-- "Not a crutch" (Phase 2 PRD §7 guardrail / counter-metric).
--
-- Lesson COMPLETION rate for lessons where the learner used the tutor, against
-- lessons where they did not. The failure mode being watched: a learner who
-- chats and then abandons the lesson. If the with-tutor rate falls below the
-- without-tutor rate, the tutor is absorbing effort the lesson needed, and the
-- primary continuation gap should not be celebrated over it.
--
-- Denominator is lessons *started* (viewed), which is the population that could
-- have been completed; numerator is those that were. Both sides are keyed on
-- (path, lesson) so a lesson counts once however many times it was polled.
--
-- Events used: lesson_viewed (started), lesson_completed (completed),
-- tutor_message_sent (which lessons had tutor use).
WITH viewed AS (
    SELECT DISTINCT
        attributes ->> 'path_id' AS path_id,
        attributes ->> 'lesson_id' AS lesson_id
    FROM records
    WHERE span_name = 'lesson_viewed'
),
completed AS (
    SELECT DISTINCT
        attributes ->> 'path_id' AS path_id,
        attributes ->> 'lesson_id' AS lesson_id
    FROM records
    WHERE span_name = 'lesson_completed'
),
tutor_lessons AS (
    SELECT DISTINCT
        attributes ->> 'path_id' AS path_id,
        attributes ->> 'lesson_id' AS lesson_id
    FROM records
    WHERE span_name = 'tutor_message_sent'
)
SELECT
    t.lesson_id IS NOT NULL AS with_tutor,
    count(*) AS lessons_started,
    count(*) FILTER (WHERE c.lesson_id IS NOT NULL)::float
        / nullif(count(*), 0) AS completion_rate
FROM viewed AS v
LEFT JOIN completed AS c
    ON c.path_id = v.path_id AND c.lesson_id = v.lesson_id
LEFT JOIN tutor_lessons AS t
    ON t.path_id = v.path_id AND t.lesson_id = v.lesson_id
GROUP BY 1;
