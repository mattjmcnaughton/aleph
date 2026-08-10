-- Wait tolerance (Phase 6 PRD §7 guardrail, §4.2).
--
-- Share of researching Beats the learner is still present for when the
-- Brief lands. THE FIRST metric to read (PRD §5): with Brief prefetch
-- deferred (CONTEXT.md: Brief prefetch), every first-slice Brief is
-- researched while the learner is already on the page waiting, so this is
-- measured at its WORST case, never an average across a mix of
-- waited/prefetched runs. If learners consistently leave and never come
-- back, the fix order is prefetch first, always-on second (§4.2).
--
-- No event records "still on the page" directly. Approximated instead as:
-- the learner opened the Brief this run produced soon after the run landed.
-- This is sound because brief_research_completed carries beat_id but no
-- brief_id — a Beat resolves one research run at a time (the claim fence),
-- so the NEXT brief_read (marker='opened') for the same Beat after a
-- published run's own timestamp is unambiguously the read that run's own
-- Brief produced. A published run never opened at all — the common miss,
-- the learner left — reports NOT present, never a false positive.
--
-- PRESENT_WINDOW (5 minutes) is a deliberate round number above
-- brief_research_timeout_seconds (180s, config.py): long enough to absorb
-- the poll interval + render time a learner who never left the page would
-- still take, short enough that a read from a later, unrelated visit does
-- not count as "was still there".
--
-- Events used: brief_research_completed (outcome='published', beat_id),
-- brief_read (marker='opened', beat_id).
WITH published_runs AS (
    SELECT
        attributes ->> 'beat_id' AS beat_id,
        start_timestamp AS completed_at
    FROM records
    WHERE span_name = 'brief_research_completed'
      AND attributes ->> 'outcome' = 'published'
),
next_open_after AS (
    SELECT
        pr.beat_id,
        pr.completed_at,
        min(r.start_timestamp) AS opened_at
    FROM published_runs AS pr
    LEFT JOIN records AS r
        ON r.span_name = 'brief_read'
       AND r.attributes ->> 'marker' = 'opened'
       AND r.attributes ->> 'beat_id' = pr.beat_id
       AND r.start_timestamp >= pr.completed_at
    GROUP BY pr.beat_id, pr.completed_at
)
SELECT
    count(*) FILTER (
        WHERE opened_at IS NOT NULL
          AND opened_at - completed_at <= INTERVAL '5 minutes'
    )::float / nullif(count(*), 0) AS wait_tolerance_rate
FROM next_open_after;
