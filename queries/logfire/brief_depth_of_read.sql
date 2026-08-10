-- Depth of read (Phase 6 PRD §7 supporting metric).
--
-- Share of opened Briefs whose Sources block was also reached: distinct
-- Briefs pinged marker='sources' ÷ distinct Briefs pinged marker='opened'.
-- Is a Brief being read, or glanced at? A feature about provenance should
-- be able to show that provenance gets used — this is the second signal,
-- distinct from opening alone, PRD §5 asks for.
--
-- Events used: brief_read (marker='opened' and marker='sources').
WITH opened AS (
    SELECT count(DISTINCT attributes ->> 'brief_id') AS n
    FROM records
    WHERE span_name = 'brief_read'
      AND attributes ->> 'marker' = 'opened'
),
sources_seen AS (
    SELECT count(DISTINCT attributes ->> 'brief_id') AS n
    FROM records
    WHERE span_name = 'brief_read'
      AND attributes ->> 'marker' = 'sources'
)
SELECT
    sources_seen.n::float / nullif(opened.n, 0) AS depth_of_read_rate
FROM opened, sources_seen;
