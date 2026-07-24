-- Quick-check correctness rate (PRD §7 guardrail).
--
-- Share of Attempts whose Outcome was correct. Must sit in a sane band: near-100%
-- signals trivial questions, very low signals broken / mis-keyed ones (PRD §7).
--
-- Events used: quick_check_attempted (is_correct).
SELECT
    count(*) FILTER (WHERE attributes ->> 'is_correct' = 'true')::float
        / nullif(count(*), 0) AS correctness_rate
FROM records
WHERE span_name = 'quick_check_attempted';
