-- Tutor depth (Phase 2 PRD §7 supporting metric + the turns-per-conversation
-- guardrail — both read off this one query).
--
-- Median and p95 learner messages, at two scopes:
--   * conversation — one per path (PRD §5.8), so this is "turns per
--     conversation": the number that says whether the §6 context window
--     (TUTOR_CONTEXT_TURNS) is set somewhere sane. A p95 far above the window
--     means real conversations are routinely losing their early turns.
--   * lesson — depth within a single lesson-with-tutor-use, which is the
--     "did one question turn into a dialogue" reading.
--
-- Turns, not messages: a turn is a learner message and the tutor reply that
-- answered it, written as a pair (D2), so counting the learner side counts each
-- turn exactly once rather than twice.
--
-- Counted at ADMISSION, not persistence: tutor_message_sent fires when the turn
-- is admitted, and D2 persists nothing when the reply then fails or is stopped.
-- So these counts are "turns the learner asked for" and run ABOVE the persisted
-- thread length, by exactly the failed and stopped replies. That is the right
-- denominator for "did one question turn into a dialogue" (the learner did ask),
-- but it means the p95 read against TUTOR_CONTEXT_TURNS runs slightly HIGH —
-- the context window only ever sees the persisted turns, so the window looks
-- tighter here than it is.
--
-- Events used: tutor_message_sent (account_id, path_id, lesson_id).
WITH per_conversation AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        attributes ->> 'path_id' AS path_id,
        count(*) AS turns
    FROM records
    WHERE span_name = 'tutor_message_sent'
    GROUP BY 1, 2
),
per_lesson AS (
    SELECT
        attributes ->> 'path_id' AS path_id,
        attributes ->> 'lesson_id' AS lesson_id,
        count(*) AS turns
    FROM records
    WHERE span_name = 'tutor_message_sent'
    GROUP BY 1, 2
)
SELECT
    'conversation' AS scope,
    count(*) AS conversations,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY turns::float) AS median_turns,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY turns::float) AS p95_turns
FROM per_conversation
UNION ALL
SELECT
    'lesson' AS scope,
    count(*) AS conversations,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY turns::float) AS median_turns,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY turns::float) AS p95_turns
FROM per_lesson;
