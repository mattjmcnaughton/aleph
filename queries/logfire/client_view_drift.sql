-- No metric drift between clients (iOS PRD §7 guardrail, TDD §12).
--
-- lesson_viewed volume per completed lesson, per client. The app's ratio should
-- stay within the web's band; a large excess on 'ios' means a poll loop is not
-- cancelling (each lesson poll that re-reads the lesson is a view). Events with
-- no client (none is expected for these two request-path events) are left out.
--
-- Events used: lesson_viewed, lesson_completed (client).
SELECT
    attributes ->> 'client' AS client,
    count(*) FILTER (WHERE span_name = 'lesson_viewed') AS views,
    count(*) FILTER (WHERE span_name = 'lesson_completed') AS completions,
    count(*) FILTER (WHERE span_name = 'lesson_viewed')::float
        / nullif(count(*) FILTER (WHERE span_name = 'lesson_completed'), 0)
        AS views_per_completion
FROM records
WHERE span_name IN ('lesson_viewed', 'lesson_completed')
  AND attributes ->> 'client' IS NOT NULL
GROUP BY 1
ORDER BY 1;
