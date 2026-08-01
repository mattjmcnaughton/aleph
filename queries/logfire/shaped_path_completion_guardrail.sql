-- "Not hoarding" — Phase 2B PRD §7 guardrail / counter-metric (W21).
--
-- Lesson completion rate on paths a learner has SHAPED, against paths they have
-- not. The failure mode being watched is the one the primary metric cannot see:
-- paths growing while completion stalls. "I'll add it" replacing "I'll do it" —
-- the learner curates a beautiful path and studies none of it, and every
-- shaping number goes up while learning goes down.
--
-- If the shaped rate falls below the unshaped rate, shaping yield should not be
-- celebrated over it, and the phase's compounding claim is in trouble whatever
-- the yield says.
--
-- Denominator is lessons STARTED (viewed), the population that could have been
-- completed; numerator is those completed. Both sides are keyed on
-- (path, lesson) so a lesson counts once however many times it was polled —
-- tutor_completion_guardrail.sql's shape, deliberately, so the two guardrails
-- are read the same way.
--
-- A path is "shaped" if ANY Change was ever applied to it, including one later
-- undone: the hoarding behaviour is the applying, and an undo does not
-- retroactively make the learner someone who never curated. It is also a
-- whole-path split rather than a per-lesson one — the added lessons are not
-- compared against their neighbours — because the claim under test is about the
-- learner's relationship to the path, not about whether added lessons are
-- better than generated ones.
--
-- CAVEAT: this is a correlation and self-selected on both sides. Learners who
-- shape are plausibly the more engaged learners to begin with, which biases the
-- shaped side UP; a path is only shapeable once it is ready and has progress on
-- it, which biases it up again. So a shaped rate merely equal to the unshaped
-- one is already a mild warning, and the number to act on is a clear fall.
--
-- Events used: lesson_viewed (started), lesson_completed (completed),
-- change_applied (which paths were shaped).
WITH shaped_paths AS (
    SELECT DISTINCT attributes ->> 'path_id' AS path_id
    FROM records
    WHERE span_name = 'change_applied'
),
viewed AS (
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
)
SELECT
    s.path_id IS NOT NULL AS shaped,
    count(*) AS lessons_started,
    count(*) FILTER (WHERE c.lesson_id IS NOT NULL)::float
        / nullif(count(*), 0) AS completion_rate
FROM viewed AS v
LEFT JOIN completed AS c
    ON c.path_id = v.path_id AND c.lesson_id = v.lesson_id
LEFT JOIN shaped_paths AS s ON s.path_id = v.path_id
GROUP BY 1;
