-- Authoritative accepted receipts, including cleared payloads. Bind :as_of UTC.
-- One receipt = one acceptance. Replays never create another receipt.
-- Windows are elapsed and inclusive; immature windows never enter either rate.
-- Deleted targets lose their activity history: unknown, not a negative outcome.
WITH receipts AS (
    SELECT r.id, r.user_id, r.kind, r.accepted_at,
        r.accepted_path_id, r.accepted_beat_id,
        r.accepted_at + CASE WHEN r.kind = 'path' THEN INTERVAL '168 hours'
                            ELSE INTERVAL '336 hours' END AS deadline
    FROM learning_recommendations r
    WHERE r.status = 'accepted' AND r.accepted_at <= CAST(:as_of AS timestamptz)
), outcomes AS (
    SELECT r.kind, r.deadline <= CAST(:as_of AS timestamptz) AS mature,
        CASE WHEN r.kind = 'path' THEN p.id IS NULL ELSE b.id IS NULL END AS unknown,
        CASE WHEN r.kind = 'path' THEN EXISTS (
            SELECT 1 FROM lessons l WHERE l.path_id = p.id
              AND l.completed_at >= r.accepted_at AND l.completed_at <= r.deadline
        ) ELSE EXISTS (
            SELECT 1 FROM briefs f WHERE f.beat_id = b.id AND f.kind = 'published'
              AND f.read_at >= r.accepted_at AND f.read_at <= r.deadline
        ) END AS succeeded
    FROM receipts r
    LEFT JOIN paths p ON p.id = r.accepted_path_id AND p.user_id = r.user_id
    LEFT JOIN beats b ON b.id = r.accepted_beat_id AND b.user_id = r.user_id
), counts AS (
    SELECT kind, count(*) AS accepted,
        count(*) FILTER (WHERE NOT mature) AS pending,
        count(*) FILTER (WHERE mature) AS mature,
        count(*) FILTER (WHERE mature AND succeeded) AS succeeded,
        count(*) FILTER (WHERE mature AND NOT unknown AND NOT succeeded) AS no_activity,
        count(*) FILTER (WHERE mature AND unknown) AS deleted_unknown,
        count(*) FILTER (WHERE mature AND NOT unknown) AS known
    FROM outcomes GROUP BY kind
)
SELECT *, succeeded::float / nullif(known, 0) AS known_rate,
    succeeded::float / nullif(mature, 0) AS confirmed_lower_bound
FROM counts ORDER BY kind;
