-- Recall rate by rung (Phase 3 PRD §5 supporting metric).
--
-- "Got it" over all reviews, grouped by the ladder rung the card was AT when
-- graded (`rung_before` — the interval that was actually being tested, not
-- `rung_after`, which is `apply_grade`'s output and would group a review by
-- the answer it produced rather than the question it asked). `rung` here is a
-- position in the configured `flashcard_ladder` (TDD §5.1/D2's "the ladder is
-- a parameter, never a module constant"), so it is comparable across any
-- length or day-count the ladder is ever tuned to.
--
-- Recall collapsing at a given rung is the ladder telling you that interval
-- is too long (PRD §5) — read this per rung, never pooled, or a healthy low
-- rung can hide a collapsing high one.
--
-- Note: a resubmitted double-tap of `again` at rung 0 can append a second
-- review row for the same lapse (`services/reviews.py::_grade`'s own
-- documented limit — the stale-rung guard does not catch this one case), so
-- rung 0's review count is a slight over-count relative to distinct lapses.
-- Every other rung is unaffected.
--
-- Events used: review_graded.
SELECT
    (attributes ->> 'rung_before')::int AS rung,
    count(*) AS reviews,
    count(*) FILTER (WHERE attributes ->> 'grade' = 'got_it') AS got_it_count,
    count(*) FILTER (WHERE attributes ->> 'grade' = 'got_it')::float
        / nullif(count(*), 0) AS recall_rate
FROM records
WHERE span_name = 'review_graded'
GROUP BY 1
ORDER BY 1;
