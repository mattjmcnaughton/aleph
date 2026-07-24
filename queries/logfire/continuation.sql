-- Lesson-to-lesson continuation (PRD §7 supporting metric).
--
-- Of the completed lessons that HAVE a next lesson, the % followed by the learner
-- STARTING (viewing) that next lesson on the same path (position_in_path + 1).
-- Measures whether the loop keeps pulling learners forward.
--
-- Final-lesson deflation fix: a path's LAST lesson has no next lesson, so it can
-- never "continue" and would drag the rate down as a denominator artefact. Where
-- a path has completed (``path_completed`` carries its ``lesson_count``), the
-- final position is excluded. Paths not yet complete keep all positions (their
-- length is unknown from events alone) — a documented partial exclusion, see
-- docs/metrics.md.
--
-- Events used: lesson_completed (position N), lesson_viewed (position N+1),
-- path_completed (lesson_count, to drop the final position of completed paths).
WITH path_len AS (
    SELECT
        attributes ->> 'path_id' AS path_id,
        max((attributes ->> 'lesson_count')::int) AS lesson_count
    FROM records
    WHERE span_name = 'path_completed'
    GROUP BY 1
),
completed AS (
    SELECT
        c.attributes ->> 'path_id' AS path_id,
        (c.attributes ->> 'position_in_path')::int AS position_in_path
    FROM records AS c
    LEFT JOIN path_len AS pl ON pl.path_id = c.attributes ->> 'path_id'
    WHERE c.span_name = 'lesson_completed'
      -- Keep every position unless we KNOW it is the path's final one.
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
)
SELECT
    count(*) FILTER (WHERE started.path_id IS NOT NULL)::float
        / nullif(count(*), 0) AS continuation_rate
FROM completed
LEFT JOIN started
    ON started.path_id = completed.path_id
   AND started.position_in_path = completed.position_in_path + 1;
