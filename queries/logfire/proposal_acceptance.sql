-- Proposal acceptance (Phase 2B PRD §7 supporting metric).
--
-- Applied Changes over Proposals shown. It is read from BOTH ends, which is why
-- the raw counts are on the row and not just the ratio:
--
--   * LOW acceptance beside high depth_to_proposal means the shaper proposes
--     badly — the learner keeps asking and keeps declining what comes back.
--   * NEAR-100% acceptance is not a triumph either: it suggests the consent
--     step has become a rubber stamp, which is the failure mode the whole
--     "the tap is the consent" design (TDD §5.6) exists to prevent.
--
-- The right reading is therefore a band, not a maximum.
--
-- TWO CAVEATS, both inflating the denominator slightly, both deliberate:
--
--   * A Proposal SHOWN on a reply that then failed still counts. It is emitted
--     mid-stream where the card reaches the rail, which is what "shown" means
--     (2A's tutor_check_shown rule verbatim) — but D2 persisted nothing, so it
--     could never have been applied.
--   * A Proposal that went STALE (the learner attempted the target, another
--     Change moved the positions) counts as un-accepted. That is honest for
--     "did this offer become structure", and it means a rising staleness rate
--     reads here as falling acceptance; §5.8's coded 409s are where the
--     difference actually lives, and they are not events this phase emits.
--
-- Counted per card and per Change, not per learner: the question is about the
-- offers, and one learner applying ten of them is ten accepted offers.
--
-- Events used: proposal_shown, change_applied.
WITH shown AS (
    SELECT count(*) AS proposals FROM records WHERE span_name = 'proposal_shown'
),
applied AS (
    SELECT count(*) AS changes FROM records WHERE span_name = 'change_applied'
)
SELECT
    shown.proposals AS proposals_shown,
    applied.changes AS changes_applied,
    applied.changes::float / nullif(shown.proposals, 0) AS acceptance_rate
FROM shown, applied;
