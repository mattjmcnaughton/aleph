-- Generation failure rate + latency (PRD §7 guardrail).
--
-- Per generation kind: the share of generations that failed and the p95 wait.
-- These are the ops guardrails watched from day one (TDD §9/§10) — a rising
-- failure rate or p95 is a "we are breaking things" counter-signal.
--
-- Events used: outline_generated + lesson_generated (success flag + duration_ms).
SELECT
    span_name,
    count(*) FILTER (WHERE attributes ->> 'success' = 'false')::float
        / nullif(count(*), 0) AS failure_rate,
    percentile_cont(0.95) WITHIN GROUP (
        ORDER BY (attributes ->> 'duration_ms')::int
    ) AS p95_duration_ms
FROM records
WHERE span_name IN ('outline_generated', 'lesson_generated')
GROUP BY span_name;
