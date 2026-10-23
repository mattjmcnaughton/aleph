-- Observed acceptances only, NOT the authoritative denominator. Reconcile these
-- IDs with accepted learning_recommendations receipts over the same scope/time.
-- No Topic, focus, rationale or chat text. First observation deduplicates replay.
SELECT DISTINCT ON (attributes ->> 'account_id', attributes ->> 'recommendation_id')
    attributes ->> 'account_id' AS account_id,
    attributes ->> 'recommendation_id' AS recommendation_id,
    attributes ->> 'target_kind' AS target_kind,
    attributes ->> 'target_id' AS target_id,
    start_timestamp AS observed_at
FROM records
WHERE span_name = 'learning_recommendation_accepted'
ORDER BY attributes ->> 'account_id', attributes ->> 'recommendation_id', start_timestamp;
