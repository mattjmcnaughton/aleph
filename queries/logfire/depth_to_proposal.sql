-- Depth to proposal (Phase 2B PRD §7 supporting metric).
--
-- Median (and p95) learner messages before the FIRST Proposal in a shaping
-- conversation. It is the "is the shaper listening or interrogating" number,
-- and it is only readable beside proposal_acceptance.sql:
--
--   * deep and ACCEPTED — the conversation is doing real work.
--   * deep and DECLINED — the shaper is not converging; the learner is asking
--     repeatedly and getting offers they do not want.
--   * shallow and ACCEPTED — the one-shot ask works, which is the §5.3
--     suggestion-chip claim.
--   * shallow and DECLINED — it is proposing on reflex.
--
-- Counted UP TO AND INCLUDING the ask that produced the card, so a proposal on
-- the first message reads as 1, never 0. Anything else would need "the message
-- that caused it" to be excluded, which is a different question ("how much
-- context did it need") and would put a floor of 0 on a quantity that is never
-- really zero — no Proposal exists without an ask.
--
-- Messages are counted at ADMISSION (shaping_message_sent), so it includes
-- turns whose reply then failed or was stopped. That is the right denominator
-- for "how many times did the learner have to ask" — the learner did ask — but
-- it means this runs slightly ABOVE the persisted thread length, by exactly the
-- failed and stopped replies. 2A's tutor_depth.sql inherits the same property
-- for the same reason.
--
-- Only conversations that PRODUCED a proposal appear. A conversation that never
-- got one has no depth-to-proposal, and folding it in as an infinity (or as its
-- message count) would silently mix "took a while" with "never happened";
-- proposal_acceptance.sql and shaping_adoption.sql are where the never-happened
-- population is visible.
--
-- Scoped per (account, path) because there is exactly one shaping conversation
-- per path (PRD §5.8) — the same shape tutor_depth.sql uses.
--
-- Events used: shaping_message_sent, proposal_shown.
WITH first_proposal AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        attributes ->> 'path_id' AS path_id,
        min(start_timestamp) AS shown_at
    FROM records
    WHERE span_name = 'proposal_shown'
    GROUP BY 1, 2
),
messages_before AS (
    SELECT
        fp.account_id,
        fp.path_id,
        count(*) AS messages
    FROM first_proposal AS fp
    JOIN records AS m
        ON m.span_name = 'shaping_message_sent'
       AND m.attributes ->> 'account_id' = fp.account_id
       AND m.attributes ->> 'path_id' = fp.path_id
       AND m.start_timestamp <= fp.shown_at
    GROUP BY 1, 2
)
SELECT
    count(*) AS conversations_with_a_proposal,
    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY messages::float
    ) AS median_messages_to_proposal,
    percentile_cont(0.95) WITHIN GROUP (
        ORDER BY messages::float
    ) AS p95_messages_to_proposal
FROM messages_before;
