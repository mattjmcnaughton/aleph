-- Edit-shape mix (Phase 2B PRD §7 supporting metric).
--
-- Additions versus Revisions — "which lever do learners actually want". Two
-- rows, because the two scopes answer different questions and disagreeing is
-- the interesting case:
--
--   * `proposed` — what the shaper offered. Its mix is partly the SHAPER's
--     preference, not the learner's.
--   * `applied`  — what the learner consented to. This is the answer to the §7
--     question; a mix that swings between the two rows says the shaper is
--     reaching for the wrong lever and the learner is correcting it.
--
-- The two counts are deliberately not symmetric in what they count, and both
-- are in the same unit — LESSONS:
--
--   * `lessons_added` sums `n_add_lessons`, which counts lessons rather than
--     operations: one Addition carrying three lessons is not the same ask as
--     one carrying one.
--   * `lessons_revised` sums `n_revisions`, which counts operations — and a
--     Revision targets exactly one lesson by construction (the closed
--     vocabulary, TDD D1), so it is already a lesson count.
--
-- `with_new_unit` counts payloads that brought a new unit along: the "this is a
-- new topic, not more of the same" signal, which is qualitative, hence a count
-- of payloads rather than of units.
--
-- Events used: proposal_shown, change_applied (n_add_lessons, n_revisions,
-- new_unit).
WITH proposed AS (
    SELECT
        count(*) AS payloads,
        sum((attributes ->> 'n_add_lessons')::int) AS lessons_added,
        sum((attributes ->> 'n_revisions')::int) AS lessons_revised,
        count(*) FILTER (WHERE attributes ->> 'new_unit' = 'true') AS with_new_unit
    FROM records
    WHERE span_name = 'proposal_shown'
),
applied AS (
    SELECT
        count(*) AS payloads,
        sum((attributes ->> 'n_add_lessons')::int) AS lessons_added,
        sum((attributes ->> 'n_revisions')::int) AS lessons_revised,
        count(*) FILTER (WHERE attributes ->> 'new_unit' = 'true') AS with_new_unit
    FROM records
    WHERE span_name = 'change_applied'
)
SELECT
    'proposed' AS scope,
    payloads,
    lessons_added,
    lessons_revised,
    with_new_unit,
    lessons_added::float / nullif(lessons_added + lessons_revised, 0) AS addition_share
FROM proposed
UNION ALL
SELECT
    'applied' AS scope,
    payloads,
    lessons_added,
    lessons_revised,
    with_new_unit,
    lessons_added::float / nullif(lessons_added + lessons_revised, 0) AS addition_share
FROM applied;
