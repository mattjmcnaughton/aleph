-- Cost per read Brief (Phase 6 PRD §7 guardrail; D14a).
--
-- Total spend across every research run ÷ distinct Briefs actually opened —
-- the number that decides whether this is viable at all. D14a's $0.50
-- ceiling is arithmetic over character/token budgets, split three ways:
-- retrieval ~$0.04 + the research read ~$0.12 + the write ~$0.05. TDD §9
-- calls this query "the MEASUREMENT — if they disagree, this wins" — but a
-- query can only outrank the arithmetic if it reports the SAME kind of
-- number. An earlier version of this query summed `total_tokens` only and
-- output a bare token count (FIX 3, code-review): no dollars to compare
-- against a dollar ceiling, no retrieval line at all even though `queries`
-- and `documents_retrieved` are emitted and were read by nothing, and
-- prompt/completion tokens flattened together even though they are priced
-- 3-5x apart. This version reports dollars, split the same way D14a's own
-- arithmetic is, so "under or over $0.50" is a question this query can
-- actually answer.
--
-- RATE CONSTANTS below are CURRENT AS OF 2026-08, NOT AN INVARIANT — D14a's
-- own caveat applies here word for word: "the dollar figures assume current
-- Sonnet-class pricing... a design target, not an invariant." Re-check
-- these against `docs/deploy.md`'s model pricing and Exa's published rates
-- before trusting this query's dollar figure over any longer horizon; if
-- they drift, this query's dollars drift with them and stop meaning what
-- the header claims.
--   * MODEL_RESEARCH / MODEL_BRIEF (config.py) default to Claude Sonnet 5:
--     $3.00 per 1M prompt tokens, $15.00 per 1M completion tokens.
--   * Exa neuralSearch (services/retrieval.py::ExaRetriever, CONFIRMED FROM
--     DOCS): $0.005 per query (one Exa `/search` call per plan query, the
--     emitted `queries` count) + $0.001 per page of content (the raw,
--     pre-filter `documents_retrieved` count) — the same arithmetic
--     `ExaRetriever.search`'s own docstring uses to cost a run.
--
-- Every research run's tokens/queries/documents count in the numerator, not
-- published-only: a Skipped or failed run still spends retrieval and model
-- cost, and excluding it would make the ceiling look healthier than the
-- learner's actual bill. The denominator is distinct Briefs OPENED, not
-- published — the question is viability per Brief a learner actually got
-- value from, not per Brief merely produced.
--
-- Right-censored (FIX 6, code-review; matches activation_rate.sql's clamp
-- idiom): a research run from the last 24h has not yet given its Brief a
-- fair chance to be opened, so its spend would count against the ceiling
-- before it has had the chance to earn a read — at launch, with a fresh
-- Beat's Briefs still arriving, that mechanically inflates dollars-per-read
-- (every Beat has 2 Briefs, only the older one has had time to be opened ->
-- spend for both, a read for one -> ~2x). The same 24h window
-- `brief_read_rate.sql` uses, for the same reason.
--
-- Events used: brief_research_completed (queries, documents_retrieved,
-- prompt_tokens, completion_tokens, every outcome), brief_read
-- (marker='opened').
WITH spend AS (
    SELECT
        sum((attributes ->> 'prompt_tokens')::bigint) AS prompt_tokens,
        sum((attributes ->> 'completion_tokens')::bigint) AS completion_tokens,
        sum((attributes ->> 'queries')::bigint) AS retrieval_calls,
        sum(
            (attributes ->> 'queries')::bigint * 0.005::float
            + (attributes ->> 'documents_retrieved')::bigint * 0.001::float
            + (attributes ->> 'prompt_tokens')::bigint * 0.000003::float
            + (attributes ->> 'completion_tokens')::bigint * 0.000015::float
        ) AS total_dollars
    FROM records
    WHERE span_name = 'brief_research_completed'
      AND start_timestamp < now() - INTERVAL '1 day'
),
read AS (
    SELECT count(DISTINCT attributes ->> 'brief_id') AS n
    FROM records
    WHERE span_name = 'brief_read'
      AND attributes ->> 'marker' = 'opened'
)
SELECT
    spend.prompt_tokens::float / nullif(read.n, 0)
        AS prompt_tokens_per_read_brief,
    spend.completion_tokens::float / nullif(read.n, 0)
        AS completion_tokens_per_read_brief,
    spend.retrieval_calls::float / nullif(read.n, 0)
        AS retrieval_calls_per_read_brief,
    spend.total_dollars / nullif(read.n, 0)
        AS dollars_per_read_brief
FROM spend, read;
