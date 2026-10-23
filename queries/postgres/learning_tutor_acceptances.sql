-- ID-only reconciliation export. Bind the same UTC :as_of as follow-through.
-- One row per durable receipt, even after Clear or target deletion. No content.
SELECT user_id AS account_id, id AS recommendation_id, kind AS target_kind,
    coalesce(accepted_path_id, accepted_beat_id) AS target_id, accepted_at
FROM learning_recommendations
WHERE status = 'accepted' AND accepted_at <= CAST(:as_of AS timestamptz)
ORDER BY user_id, id;
