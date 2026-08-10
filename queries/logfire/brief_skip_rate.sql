-- Skip rate + the retrieval funnel (Phase 6 PRD §7 guardrail; TDD §15).
--
-- Skipped ÷ every research run, PER BEAT — calibrates PRD §4.6 in both
-- directions: near zero means the novelty gate is not gating and filler is
-- shipping; consistently high on one Beat means weekly is faster than that
-- subject moves (§8 Q7); high across every Beat means the gate is too
-- strict. It is also the number that has to look healthy before a daily
-- cadence is trusted (§4.11).
--
-- RAW skip rate cannot tell a genuinely quiet week apart from thin
-- retrieval RECALL (we found little to read) from thin retrieval PRECISION
-- (we read plenty and it was chum) — TDD §15's table:
--
--   documents_retrieved | findings | survivors | reading
--   healthy              healthy    0           a genuinely quiet week
--   low                  low        0           retrieval RECALL
--   healthy              low        0           retrieval PRECISION
--
-- So alongside the rate this returns the funnel's own three counts,
-- averaged over each Beat's SKIPPED runs specifically — the runs the rate
-- alone cannot diagnose. Read avg_documents_retrieved_when_skipped against
-- avg_findings_when_skipped, per row, per the table above, to place a Beat
-- in one of its three cases (avg_survivors_when_skipped is ~0 by
-- definition of "skipped" — present for sanity-checking the query, not for
-- diagnosis). This is the instrument; naming the query that WOULD have
-- worked, or catching retrieval confidently returning documents about the
-- wrong half of a subject, is a dogfooding ritual, not this dashboard
-- (TDD §15, AL-570).
--
-- Events used: brief_research_completed (outcome, documents_retrieved,
-- findings, survivors), grouped by beat_id.
WITH runs AS (
    SELECT
        attributes ->> 'beat_id' AS beat_id,
        attributes ->> 'outcome' AS outcome,
        (attributes ->> 'documents_retrieved')::int AS documents_retrieved,
        (attributes ->> 'findings')::int AS findings,
        (attributes ->> 'survivors')::int AS survivors
    FROM records
    WHERE span_name = 'brief_research_completed'
)
SELECT
    beat_id,
    count(*) AS total_runs,
    count(*) FILTER (WHERE outcome = 'skipped') AS skipped_runs,
    count(*) FILTER (WHERE outcome = 'skipped')::float
        / nullif(count(*), 0) AS skip_rate,
    avg(documents_retrieved) FILTER (WHERE outcome = 'skipped')
        AS avg_documents_retrieved_when_skipped,
    avg(findings) FILTER (WHERE outcome = 'skipped')
        AS avg_findings_when_skipped,
    avg(survivors) FILTER (WHERE outcome = 'skipped')
        AS avg_survivors_when_skipped
FROM runs
GROUP BY beat_id
ORDER BY skip_rate DESC NULLS LAST;
