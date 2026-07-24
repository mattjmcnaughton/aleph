-- Path start rate (PRD §7 supporting metric).
--
-- % of successfully generated paths whose learner starts lesson 1
-- (position_in_path = 1). A proxy for first-try outline quality: there is no
-- regenerate, so a bad outline shows up as a path that never starts.
--
-- Events used: outline_generated with outcome 'ready' (denominator = generated
-- paths), lesson_viewed at position_in_path = 1 (numerator = started paths).
WITH generated_paths AS (
    SELECT DISTINCT attributes ->> 'path_id' AS path_id
    FROM records
    WHERE span_name = 'outline_generated'
      AND attributes ->> 'outcome' = 'ready'
),
started AS (
    SELECT DISTINCT attributes ->> 'path_id' AS path_id
    FROM records
    WHERE span_name = 'lesson_viewed'
      AND (attributes ->> 'position_in_path')::int = 1
)
SELECT
    count(started.path_id)::float
        / nullif(count(generated_paths.path_id), 0) AS path_start_rate
FROM generated_paths
LEFT JOIN started ON started.path_id = generated_paths.path_id;
