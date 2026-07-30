-- Tutor entry mix (Phase 2 PRD §7 supporting metric).
--
-- Share of learner messages that originated from a one-tap suggestion versus
-- free text. The mock claims the suggestions do real teaching work (§5.3); this
-- is the number that says whether learners actually use them, and a near-100%
-- suggestion share would say the composer is the wrong affordance (or that the
-- suggestions are the only thing anyone can think to ask).
--
-- Counted per message, not per learner: the question is what the entry surface
-- carries, and one learner sending twenty typed messages is twenty typed asks.
--
-- Events used: tutor_message_sent (source).
SELECT
    attributes ->> 'source' AS source,
    count(*) AS messages,
    count(*)::float / nullif(sum(count(*)) OVER (), 0) AS share
FROM records
WHERE span_name = 'tutor_message_sent'
GROUP BY 1;
