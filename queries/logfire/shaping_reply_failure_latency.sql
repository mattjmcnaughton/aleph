-- Shaping reply failure rate + latency (Phase 2B PRD §7 guardrail, W21).
--
-- 2A's budgets applied to this surface unchanged (PRD §7), which is why this is
-- tutor_reply_failure_latency.sql column for column rather than a new reading:
--   * failure_rate     — resolutions that ended in an error frame.
--   * stopped_rate     — the learner aborted (the rail's stop affordance, or a
--                        disconnect; indistinguishable from the server, and
--                        both mean the learner ended their own turn).
--   * p95_ttft_ms      — latency to first token. Replies that produced no token
--                        carry a null TTFT and are skipped by percentile_cont,
--                        so this is "how long the learner waited to see
--                        something WHEN something came".
--   * p95_duration_ms  — latency to a complete reply, over SUCCESSES only, and
--                        INCLUDING queue wait behind the shared concurrency
--                        permit (both rails draw on one pool, D11). So it can
--                        exceed TUTOR_REPLY_TIMEOUT, and duration - ttft is not
--                        "streaming time".
--
-- W21 is the workflow this query serves: shaping must never be on the critical
-- path, and a shaping rail that saturates the shared permit pool would show up
-- HERE and in tutor_reply_failure_latency.sql at the same time. Reading the two
-- side by side is the whole point of keeping them identical in shape — a rise
-- in both is the pool; a rise in one is that rail.
--
-- A DECLINED EDIT IS A SUCCESS, exactly as a 2A refusal is. An out-of-vocabulary
-- ask (remove a unit, reorder, revise something already finished) answered
-- gracefully is a real, persisted turn and is deliberately not machine-tagged
-- this phase (D5, PRD §5.7b). So failure_rate means "the shaper broke", never
-- "the shaper declined" — the declines are visible only as successes with
-- has_proposal = false, which is also what an ordinary conversational turn looks
-- like. That conflation is accepted, not overlooked; the evals police decline
-- quality (docs/evals.md).
--
-- Logfire cannot carry a null OTEL attribute, so a missing TTFT arrives as the
-- JSON text `null`; the nullif() is what keeps the cast honest either way.
--
-- Events used: shaping_reply_completed (outcome, ttft_ms, duration_ms,
-- has_proposal).
SELECT
    count(*) AS replies,
    count(*) FILTER (WHERE attributes ->> 'outcome' = 'failure')::float
        / nullif(count(*), 0) AS failure_rate,
    count(*) FILTER (WHERE attributes ->> 'outcome' = 'stopped')::float
        / nullif(count(*), 0) AS stopped_rate,
    count(*) FILTER (WHERE attributes ->> 'has_proposal' = 'true')::float
        / nullif(count(*), 0) AS proposal_rate,
    percentile_cont(0.95) WITHIN GROUP (
        ORDER BY nullif(attributes ->> 'ttft_ms', 'null')::float
    ) AS p95_ttft_ms,
    percentile_cont(0.95) WITHIN GROUP (
        ORDER BY (attributes ->> 'duration_ms')::float
    ) FILTER (WHERE attributes ->> 'outcome' = 'success') AS p95_duration_ms
FROM records
WHERE span_name = 'shaping_reply_completed';
