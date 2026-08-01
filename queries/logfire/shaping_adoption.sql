-- Shaping adoption (Phase 2B PRD §7 supporting metric).
--
-- % of learners with a READY path who apply at least one Change. The number the
-- primary shaping_yield gap must be read beside: a beautiful yield on 2%
-- adoption is a handful of enthusiasts, not a product claim.
--
-- The denominator is "has a ready path", not "activated" and not "all accounts",
-- and the difference is the whole point: there is nothing to shape until the
-- outline exists (PRD §5.1, enforced server-side as a 409), so an account that
-- never got one could not have adopted shaping and counting it would measure
-- Phase 1's generation funnel instead. That population is derived from
-- `outline_generated` with outcome `ready` — the same fenced event
-- path_start_rate.sql uses for the same "this path became shapeable" fact.
--
-- No maturity clamp, for tutor_adoption.sql's reason: this is a ratio *within*
-- a qualified set rather than against the full signup cohort, so young accounts
-- cannot dilute it — and clamping would blind the panel during exactly the
-- rollout week when adoption is the number being watched.
--
-- APPLIED, not "sent a message": the consent tap is the adoption event this
-- phase claims. A learner who chatted with the shaper and applied nothing has
-- not shaped their path; shaping_reply_failure_latency.sql and
-- proposal_acceptance.sql are where that population shows up.
--
-- Events used: outline_generated (the ready-path denominator), change_applied.
WITH ready_paths AS (
    SELECT DISTINCT attributes ->> 'account_id' AS account_id
    FROM records
    WHERE span_name = 'outline_generated'
      AND attributes ->> 'outcome' = 'ready'
),
shapers AS (
    SELECT DISTINCT attributes ->> 'account_id' AS account_id
    FROM records
    WHERE span_name = 'change_applied'
)
SELECT
    count(*) AS learners_with_a_ready_path,
    count(*) FILTER (WHERE s.account_id IS NOT NULL) AS learners_who_shaped,
    count(*) FILTER (WHERE s.account_id IS NOT NULL)::float
        / nullif(count(*), 0) AS adoption_rate
FROM ready_paths AS r
LEFT JOIN shapers AS s ON s.account_id = r.account_id;
