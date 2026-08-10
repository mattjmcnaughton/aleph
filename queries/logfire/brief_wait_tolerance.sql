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
-- brief_research_completed carries beat_id but no brief_id, so this joins
-- by beat_id and timing, not brief_id — sound because a Beat resolves one
-- research run at a time (the claim fence), so the NEXT brief_read
-- (marker='opened') for that Beat after the run's own completion is a
-- reasonable proxy for the read that run's Brief produced.
--
-- **This is a proxy, NOT unambiguous** (FIX 8, code-review — correcting an
-- earlier version of this header that claimed otherwise, in the same words
-- docs/metrics.md already used correctly: "unambiguously the read that
-- run's own Brief produced" / "NOT present, never a false positive"). A
-- learner who opens an OLD, unrelated Brief for the same Beat inside the
-- 5-minute window is misread as present for the NEW one — the join has no
-- `brief_id` to pin the read to a specific Brief, because
-- `brief_research_completed` never carries one (it fires before any Brief
-- row exists). `brief_read` DOES emit `age_days` — days since THAT read
-- Brief's own `published_on` — precisely so a query can tell a fresh open
-- from a stale one; requiring `age_days = 0` (FIX 8) removes most of the
-- ambiguity, since the just-published Brief is the only one that can be zero
-- days old the moment this run lands. Not a complete fix: a learner who
-- reopens a genuinely same-day-published OLDER Brief for this Beat inside
-- the window would still be misread — but D4's cadence floor means a Beat
-- cannot publish twice in one day, so that residual case does not arise in
-- practice. Read the honest version of this caveat in docs/metrics.md, which
-- stated it correctly before this header did.
--
-- PRESENT_WINDOW (5 minutes) is a deliberate round number above
-- brief_research_timeout_seconds (180s, config.py): long enough to absorb
-- the poll interval + render time a learner who never left the page would
-- still take, short enough that a read from a later, unrelated visit does
-- not count as "was still there".
--
-- Right-censored (FIX 6, code-review; matches activation_rate.sql's clamp
-- idiom): a run that completed in the last 5 minutes has not yet had its
-- own PRESENT_WINDOW to be opened within — counting it "not present" this
-- instant, before its window has even closed, is exactly the "purely
-- mechanical dilution" activation_rate.sql's own comment warns against.
-- `published_runs` excludes runs whose window has not yet fully elapsed.
--
-- Events used: brief_research_completed (outcome='published', beat_id),
-- brief_read (marker='opened', beat_id, age_days).
WITH published_runs AS (
    SELECT
        attributes ->> 'beat_id' AS beat_id,
        start_timestamp AS completed_at
    FROM records
    WHERE span_name = 'brief_research_completed'
      AND attributes ->> 'outcome' = 'published'
      AND start_timestamp < now() - INTERVAL '5 minutes'
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
       AND (r.attributes ->> 'age_days')::int = 0
    GROUP BY pr.beat_id, pr.completed_at
)
SELECT
    count(*) FILTER (
        WHERE opened_at IS NOT NULL
          AND opened_at - completed_at <= INTERVAL '5 minutes'
    )::float / nullif(count(*), 0) AS wait_tolerance_rate
FROM next_open_after;
