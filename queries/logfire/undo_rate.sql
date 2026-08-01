-- Undo rate + time to undo (Phase 2B PRD §7 guardrail).
--
-- Undone Changes over applied ones, and how long the learner took to regret it.
-- This is a guardrail on PROPOSAL QUALITY, not on the undo feature: regret is
-- the signal that consent did not work — the card said one thing and the path
-- turned out to be another. A healthy undo rate is low but NOT zero; zero would
-- more likely mean the affordance is undiscoverable than that every proposal
-- was right (undo is reached from the change-history sheet, TDD §8).
--
-- Time-to-undo is what separates the two failure modes, and it is the reason
-- `minutes_since_apply` is emitted as fractional minutes rather than whole
-- ones:
--
--   * SECONDS — "that is not what I meant". The card mis-sold the edit; the
--     learner saw the real thing and reversed it immediately. Whole-minute
--     rounding would file every one of these as 0 and erase the distribution.
--   * HOURS or DAYS — a considered change of plan, which is the feature
--     working as designed (PRD §5.5) and not a quality signal at all.
--
-- The rate's numerator and denominator come from different populations in one
-- respect worth stating: a Change applied before the retention window and
-- undone inside it counts in the numerator with no matching denominator row.
-- Over any window long enough to read a rate on, that is negligible; it is not
-- corrected here because correcting it would need a join that silently drops
-- undos of old Changes, which is worse.
--
-- Undo is LAST-IN-FIRST-OUT and closes for good once the learner engages with
-- what the Change touched (D2), so this rate is bounded below by what the
-- engagement boundary already prevented — a Change the learner started working
-- through cannot appear in the numerator however much they regret it.
--
-- Events used: change_applied, change_undone (minutes_since_apply).
WITH applied AS (
    SELECT count(*) AS changes FROM records WHERE span_name = 'change_applied'
),
undone AS (
    SELECT
        count(*) AS undos,
        percentile_cont(0.5) WITHIN GROUP (
            ORDER BY (attributes ->> 'minutes_since_apply')::float
        ) AS median_minutes_to_undo,
        percentile_cont(0.95) WITHIN GROUP (
            ORDER BY (attributes ->> 'minutes_since_apply')::float
        ) AS p95_minutes_to_undo
    FROM records
    WHERE span_name = 'change_undone'
)
SELECT
    applied.changes AS changes_applied,
    undone.undos AS changes_undone,
    undone.undos::float / nullif(applied.changes, 0) AS undo_rate,
    undone.median_minutes_to_undo,
    undone.p95_minutes_to_undo
FROM applied, undone;
