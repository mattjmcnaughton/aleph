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
-- Right-censored (FIX 6, code-review; matches activation_rate.sql's clamp
-- idiom, "a purely mechanical dilution... biases the north star low"):
-- only Briefs published at least 24h ago count in the denominator. A Brief
-- published minutes ago has not yet had a fair chance to be opened, and
-- counting it unread this instant is exactly that mechanical dilution —
-- worst right when a Beat is fresh (every one of its Briefs is new) or
-- traffic spikes (the freshest Briefs dominate the denominator), which is
-- precisely when this guardrail is watched most closely.
--
-- Events used: brief_research_completed (outcome), brief_read (marker).
WITH published AS (
    SELECT count(*) AS n
    FROM records
    WHERE span_name = 'brief_research_completed'
      AND attributes ->> 'outcome' = 'published'
      AND start_timestamp < now() - INTERVAL '1 day'
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
