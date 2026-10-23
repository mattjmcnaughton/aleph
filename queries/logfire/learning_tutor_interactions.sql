-- Observational distinct card/surface/session/actions, not total clicks.
-- Decisions have no event ID: count distinct card/decision, not dismiss cycles.
WITH interactions AS (
    SELECT DISTINCT attributes ->> 'account_id' AS account_id,
        attributes ->> 'recommendation_id' AS recommendation_id,
        attributes ->> 'interaction' AS action,
        attributes ->> 'surface' AS surface,
        attributes ->> 'session_id' AS session_id
    FROM records WHERE span_name = 'learning_recommendation_interacted'
), decisions AS (
    SELECT DISTINCT attributes ->> 'account_id' AS account_id,
        attributes ->> 'recommendation_id' AS recommendation_id,
        attributes ->> 'decision' AS action
    FROM records WHERE span_name = 'learning_recommendation_decided'
), observations AS (
    SELECT account_id, recommendation_id, action FROM interactions
    UNION ALL
    SELECT account_id, recommendation_id, action FROM decisions
)
SELECT action, count(*) AS observations,
    count(DISTINCT (account_id, recommendation_id)) AS recommendations
FROM observations GROUP BY action ORDER BY action;
