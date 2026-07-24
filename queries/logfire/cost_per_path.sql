-- Generation cost per path (PRD §7 guardrail).
--
-- Average total generation tokens per path — the outline plus every lesson.
-- A token-based proxy for model cost: continuity makes later lessons the
-- expensive ones (TDD §5.2/§10), so cost must be watched, not assumed cheap.
-- Dollar cost proper lives on the pydantic-ai model-call spans (token/cost per
-- call), grouped by the same path_id; this query is the event-derived proxy that
-- makes "cost per path" computable from the product events alone.
--
-- Events used: outline_generated + lesson_generated (ALL outcomes), summing
-- total_tokens per path. A refusal (W7) and, where the provider bills partial
-- output, a failure (W8) are real spend — filtering to success-only would
-- understate cost, so every resolution counts. Failed resolutions that carried
-- no usage contribute 0 tokens (the emitter defaults them), so they neither
-- inflate nor are silently dropped.
WITH per_path AS (
    SELECT
        attributes ->> 'path_id' AS path_id,
        sum((attributes ->> 'total_tokens')::bigint) AS total_tokens
    FROM records
    WHERE span_name IN ('outline_generated', 'lesson_generated')
    GROUP BY 1
)
SELECT avg(total_tokens) AS avg_tokens_per_path FROM per_path;
