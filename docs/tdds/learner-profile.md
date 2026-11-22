# TDD: About you and the Learning summary

Status: built behind `learning_summary` (off, on for admins). About you is not flagged.
Vocabulary: [CONTEXT.md](../CONTEXT.md) (*About you*, *Learning summary*, *Suggestion*).

## About you

- Storage: nullable `users.about_you` (migration `0023_learner_profile`), 2000-char cap.
- Prompts: `agents/_about_you.py` renders one delimited `<about_you>` block with the
  shared tag-forgery guard. Outline (after Guidance), lesson (before Teaching style,
  so the path's instruction is read later and wins), tutor and shaper contexts.
- Forward-only by construction: each generation reads the column when it runs.
- Briefs never read it (Constitution §9: anchored to Sources).

## Learning summary

- One `learning_summaries` row per account: `status`, `started_at` (fence), `body`
  (JSONB), `written_at`. `body` survives a failed or in-flight refresh.
- Trigger + poll (Constitution §4): `POST` claims with one upsert that only wins when
  no fresh run is generating, so concurrent triggers start one run; the run settles
  with a fenced update. Stale runs (`GENERATION_STALE_AFTER`) read as `failed` and are
  reclaimable. Runs share the generation registry and semaphore.
- Input (`build_summary_deps`, pure): per ready path its topic, level, progress, recent
  completed titles, next titles, and titles with a missed Quick check; Beat topics; About
  you. Never lesson or Brief bodies.
- Agent (`agents/learning_summary.py`): structured `areas` + `suggestions`; validator
  rejects suggestions that repeat an existing path or Beat, more than one Beat, or a
  Beat carrying path fields. Runs on `MODEL_DEFAULT`.
- Counted facts come from progress on every read, never from the model.
- Suggestions only prefill `/new` or `/beats/new` (Constitution §2).
- Quality: there is no eval harness on main, so suggestion quality is judged by hand.
