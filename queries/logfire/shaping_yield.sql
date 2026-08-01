-- Shaping yield — Phase 2B's PRIMARY metric (PRD §7).
--
-- Of applied Changes, the share whose created or revised lessons the learner
-- then ENGAGES with (records an Attempt or completes) within 7 days of applying.
--
-- Stated so it can fail, and aimed at one specific failure mode: if learners
-- apply changes and never touch what they asked for, shaping is theatre —
-- adding lessons *feels* like progress and substitutes for doing them. A low
-- yield argues against Phase 4, which would generate more proposals.
--
-- The join key is `change_applied.lesson_ids`: shaping events carry no
-- `lesson_id` (a shaping turn is path-level, PRD §5.1), so the ids an APPLY
-- actually touched ride the change event as a payload-derived field — created
-- lesson ids from the Change's own inverse, revised ones from its operations.
-- Logfire carries a list attribute as JSON TEXT, so it is read back through
-- `::jsonb` and unnested; a bare `->` would work in Logfire but not in the
-- executed replay, and one spelling that works in both is worth more than two.
--
-- ENGAGEMENT IS PHASE 1'S OWN EVENTS, unchanged: `quick_check_attempted` OR
-- `lesson_completed` is exactly the CONTEXT.md "Engaged" boundary the shaping
-- backend enforces at proposal validation, apply and undo. Reusing them rather
-- than emitting a shaping-specific engagement event is what makes this metric
-- comparable with the Phase 1 numbers beside it.
--
-- MATURITY CLAMP (deliberate, and the one place this phase pays for honesty):
-- only Changes whose 7-day window has already CLOSED are counted. Without it, a
-- Change applied an hour ago — which cannot have been engaged with yet —
-- dilutes the rate downward, and the primary metric would read worst exactly
-- when adoption is climbing. Same call activation_rate.sql makes; the cost is
-- that this panel is empty for the first week after launch, and the unclamped
-- read is one deleted line away when that week is the question.
--
-- CAVEAT — an UNDONE Change stays in the denominator. The learner did apply it,
-- and an addition that was undone can no longer be engaged with, so it counts
-- as unyielded. That is the honest reading (undoing is a way of not engaging),
-- and undo_rate.sql is what separates "regretted it" from "ignored it".
--
-- Events used: change_applied (the cohort + its lesson ids),
-- quick_check_attempted / lesson_completed (the engagement).
WITH applied AS (
    SELECT
        attributes ->> 'change_id' AS change_id,
        attributes ->> 'account_id' AS account_id,
        (attributes ->> 'lesson_ids')::jsonb AS lesson_ids,
        start_timestamp AS applied_at
    FROM records
    WHERE span_name = 'change_applied'
      -- The clamp: only windows that have closed (see the note above).
      AND start_timestamp < now() - INTERVAL '7 days'
),
applied_lessons AS (
    SELECT
        a.change_id,
        a.account_id,
        a.applied_at,
        touched.lesson_id
    FROM applied AS a
    CROSS JOIN LATERAL jsonb_array_elements_text(a.lesson_ids) AS touched(lesson_id)
),
engagement AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        attributes ->> 'lesson_id' AS lesson_id,
        start_timestamp AS engaged_at
    FROM records
    WHERE span_name IN ('quick_check_attempted', 'lesson_completed')
)
SELECT
    count(DISTINCT al.change_id) AS changes_applied,
    count(DISTINCT al.change_id) FILTER (WHERE e.lesson_id IS NOT NULL)::float
        / nullif(count(DISTINCT al.change_id), 0) AS yield_rate
FROM applied_lessons AS al
LEFT JOIN engagement AS e
    ON e.account_id = al.account_id
   AND e.lesson_id = al.lesson_id
   AND e.engaged_at >= al.applied_at
   AND e.engaged_at <= al.applied_at + INTERVAL '7 days';
