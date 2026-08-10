-- Cost per read Brief (Phase 6 PRD §7 guardrail; D14a).
--
-- Total token spend across every research run ÷ distinct Briefs actually
-- opened — the number that decides whether this is viable at all. D14a's
-- $0.50 ceiling is arithmetic over character/token budgets; this is the
-- MEASUREMENT, and if they disagree, this wins (TDD §9). §4.8 makes the
-- ceiling structurally bounded; this confirms it in production.
--
-- Every research run's tokens count in the numerator, not published-only:
-- a Skipped or failed run still spends retrieval and model cost, and
-- excluding it would make the ceiling look healthier than the learner's
-- actual bill. The denominator is distinct Briefs OPENED, not published —
-- the question is viability per Brief a learner actually got value from,
-- not per Brief merely produced.
--
-- Events used: brief_research_completed (total_tokens, every outcome),
-- brief_read (marker='opened').
WITH spend AS (
    SELECT sum((attributes ->> 'total_tokens')::bigint) AS total_tokens
    FROM records
    WHERE span_name = 'brief_research_completed'
),
read AS (
    SELECT count(DISTINCT attributes ->> 'brief_id') AS n
    FROM records
    WHERE span_name = 'brief_read'
      AND attributes ->> 'marker' = 'opened'
)
SELECT
    spend.total_tokens::float / nullif(read.n, 0) AS tokens_per_read_brief
FROM spend, read;
