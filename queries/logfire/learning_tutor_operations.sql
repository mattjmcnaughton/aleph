-- Terminal observations only: not attempted requests, nor all model admissions.
-- Completed and needs_context are the two successful durable output outcomes.
-- Deduplicate operation IDs before summing tokens. Missing events remain missing.
-- Tokens per acceptance is a period-level ratio, NOT causal per-card model cost.
WITH operations AS (
    SELECT DISTINCT ON (attributes ->> 'account_id', attributes ->> 'operation_id')
        attributes ->> 'account_id' AS account_id,
        attributes ->> 'operation_id' AS operation_id,
        attributes ->> 'kind' AS kind,
        attributes ->> 'outcome' AS outcome,
        (attributes ->> 'duration_ms')::float AS duration_ms,
        (attributes ->> 'input_tokens')::bigint AS input_tokens,
        (attributes ->> 'output_tokens')::bigint AS output_tokens,
        (attributes ->> 'model_admitted')::boolean AS model_admitted
    FROM records WHERE span_name = 'learning_tutor_operation_completed'
    ORDER BY attributes ->> 'account_id', attributes ->> 'operation_id', start_timestamp
), acceptances AS (
    SELECT DISTINCT attributes ->> 'account_id' AS account_id,
        attributes ->> 'recommendation_id' AS recommendation_id
    FROM records WHERE span_name = 'learning_recommendation_accepted'
)
SELECT kind, count(*) AS operations,
    count(*) FILTER (WHERE outcome IN ('completed', 'needs_context')) AS succeeded,
    count(*) FILTER (WHERE outcome NOT IN ('completed', 'needs_context')) AS failed,
    count(*) FILTER (WHERE outcome NOT IN ('completed', 'needs_context'))::float
        / nullif(count(*), 0) AS failure_rate,
    count(*) FILTER (WHERE model_admitted) AS model_admissions,
    avg(duration_ms) AS mean_duration_ms,
    sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
    sum(input_tokens + output_tokens) AS total_tokens,
    sum(input_tokens + output_tokens)::float
        / nullif(count(*) FILTER (WHERE outcome IN ('completed', 'needs_context')), 0)
        AS tokens_per_success,
    sum(input_tokens + output_tokens)::float
        / nullif((SELECT count(*) FROM acceptances), 0) AS tokens_per_observed_acceptance
FROM operations GROUP BY kind ORDER BY kind;
