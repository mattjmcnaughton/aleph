# Account-wide Tutor — GitHub-ready tickets and wave execution

**Status:** Issues #291–#296 published; feature-branch execution authorized, 2026-10-23.
**Repository:** `mattjmcnaughton/aleph`.
**Scope authorities:** [PRD](../prds/learning-tutor.md) and
[TDD](../tdds/learning-tutor.md), accepted as the experiment's implementation baseline.

`LT-1` through `LT-6` are local planning IDs, not tracker IDs. Each ticket below
has a title and a body suitable for a GitHub issue. When publishing, include the
Common execution contract in each body, replace local dependencies with real issue
links, and fill the mapping below. Do not invent issue numbers or infer labels.

## Execution authorization (2026-10-23)

The owner requested issue creation and a new Amp thread to run `run-waves`, merging
ticket work into `mattjmcnaughton/tutor`, with that merge sufficient to close each
issue. The owner will review and merge the full integration branch later. Source:
[request and design discussion](https://ampcode.com/threads/T-01a16468-96d4-76f9-9a98-65f5c269c599).

- Finite scope: LT-1–LT-6 below. No automatic expansion to unrelated open issues.
- Initial integration base: current `origin/main` at
  [`e745658`](https://github.com/mattjmcnaughton/aleph/commit/e745658af94654f9be7dd0a24a09b4f5e330dfd0).
  The authoring checkout's local `main` was older; it is not the integration base.
- Delivery: per-ticket PRs targeting `mattjmcnaughton/tutor`; merge reviewed,
  verified candidates after required CI passes. Explicitly close the corresponding
  issue after its verified integration merge: GitHub does not automatically close
  issues from a PR merged into a non-default branch. Link the merge as evidence.
- Granted for this run: create/update the six issues and their dependency/progress
  comments, create/push integration and task branches, open/update ticket PRs,
  merge into `mattjmcnaughton/tutor`, and close integrated tickets. The orchestrator
  owns these external writes. Use at most two implementers and the waves below.
- First-ticket bootstrap: publish these specifications on the integration branch
  and add `mattjmcnaughton/tutor` to CI push/pull-request branch filters. Current CI
  filters exclude it. Keep release workflow restrictions to `main` unchanged.
  This small bootstrap may be pushed directly to establish CI before ticket PRs;
  run its applicable local checks. It does not by itself complete LT-1.
- Not granted: main merge, global/per-user live enablement, paid model runs,
  production/deployment/infrastructure/shared-data changes, unrelated tickets,
  or remote branch deletion. No automatic release or deploy from this branch.
- Keep skill-level commit-message and proof-capture approval gates. Do not invent
  answers to consequential new product questions; checkpoint and ask the owner.
- Record this grant and actual issue IDs in the execution thread's outer
  `.agentic/learning-tutor/plan.md` and `diary.md`; this file is not its diary.

## Common execution contract

- Implement only the assigned ticket against an explicitly recorded base containing
  its prerequisites. Read the PRD/TDD, Constitution and scoped AGENTS guidance.
  Resolve genuine requirement changes with the owner; do not expand the experiment.
- Use `/ship-ticket` for preparation, red-green implementation, review, resolution,
  evidence and authorized delivery. Its human approval gates remain in effect.
  “TDD” in the document title means technical design; red-green TDD remains the
  repository's separate testing requirement.
- Use the execution authorization above for this run, copied into the outer
  permission record. No further external writes are authorized by the issue body;
  in particular, feature-branch merges do not authorize a main merge or release.
- Keep `learning_tutor` off by default, including admins. Synthetic fixtures opt
  in. All new operations verify account ownership and flags server-side.
- Preserve the existing lesson Tutor, shaping, ordinary creation and analyst
  contracts. No Swift/native work. Schema/API/client changes stay consistent.
- Keep recovery in `.agentic/<task-slug>/plan.md` and `diary.md` per the skills.
  Record candidate revision, tests, review/evidence limitations, delivery state and
  blockers. Never call a checkpoint or offline eval a verified release.
- Use disposable test databases and fake model providers. Run targeted checks and
  `just gate`; database changes require database-backed tests. UI work requires
  rendered desktop/narrow inspection and a real interaction recording.

## Dependency graph and recommended waves

| Ticket | Title | Hard prerequisites | Recommended wave | GitHub issue |
| --- | --- | --- | --- | --- |
| LT-1 | Establish account-wide Tutor storage, state API and dark flag | Approved PRD/TDD | 1 | [#291](https://github.com/mattjmcnaughton/aleph/issues/291) |
| LT-2 | Generate grounded Tutor turns and learning recommendations | LT-1 | 2 | [#292](https://github.com/mattjmcnaughton/aleph/issues/292) |
| LT-3 | Accept recommendations through atomic path and Beat creation | LT-1, LT-2 | 3 | [#293](https://github.com/mattjmcnaughton/aleph/issues/293) |
| LT-4 | Connect Home, account-wide Tutor and editable creation flows | LT-1, LT-2, LT-3 | 4 | [#294](https://github.com/mattjmcnaughton/aleph/issues/294) |
| LT-5 | Add recommendation quality evaluation and follow-through queries | LT-2, LT-3 | 4 | [#295](https://github.com/mattjmcnaughton/aleph/issues/295) |
| LT-6 | Verify the integrated experiment and prepare opt-in runbook | LT-4, LT-5 | 5 | [#296](https://github.com/mattjmcnaughton/aleph/issues/296) |

Most work is intentionally serial: migration/contracts precede execution, and the
creation transaction must be settled before the UI depends on it. Wave 4 offers
useful parallelism: LT-4 owns frontend/E2E; LT-5 owns `evals/`, metric queries and
their docs. LT-2/3 own production telemetry emitters so LT-5 does not race them.
If evals discover a production defect, report it to the orchestrator; serialize
that fix instead of letting both workers edit shared files.

Dependencies mean implemented and verified behavior on the **exact integration
base**, not merely closed issues. LT-3 follows LT-2 even though some creation
refactoring could start earlier: both touch recommendation state/repository and
this sequencing avoids competing contracts. Do not add speculative parallelism.

## LT-1 — Establish account-wide Tutor storage, state API and dark flag

**Outcome:** An opted-in account can save its Learning focus, read/clear its own
Learning conversation state, and manage seeded recommendation state safely. No
model calls and no learner-facing Tutor UI yet.

**Depends on:** Approval of PRD §6 and TDD contracts; no implementation ticket.

**Scope / likely owners:**
- Bootstrap the specs and `mattjmcnaughton/tutor` CI push/PR filters as described in
  the authorization above. Preserve `main`-only release/deploy guards and verify
  CI runs on ticket PRs targeting the integration branch.
- Adopt proposed glossary terms in `docs/CONTEXT.md`, marked unshipped. Link specs
  from `docs/roadmap.md` without claiming delivery or changing other phase status.
- Add the three tables, constraints, relationships, and repository operations in
  TDD §3. Allocate one migration at the current Alembic head.
- Register `learning_tutor=False`, absent from admin defaults; wire authenticated
  router gate, account-scoped state/focus/history/clear/recommendation reads and
  decision mutations. Generation POST is introduced by LT-2, not a fake success.
- Define DTOs, state/epoch/revision semantics, effective staleness and pure
  recommendation transition/normalization rules. Establish receipt replay and
  locking seams LT-2/3 need. No changes to existing conversations/messages schema.
- Document the implemented API subset in `docs/api.md` and extend flag/schema
  fixtures. Do not document unimplemented endpoints as live.

**Acceptance criteria:**
1. With no override, learner and admin see the feature disabled; explicit opt-in
   permits the new API. Feature-off and cross-account identifiers return 404.
2. Empty state is valid without model work. Focus save/blank clear persists;
   conflicting revisions reject without losing the newer edit.
3. Seeded active/dismissed/accepted records enforce cap, duplicate, restore and
   stale rules. Clear redacts text, preserves focus and acceptance receipts, bumps
   epoch and prevents pending results from being considered current.
4. Database uniqueness/FKs cover account isolation and target deletion semantics;
   account deletion cascades. Existing path/lesson conversations are unchanged.
5. Upgrade from the current migration head succeeds on disposable PostgreSQL;
   state API contracts and existing flag tests pass.

**Verification:** `just gate`; targeted integration tests using
`tests/integration/conftest.py` fixtures for migrations, ownership, state CAS,
deletion and repository constraints. At least one two-session conflicting-write
test, not two calls serialized by a single test session.

**Out of scope:** Agent execution, recommendation acceptance, frontend presentation,
live enablement. Return the finalized API/schema contract to dependent workers.

## LT-2 — Generate grounded Tutor turns and learning recommendations

**Outcome:** An opted-in learner can explicitly request a Tutor turn or refreshed
recommendations, recover it after reload, and receive only durable validated output.

**Depends on:** LT-1 integrated and verified.

**Scope / likely owners:**
- `agents/learning_tutor.py`, `services/learning_tutor_context.py`, operation
  orchestration in `services/learning_tutor.py`, repository support and pure rules.
- `config.py` model slot/bounds/quota, `services/lifecycle.py` runtime wiring,
  `services/stub_model.py` deterministic outputs; no new scheduler or broker.
- Generation POST/poll endpoints and corresponding API documentation.
- Production event contracts/emitters for operation completion and recommendation
  interactions/decisions. If client interactions need a route, define its bounded
  authenticated telemetry input here; it must never perform a decision mutation.
- Synthetic seeds cover useful new path/Beat, existing-work advice, empty, invalid,
  provider failure, delayed completion and cold context.

**Acceptance criteria:**
1. Context includes only allowed owned metadata/focus/recent Learning Turns, with
   deterministic ordering and total bounds. Path title, lesson/Shaping chat, bodies,
   identity data and correctness are absent. Omitted counts are available to UI.
2. Cold refresh without context succeeds with `needs_context` and no paid admission.
   A typed interest can ground a conversation recommendation without saved focus.
3. Refresh publishes 0–3 cards atomically; a turn publishes a whole Turn and at most
   one card. A refinement only replaces the selected card; exact repeats, missing
   evidence, disabled Beat kind and unsolicited creation are rejected.
4. Receipt replay does not duplicate calls or Turns. Changed body/same key rejects.
   Separate database sessions cannot admit overlapping operations for one account.
   Quota is receipt-based and survives clear; validation failures do not consume it.
5. Timeout, provider failure, invalid output, process interruption, stale revision
   and flag-off are not empty successes. Expired or cleared work cannot publish;
   successful response-loss replay recovers the same result.
6. GETs never trigger model work. Failure preserves the prior recommendation set.
   Metrics/logging contain no prompt/reply/focus text, including agent instrumentation.

**Verification:** `just gate`; targeted PostgreSQL integration tests with fake
models and controlled delays/clocks, including clear/focus/flag races; agent
layering tests and context/output boundary tests. No live model run.

**Out of scope:** Existing creation transaction changes, production UI, quality
claims from live output. Document exact stub sentinels and event fields for LT-4/5.

## LT-3 — Accept recommendations through atomic path and Beat creation

**Outcome:** Final explicit creation from a recommendation creates one target and
one durable acceptance, even with retries, response loss or concurrent tabs.

**Depends on:** LT-1 and LT-2 integrated and verified.

**Scope / likely owners:**
- First preserve behavior while extracting narrow creation use cases from
  `routers/v1/paths.py`, `routers/v1/beats.py` and
  `services/generation.py::create_path` into transaction-owned services. Verify
  existing tests before adding behavior; keep the stages separately reviewable.
- Extend creation DTOs with the optional paired recommendation ID/revision fields.
  Keep existing response shapes and model-override policy. Ordinary callers still
  use the same creation flow with no recommendation fields.
- Implement TDD §8 lock order, cap serialization across both entry points, edited
  payload hashing, accepted replay, target-deleted result and post-commit dispatch.
- Minimal target-owned recovery if commit-before-dispatch can leave a target
  unrecoverable. Reuse generation/research claims rather than re-create targets.
- Add server acceptance telemetry, API docs and integration fixtures.

**Acceptance criteria:**
1. Ordinary creation still honors Topic/Level/Guidance/Teaching style, admin-only
   overrides, rate/stock limits, analyst flag and original response shapes.
2. Recommendation creation validates ownership, feature flags, kind, staleness and
   revision. Chat, Explore, and Discuss alone cannot create anything.
3. Two simultaneous accepts create exactly one target; same edited-payload replay
   returns its ID without another dispatch/quota charge. Different edited payload
   conflicts. Receipt survives clear; deleted target returns 410, not re-creation.
4. Forced failure between insertion and receipt leaves neither committed. Failure
   after commit exposes the existing target and a recovery path, not a misleading
   failed-creation response or a second target.
5. Normal creation racing recommendation creation at a cap cannot exceed that cap.
   Tests use independently committed PostgreSQL sessions and real constraints.
6. Accepted cards disappear from active reads on both surfaces; source/target
   deletion and mid-flow flag disablement cannot bypass acceptance checks.

**Verification:** `just gate`; existing `test_paths_api.py`, `test_beats_api.py` plus
recommendation-acceptance integration tests; concurrency, rollback, lost-response
and spawn-failure cases. Broader integration run after the creation refactor.

**Out of scope:** General request idempotency for all ordinary creation, changing
quota values, paid research, unrelated route/service cleanup, new undo behavior.

## LT-4 — Connect Home, account-wide Tutor and editable creation flows

**Outcome:** Learners can use the complete flagged experiment on desktop and narrow
web layouts, using the existing app rather than the prototype's placeholders.

**Depends on:** LT-1, LT-2 and LT-3 integrated and verified.

**Scope / likely owners:** Frontend `/tutor` route, gated navigation, Home section,
shared query/API hooks, recommendation cards, focus editor, clear confirmation,
dismissed list/Undo, and creation form recommendation seeding. Own frontend mocks,
client tests and new Playwright journeys. Use LT-2's stub backend contracts; if a
backend change is needed, coordinate ownership rather than changing its contract
silently. Do not write `evals/` or shared metrics documentation owned by LT-5.

**Acceptance criteria:**
1. Flag-off preserves existing Home/navigation/creation and direct `/tutor` entry is
   unavailable. Analyst-off hides Beat context/cards/actions. No model call on visit.
2. Home shows current work before shared cards. Initial, loading, no-context, valid
   empty, all-dismissed, stale, rate-limited and failed-refresh states differ.
3. Discuss opens the same recommendation in the Learning conversation; whole reply
   and card publication agree. Operation reload/retry and conflicts preserve typed
   input; request IDs are reused only for recovery of the same request.
4. Focus save/clear, clear confirmation, dismissal/Undo/later restore work across
   navigation/refetch. Account switching cannot expose the prior account's cache.
5. Explore fetches an owned draft and seeds existing onboarding once. User edits
   survive refetch. Cancel creates nothing; explicit submit sends the edited fields
   and receipt identity. Stale/unowned drafts never silently fall back to a blank
   normal-creation form; accepted cards do not return.
6. Keyboard controls, focus handling, safe Markdown and mobile-first layout pass
   DOM/browser checks. Record a real suggestion → discussion → edited Beat
   creation flow, and verify a path flow and flag-off regression independently.

**Verification:** `just gate`; targeted frontend tests and Playwright with the stub
backend; inspected default and non-default screenshots at desktop and 390px, plus
an interaction recording. Allocate workflow IDs against the current catalog.

**Out of scope:** New library/reader redesign, native client, live model calls,
automatic refresh, embeddings, or production feature overrides.

## LT-5 — Add recommendation quality evaluation and follow-through queries

**Outcome:** The experiment can distinguish technically valid output from useful
recommendations and can measure learning after acceptance without logging content.

**Depends on:** LT-2 and LT-3 integrated and verified. May run beside LT-4.

**Scope / likely owners:** `evals/` fixtures/CLI/rubric integration, eval tests,
`queries/` metric queries, `docs/evals.md`, `docs/metrics.md`. Production agent,
event emitters and schema remain owned by earlier tickets; report gaps to the
orchestrator and serialize fixes. Do not expand production behavior to fit evals.

**Acceptance criteria:**
1. At least 20 curated synthetic cases exercise PRD §5/TDD §10, with explicit
   independently derived expected evidence/forbidden behavior and useful
   counterexamples. Cover empty/no-history and continue-existing-work outputs.
2. Offline smoke runs the actual agent output predicates and harness without
   provider/retrieval access. An intentional invalid evidence ID, over-cap card set,
   disabled Beat recommendation and duplicate Topic fail the appropriate check.
3. Human-quality rubric distinguishes grounded reason, utility, novelty, sensitive
   inference and safety. Live-run threshold and calibration limitations are
   documented; no claim of live quality or judge agreement from stub smoke.
4. Queries compute accepted → seven-day lesson completion / fourteen-day Brief
   read, exclude immature windows, deduplicate retries, and explicitly report
   deleted-target/unknown outcomes. Use synthetic data to check numerator and
   denominator, including a boundary case that changes cohort inclusion.
5. Operation failures, usage and interaction counts are queryable. Sensitive text
   is absent from events. Document how to obtain/reconcile durable acceptance
   counts when best-effort event delivery is incomplete.

**Verification:** `just gate`; eval unit tests; newly documented offline smoke
command; synthetic query checks. Live evals, retrieval recordings and production
queries involving private data are not authorized by this ticket.

**Out of scope:** Flag flip, paid evaluations, automatic quality enforcement on
every production reply, optimizing to a fabricated conversion target.

## LT-6 — Verify the integrated experiment and prepare opt-in runbook

**Outcome:** A reviewed integrated candidate and an honest record of what remains
before anyone enables the experiment. This ticket does not turn the flag on.

**Depends on:** LT-4 and LT-5, including LT-1–3 behavior, on the exact candidate.

**Scope:** Combined acceptance sweep, security/deployment-risk review, bounded
fixes for confirmed findings, API/doc reconciliation, and an opt-in/disable
runbook in `docs/deploy.md`. Re-run affected evidence after fixes; do not lower
acceptance criteria to declare success.

**Acceptance criteria:**
1. `just gate`, `just test-integration`, `just test-e2e` and the new offline eval
   smoke pass on the integrated candidate; required CI must later pass on any PR
   head, not merely on isolated workers' branches.
2. PRD P1–P6 map to concrete tests/captures. Concurrent acceptance, clear fencing,
   missing evidence, deleted targets, flag bypass and ordinary creation regressions
   have evidence appropriate to their database/browser boundaries.
3. Review disposition records all findings and blockers. Any live-agent quality
   gate not run remains explicitly UNPROVEN and blocks wider enablement, not local
   completion of a dark candidate.
4. Runbook names explicit per-user opt-in, model settings/limits, quota and failure
   observation, the two-week dogfood review, and disabling user-on overrides as
   well as global defaults. No secret values or real learner content in evidence.
5. Rollback preserves data; local migration upgrade checks pass; no shared database
   migration, deployment, global default flip or live spend occurs as a side effect.

**Verification:** Combined commands above, documented reviewed candidate revision,
inspected captures/recording, and an explicit outstanding-approval/quality list.
No implementation change needed merely to produce a final commit if all evidence
already exists; retain the report in the task diary and user-facing delivery.

**Out of scope:** Production launch, week-long monitoring, changing infrastructure,
unrelated Home Screen acceptance, claiming paid or real-user tests ran when absent.

## Publication and execution handoff

Publication is authorized by the execution amendment above: create the six issues,
copy the common contract and current grant into each, replace dependencies with
real links, and update the mapping. No additional tracking issue is required.
Read existing issues first to avoid duplicates; resolve uncertain writes before
retrying. Do not auto-close issues merely because code exists locally.

Before `/run-wave` or `/run-waves`, record:

1. Accepted spec revision and finite issue list (LT-1–6 mapped to real IDs).
2. Exact integration base/revision and chosen PR target; ensure the spec files are
   present there or explicitly transfer unpushed files to workers.
3. Delivery target (merged to `mattjmcnaughton/tutor`), publication unit
   (per-ticket PRs), and the execution amendment's external-write grants. Account
   for release automation; a feature flag is not deployment permission.
4. Worker limit (recommend at most two; only Wave 4 is parallel), time/ticket/wave
   budget if any, orchestrator ownership and recovery location.
5. Isolated worktrees, test databases, ports, browser sessions and artifact paths.
   The orchestrator owns publication by default; workers return candidates.

Authorized invocation: “Run the finite six-issue Learning Tutor set using
run-waves, following this execution guide and the recorded permission grant.
Recheck dependencies on each integrated base; stop at unresolved requirements,
failed integration, or limits. Do not enable the feature.”
