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
-- (we read plenty and it was chum) — TDD §15's table, extended by FIX 4 with
-- the row `documents_after_filters` alone can distinguish:
--
--   documents_retrieved | documents_after_filters | findings | reading
--   healthy               healthy                   healthy   a genuinely quiet week (survivors=0 either way)
--   low                   low                        low       retrieval RECALL — nothing there to find
--   healthy               low                        low       RECALL-shaped, but retrieve()'s own filters
--                                                               did it, not the source (dedupe/dated/empty)
--   healthy               healthy                    low       retrieval PRECISION — read plenty, it was chum
--
-- So alongside the rate this returns the funnel's own four counts —
-- `documents_retrieved`, `documents_after_filters`, `findings`, `survivors`
-- — averaged over each Beat's SKIPPED runs (the runs the rate alone cannot
-- diagnose) AND, since FIX 4 (code-review), over its PUBLISHED runs too: the
-- healthy baseline the skipped-side averages previously had nothing to
-- compare against. Without it, a Beat whose skip rate is 100% — the one row
-- that most needs diagnosing — had no "healthy" reading on that SPECIFIC
-- Beat to read the skipped average against at all; a global sense of
-- "healthy" does not substitute; retrieval volume varies by subject.
--
-- `documents_after_filters` (FIX 4) is the second addition: emitted since
-- AL-540 shipped, read by no query until now. It disambiguates what
-- `documents_retrieved` alone cannot: 5 raw documents can retrieve() down to
-- 2 survivors of its own dedupe/dated/non-empty filters, and only that
-- SECOND count says whether "healthy documents_retrieved, low findings" is a
-- genuine retrieval PRECISION failure (chum reached the researcher) or a
-- RECALL-shaped one the filters manufactured out of a raw count that looked
-- fine (retrieve() ate almost everything before the researcher ever saw it).
--
-- Read each Beat's skipped-side averages against its OWN published-side
-- averages, per the table above, to place it in one of its three cases.
-- `avg_survivors_when_skipped` is ~0 by definition of "skipped" (a sanity
-- check, not a diagnostic). What no query here can do is name the query that
-- WOULD have worked, or catch retrieval confidently returning documents
-- about the wrong half of a subject — that has one detector, a person
-- reading the week's real news and comparing (a dogfooding ritual, AL-570),
-- not this dashboard.
--
-- Events used: brief_research_completed (outcome, documents_retrieved,
-- documents_after_filters, findings, survivors), grouped by beat_id.
WITH runs AS (
    SELECT
        attributes ->> 'beat_id' AS beat_id,
        attributes ->> 'outcome' AS outcome,
        (attributes ->> 'documents_retrieved')::int AS documents_retrieved,
        (attributes ->> 'documents_after_filters')::int AS documents_after_filters,
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
    avg(documents_after_filters) FILTER (WHERE outcome = 'skipped')
        AS avg_documents_after_filters_when_skipped,
    avg(findings) FILTER (WHERE outcome = 'skipped')
        AS avg_findings_when_skipped,
    avg(survivors) FILTER (WHERE outcome = 'skipped')
        AS avg_survivors_when_skipped,
    avg(documents_retrieved) FILTER (WHERE outcome = 'published')
        AS avg_documents_retrieved_when_published,
    avg(documents_after_filters) FILTER (WHERE outcome = 'published')
        AS avg_documents_after_filters_when_published,
    avg(findings) FILTER (WHERE outcome = 'published')
        AS avg_findings_when_published,
    avg(survivors) FILTER (WHERE outcome = 'published')
        AS avg_survivors_when_published
FROM runs
GROUP BY beat_id
ORDER BY skip_rate DESC NULLS LAST;
