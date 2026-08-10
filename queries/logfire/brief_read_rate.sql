-- Brief read rate (Phase 6 PRD §7 supporting metric).
--
-- Briefs opened ÷ Briefs published — the blunt floor: below some threshold,
-- nothing else about this feature matters. "Opened" is distinct Briefs
-- pinged marker='opened' (brief_read's own first-write-wins guarantee, D11,
-- means this is exactly distinct Briefs read, never inflated by a re-open).
-- "Published" is every brief_research_completed with outcome='published' —
-- one row per published Brief (the fenced-win, one-event-per-run contract
-- docs/metrics.md documents).
--
-- Events used: brief_research_completed (outcome), brief_read (marker).
WITH published AS (
    SELECT count(*) AS n
    FROM records
    WHERE span_name = 'brief_research_completed'
      AND attributes ->> 'outcome' = 'published'
),
opened AS (
    SELECT count(DISTINCT attributes ->> 'brief_id') AS n
    FROM records
    WHERE span_name = 'brief_read'
      AND attributes ->> 'marker' = 'opened'
)
SELECT
    opened.n::float / nullif(published.n, 0) AS brief_read_rate
FROM published, opened;
