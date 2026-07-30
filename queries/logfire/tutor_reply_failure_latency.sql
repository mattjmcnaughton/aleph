-- Tutor reply failure rate + latency (Phase 2 PRD §7 guardrail).
--
-- The ops guardrail for the streamed reply, watched from day one:
--   * failure_rate — resolutions that ended in an error frame. A refusal is a
--     SUCCESS here (it is a real, persisted turn and is deliberately not
--     machine-tagged, D5), so this is genuinely "the tutor broke", not "the
--     tutor declined".
--   * stopped_rate — the learner aborted (the rail's stop affordance, or a
--     disconnect). Reported beside the failure rate rather than folded into it:
--     an abort is learner behaviour, and a rising abort rate is a latency or
--     usefulness signal, not an outage.
--   * p95_ttft_ms — PRD §5.9's latency to first token. Replies that never
--     produced a token carry a null TTFT and are skipped by percentile_cont, so
--     this is "how long the learner waited to see something *when something
--     came*"; the timeouts are counted in failure_rate instead of being folded
--     in as an enormous TTFT.
--   * p95_duration_ms — latency to a complete reply, over SUCCESSES only. A
--     timed-out reply's duration is the timeout constant, so including failures
--     would make the panel report TUTOR_REPLY_TIMEOUT rather than how long a
--     working reply takes. It is timed from the moment the turn starts being
--     produced — BEFORE the tutor concurrency permit is acquired — so it
--     INCLUDES any queue wait behind the semaphore. That is deliberate: this is
--     learner-felt latency (ask → settled reply), not the model's own span, and
--     a saturated permit pool shows up here rather than hiding. Note the
--     consequence: p95_duration_ms can exceed TUTOR_REPLY_TIMEOUT, which bounds
--     only the model run, and duration - ttft is not "streaming time" because
--     TTFT is clocked from inside the permit.
--
-- Logfire cannot carry a null OTEL attribute, so a missing TTFT arrives as the
-- JSON text `null`; the nullif() is what keeps the cast honest either way.
--
-- Events used: tutor_reply_completed (outcome, ttft_ms, duration_ms).
SELECT
    count(*) AS replies,
    count(*) FILTER (WHERE attributes ->> 'outcome' = 'failure')::float
        / nullif(count(*), 0) AS failure_rate,
    count(*) FILTER (WHERE attributes ->> 'outcome' = 'stopped')::float
        / nullif(count(*), 0) AS stopped_rate,
    percentile_cont(0.95) WITHIN GROUP (
        ORDER BY nullif(attributes ->> 'ttft_ms', 'null')::float
    ) AS p95_ttft_ms,
    percentile_cont(0.95) WITHIN GROUP (
        ORDER BY (attributes ->> 'duration_ms')::float
    ) FILTER (WHERE attributes ->> 'outcome' = 'success') AS p95_duration_ms
FROM records
WHERE span_name = 'tutor_reply_completed';
