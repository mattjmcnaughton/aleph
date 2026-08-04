-- Flashcard keep rate (Phase 3 PRD §5 supporting metric).
--
-- Kept ÷ drafted, over every keep request. Both counts live on the SAME event
-- (`flashcards_kept`, TDD §9) by design: the ratio is a column computed inside
-- one row rather than a join between two event streams, which is what keeps
-- this metric immune to `flashcards_drafted`'s own resolution timing (a run
-- can fail and be retried before a learner ever reaches a keep screen) — a
-- keep request only ever happens against drafts that actually resolved.
--
-- A low rate is an AI-quality problem, not a UI one (PRD §5): it is the
-- production proxy the `flashcard_draft` eval kind calibrates against (TDD
-- §10), so a keep rate that drifts from what the judge predicts is the signal
-- the eval is drifting from real behaviour, not the other way round.
--
-- Read two ways, both worth keeping: the pooled rate (every kept card over
-- every drafted card, which a lesson with many drafts dominates) and the
-- per-request average (one number per keep tap, unweighted by how many cards
-- that lesson happened to draft) — a gap between the two says a few
-- high-count lessons are carrying (or dragging) the pooled number.
--
-- Events used: flashcards_kept.
WITH requests AS (
    SELECT
        (attributes ->> 'drafted_count')::int AS drafted_count,
        (attributes ->> 'kept_count')::int AS kept_count
    FROM records
    WHERE span_name = 'flashcards_kept'
)
SELECT
    count(*) AS keep_requests,
    sum(drafted_count) AS cards_drafted,
    sum(kept_count) AS cards_kept,
    sum(kept_count)::float / nullif(sum(drafted_count), 0) AS pooled_keep_rate,
    avg(kept_count::float / nullif(drafted_count, 0)) AS avg_per_request_keep_rate
FROM requests;
