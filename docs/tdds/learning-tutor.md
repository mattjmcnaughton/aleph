# TDD — Account-wide Tutor and learning recommendations

**Status:** Accepted implementation baseline for the feature-branch experiment,
2026-10-23; implemented on the integration branch, default off and not approved
for release. Live quality/calibration and production migration lock safety remain
UNPROVEN; see the [runbook](../deploy.md#account-wide-tutor-opt-in-and-disable-runbook-lt6).
**Product:** [PRD](../prds/learning-tutor.md).
**Execution:** [ticket drafts](../plans/learning-tutor-tickets.md).
Read [CONSTITUTION.md](../../CONSTITUTION.md), [CONTEXT.md](../CONTEXT.md), and
[architecture.md](../architecture.md) before implementation. Proposed module and
field names below are deliberate seams, not claims that those files exist.

## 1. Existing contracts and design decisions

The table records pre-implementation contracts and the intended changes, not a
description of today's integrated code. Future workers must recheck their exact
base. The implemented API is documented in [api.md](../api.md#account-wide-tutor-state-lt-1-unshipped).

| Existing contract | Consequence for this feature |
| --- | --- |
| `models/conversation.py`, `repositories/conversations.py`: non-null path ownership, exact lesson/shaping scope constraints and ownership joins | Do not widen these tables or overload lesson Message DTOs. Add account-owned storage. |
| `services/tutor.py` / `shaping.py`: request-scoped SSE, whole-turn settlement, process-local reservations; clear can race settlement | Reuse agent/context conventions, not their concurrency guarantees. New work needs durable admission and clear fencing. |
| `services/generation.py::create_path`: owns a session, commits, then spawns | Cannot call this inside a recommendation transaction and claim atomic acceptance. Separate transaction-owned insertion from post-commit dispatch. |
| `routers/v1/beats.py::deploy_beat`: performs limit checks and insertion, commits, then triggers initial research | Move this use case into a service shared by ordinary and recommendation-based creation, without changing its response semantics. |
| `services/feature_flags.py`: user override > settings > admin baseline > code default | Register a separate default-off flag, deliberately absent from admin defaults. A settings-off value does not defeat an explicit user-on override. |
| `services/rate_limit.py`: path daily and Beat stock checks are count-before-create | Serialize admissions for both ordinary and recommendation-driven creation on the same account lock when sharing these limits. Do not advertise current checks as concurrency-safe. |
| `routes/new.tsx` / `beats.new.tsx`: editable `?topic=` seed only | Add an owned recommendation-ID seed, not model JSON or private Guidance in URLs. Keep ordinary topic seeding working. |
| `lib/polling.ts`, `services/lifecycle.py`, `repositories/_generation.py` | Reuse trigger/poll, task registration, timeout and database-clock patterns; no new job queue or scheduler. |

**Decisions:** one dedicated no-tool agent; bounded metadata context; three
account-owned tables; whole structured output through trigger + poll; explicit
refresh; transactional acceptance via existing creation routes. Existing lesson
Tutor, shaping, generation and analyst behavior remain outside this feature except
for the narrowly required shared creation transaction boundary.

Alternatives rejected for this slice: extending path conversations (changes their
ownership model); streaming a new card protocol (provisional/durable complexity);
browser-only dismissal or acceptance bookkeeping (lost updates and duplicate
creation); embeddings or inferred profiles (no demonstrated need); background
refresh (cost and scheduling before product value is known).

## 2. Ownership and module boundaries

```text
learning_tutor router -> LearningTutorService -> context assembler + agent
                                           -> learning_tutor repository
paths/beats routers -> creation services -> ordinary target repositories
                                       -> recommendation receipt, when supplied
```

- `agents/learning_tutor.py`: immutable deps, structured output, prompt, pure
  output predicates. No tools, bound model, config, ORM, or I/O.
- `domains/learning_recommendations.py`: kind/status transitions, exact-topic
  normalization, card-cap rules and draft validity. No I/O.
- `services/learning_tutor_context.py`: scoped SQL reads and bounded context
  assembly; no calls to routes, generation-triggering APIs, or other agents.
- `services/learning_tutor.py`: authorization, flags, admission, execution,
  settlement, focus/clear/dismiss/restore use cases.
- `repositories/learning_tutor.py`, `models/learning_tutor.py`: queries, locking,
  receipts and schema. Keep each file cohesive; no generic conversation framework.
- `dtos/learning_tutor.py`, `routers/v1/learning_tutor.py`: API boundary.
- Creation services own new target writes and acceptance in the same transaction;
  the Tutor agent/service never creates a target while generating text.

Share existing creation validators at their owning service/domain boundary instead
of importing HTTP handlers into services. Preserve existing exception/envelope
behavior with router translation. Do not use this work to reorganize other routes.

## 3. Persistence and invariants

One additive Alembic revision, allocated against the implementation base, creates:

### `learning_tutor_states`

One row per `user_id` (primary key, users FK with cascade), optional `focus`, integer
`revision`, integer `focus_version`, integer `epoch`, and
`last_refresh_operation_id` (nullable, SET NULL on operation deletion). Lazy-create
on first write. `revision` increments on focus edits, completed generation,
dismiss/restore, acceptance and clear. Every mutating client request carries the
last observed revision. Lock this row for changes; mismatch is `409 stale_state`.
`focus_version` increments only when focus changes; recommendation staleness uses
that version rather than treating every unrelated state change as a focus change.
`epoch` increments on clear and prevents retries from old conversations reviving
cleared content. GET on a new account returns the empty state without model work.

### `learning_tutor_operations`

UUID `id`, owning user FK, `epoch`, request UUID, canonical request hash, kind
(`turn`/`refresh`), captured state revision, status (`running`/`succeeded`/`failed`),
start/expiry/finish timestamps, safe error code, nullable input/output JSON, usage
counts, `model_admitted` boolean, and `cleared_at`. A turn's input and successful reply form one Turn;
refresh operations never appear as learner chat messages.

- Unique `(user_id, epoch, request_id)` provides request replay identity.
- Partial unique index on `user_id WHERE status = 'running'` provides one active
  operation across replicas; the state row lock serializes admission and expiry.
- JSON payload shape is validated before persistence; successful turn output and
  its recommendations publish in one transaction, never separate writes.
- A failed operation may retain its input for explicit retry but is not a
  successful conversation Turn. UI displays its failure separately.
- Clear nulls input/output and sensitive error content, marks running operations
  failed, and clears conversation-visible history. Minimal IDs/hash/timestamps/
  usage remain as quota receipts; cleared receipts older than 30 days can be
  pruned during the next admission. No cleanup scheduler is introduced.

### `learning_recommendations`

UUID, user FK, nullable originating operation FK (SET NULL on receipt pruning),
epoch, captured focus version, kind (`path`/`beat`), Topic, Level,
Guidance, description, rationale, evidence references, normalized topic key,
status (`active`/`dismissed`/`superseded`/`accepted`), timestamps, nullable accepted
path FK and Beat FK (`ON DELETE SET NULL`), and accepted request hash.

- CHECK kind/target consistency and never both target FKs; `accepted` remains
  accepted when deletion nulls its target. Stored acceptance time distinguishes
  it from a never-created recommendation.
- Partial unique `(user_id, kind, normalized_topic) WHERE status = 'active'` is a
  database backstop for exact active duplicates. At most three active records is
  enforced under the state lock, not just counted in a browser.
  Before inserting/restoring, supersede effectively stale active rows under that
  lock so hidden rows do not consume capacity or block the unique index. Restore
  also checks existing target Topics and active duplicates.
- Normalize by Unicode NFKC, casefold, trim and whitespace collapse. Apply the same
  rule to existing target Topics and all still-retained dismissals; kind is part
  of the key, so learning a Topic and following that Topic are distinct.
- Refresh supersedes active rows and inserts the new set in one transaction;
  dismissed and accepted receipts are not replaced. Refinement supersedes only the
  explicitly supplied active recommendation and inserts its replacement atomically.
- Evidence references name owned path/Beat/Brief IDs or focus/current-message
  evidence from the operation, plus only the bounded factual values used by the
  card (for example, a completion count and observation time). Never store full
  Read passages, Briefs, or whole-context snapshots here. A missing source or changed focus makes an unaccepted card
  effectively stale; reads omit it, writes reject it. GET need not mutate rows.
- Clear deletes unaccepted recommendations and redacts accepted recommendation
  text/evidence, preserving only acceptance identity/kind/target/hash/timestamps.
  Make payload columns nullable for redacted accepted receipts, with a CHECK
  requiring complete payloads for all unaccepted rows; clear normalized Topic too.
  No active card is reconstructed from an operation's old output after clear.
- All rows cascade on account deletion. Path/Beat deletion must not cascade into
  an unrelated Learning conversation. Historical conversation text is retained
  until the learner clears it, as disclosed in the PRD.

Store displayable rationale separately from safe event metadata. Do not put raw
focus, messages, generated replies, or recommendation text in logs/traces by
default, including automatic agent instrumentation.

## 4. Context is bounded evidence, not a learner dossier

Assemble a deterministic account-scoped snapshot from live database records:

| Input | Included | Initial bound / ordering |
| --- | --- | --- |
| Learning focus | Current explicit text and revision | 2,000 characters |
| Paths | Original Topic, Level, Guidance excerpt; completion counts, latest completion time, next available lesson ID/name | Up to 12, most recent completion then creation, ID tie-break |
| Beats | Topic, Level, Guidance excerpt, read/published counts, latest read time | Up to 8, latest Brief read then creation, ID tie-break; only if analyst enabled |
| Brief activity | IDs, titles, publication/read timestamps, owning Beat | Latest 2 per selected Beat; metadata only |
| Learning conversation | Whole successful Turns from current epoch | Most recent 10; oldest dropped first |
| Recommendations | Active cards, recent dismissals and selected discussion card | 3 active + 20 recent dismissals; complete exact-dismissal set checked in SQL |
| Current input | Learner message and optional discussed recommendation | 2,000 characters; recommendation must be owned and live |

Guidance excerpts cap at 500 characters, lesson/Brief names at 200. Entire dynamic
input (including prior Turn text and recommendation payloads) caps at 40,000
characters, excluding a fixed prompt whose size is also tested. Reserve space for
focus, current message and selected card, then recent Turns, then activity and
dismissal excerpts. Drop whole records/Turns, not half JSON or half a conversation.
Report omitted counts in deps and UI scope disclosure. No claim of exhaustive
account knowledge. If required fields cannot fit, fail validation rather than
silently losing the current request. Apply bounds before model serialization.

**Never include Path title in agent deps.** It is explicitly display-only in
CONTEXT.md. UI link labels may resolve it after generation, outside the prompt.
Also exclude account name/email, Quick check correctness, all lesson/Shaping
messages, bodies, source text, and external browsing. Topic/Guidance are untrusted
data, not instructions; current explicit focus overrides inferred interest.

Each included fact has a server-generated evidence key. Agent rationale references
must be members of the supplied set. Render factual support (“6 lessons complete”)
from the server's fact values, not a model-invented number. The agent may explain
why that evidence is relevant; evals assess whether that inference is reasonable.
Existing-work actions use a separate allowlisted set of current owned targets.
Recheck ownership/existence at publication and click/creation, and reject stale
source references. Activity changing naturally does not invalidate a saved card's
historical fact; show its generation date. Source deletion and focus changes do.

For a cold refresh with no focus/activity, return a successful `needs_context`
receipt with `model_admitted=false`, without a model call or quota charge. This
keeps request replay and polling shape unchanged; already completed receipts
return HTTP 200, whereas newly admitted running work returns 202. In a conversation, the learner's
current expressed interest can itself be grounding evidence; do not demand a goal.

## 5. One structured agent, two operation kinds

Reuse the model-resolution seam with a new `model_learning_tutor` slot registered
in `MODEL_SLOTS` and the production stub guard. Initial configured model may match
`model_tutor`; no per-message admin override UI is required. Factory takes no model.

Output is a validated object:

```text
reply: bounded Markdown (turn only, nonempty, <= 6,000 chars)
existing_work: 0..2 {kind, supplied_target_key}
recommendations: 0..3 draft objects for refresh; 0..1 for a turn unless requested_pair
  {kind, topic, level, guidance?, description, rationale, evidence_keys[]}
replacement: optional discussed recommendation ID (turn only)
requested_pair: boolean, default false (turn only; no replacement)
```

**Owner amendment, 2026-10-24:** Normal chat stays the default. Optional discovery
uses ordinary turn content and bounded conversation history, without a new API
input, tools, inferred-profile storage, or persisted interview mode. The prompt
asks one short question at a time, skips already supplied answers and honors an
exit to ordinary chat. Explicit novelty requests use activity as contrast rather
than forcing similarity; current requests override saved focus without changing it.

`requested_pair` records the model's interpretation of a connected path+Beat
request, including blocked/clarifying replies. A declared pair has exactly one
draft of each kind or no drafts; two cards without that declaration are invalid.
Both drafts independently pass the existing predicates and fit two available
slots within the unchanged three-active-card cap. No silent eviction or combining
a pair with single-card refinement. If capacity or analyst availability prevents
the pair, instructions require a no-card reply explaining how to free slots or
asking whether the learner wants one kind instead. Semantic request recognition,
connection, novelty and actionable prose need quality evaluation: deterministic
validation cannot prove the model correctly set the declaration or understood
the learner. Scripted offline cases do not prove live adherence.

Pair publication uses the existing whole-operation savepoint: a late collision
on either draft rolls back both. The cards remain independently editable,
dismissible and explicitly accepted through existing creation flows; no joint
enrollment action or persistent pair relationship is introduced. The additive
output marker is internal model/receipt metadata; older receipts lack it and
clients consume their existing reply/card arrays without a new request shape.

Reuse Topic/Level/Guidance constraints without importing router/DTO dependencies
into agents. Share pure predicates rather than repeating divergent constraints.
Description <= 300 chars, rationale <= 600, 1..3 evidence keys per draft. No model
IDs, user IDs, target creation IDs, Anchor day, Teaching style, or arbitrary URLs
in model output. Existing-work links are rendered from validated keys.

Validators enforce bounds, known evidence/targets, analyst availability, valid
replacement identity, available card slots and exact-topic exclusion. Invalid
output permits one model retry, then a visible `invalid_output` failure. A valid
empty refresh is success. Safety refusal is a safe conversational reply with no
recommendations, not a fabricated provider error. A turn at card capacity can
still answer without a recommendation.

The agent neither mutates focus nor invokes creation/shaping/research tools.
Rendering uses existing `components/markdown.tsx`; no new raw-HTML renderer.

## 6. Durable trigger + poll lifecycle

POST admission authenticates, checks flags, request shape, revision/epoch, limits,
and ownership before starting model work. Lock the state row, expire any overdue
running operation, and insert a receipt with a unique client request UUID. Same
UUID and same canonical body returns the same operation; different body is
`409 request_reused`. Resolve an existing receipt before rejecting its old revision
so replay after success is safe, but reject an old epoch after clear before any
replay of its content. A different concurrent request gets
`409 operation_in_progress` with the owned operation ID.

Commit admission, then spawn through the existing lifecycle `TaskRegistry`. Return
`202` with operation ID/status, never “reply completed.” GET does not spawn work.
Execution uses a separate semaphore (initially 2), does not hold a database
connection while calling the provider, and has a 90-second total deadline including
permit wait. Operation lease expires after 120 seconds, using the database clock.

Before settlement, lock state and receipt and recheck: running status, unexpired
lease, epoch, captured revision, current flags, and source ownership/existence.
Publish the Turn/cards and `succeeded` together and increment revision. A focus
edit, clear, acceptance or dismissal that happened during execution wins: the old
run fails with `context_changed`, never restores superseded choices. Cancel its
local task if possible, but database checks are the guarantee across processes.

Provider/validation/timeout/dispatch errors set safe failed outcomes. A process
death can leave a running row; after expiry GET presents `failed/interrupted`, and
the next admission materializes that failure under lock. A late worker cannot
settle an expired operation. No automatic replay of billed work. Explicit retry
uses a new request UUID and current revision; repeating the old UUID returns its
old result. Polling/reloading recovers success after a lost HTTP response.

Initial quota: 30 admitted operations/account/UTC day, combining turns and refreshes,
counted from `model_admitted=true` receipts under the account state lock. Failed admitted attempts count;
validation/cold-start/duplicate replay does not. Clear does not refund quota.
Use the existing admin-exemption convention, but every run still has timeout,
context/output bounds, and permit limits. Configure the daily cap; document that
character bounds are not a dollar ceiling. Register usage tokens and duration.

Flag-off at settlement prevents publication; it cannot retroactively refund or
guarantee cancellation of an already dispatched provider request. Explain this in
the operational runbook rather than promising zero in-flight cost.

## 7. API contract

Prefix `/api/v1/learning-tutor`, router-level `learning_tutor` flag gate. Use
existing auth, safe error envelopes, and 404 ownership concealment. All GETs are
read-only. Paginate history/dismissals rather than returning unbounded JSON.

| Method and suffix | Request / behavior |
| --- | --- |
| GET `/` | Focus, revision, epoch, active cards, latest refresh outcome/time, current operation ID, latest 20 Turns + older cursor |
| PUT `/focus` | `{focus: string|null, revision}`; normalize blank to null; invalidate active cards and stale in-flight results; return state |
| DELETE `/conversation?revision=…` | Clear confirmation action; increment epoch/revision, redact payloads, preserve focus/created targets; return state |
| GET `/history?before=…` | Older whole Turns, up to 20, current epoch only |
| POST `/operations` | `{request_id, revision, epoch, kind, content?, recommendation_id?}`; content required for turn, forbidden for refresh; 202 running or 200 terminal receipt |
| GET `/operations/{id}` | Owned running/succeeded/failed receipt, safe failure reason; payload omitted if cleared |
| GET `/recommendations?status=dismissed&before=…` | Up to 20 owned dismissed cards, cursor |
| GET `/recommendations/{id}` | Validated owned draft for onboarding; include effective state and target availability |
| PUT `/recommendations/{id}/decision` | `{decision: dismiss|restore, revision}`; locked transitions, return state |

Existing POST `/paths` and POST `/beats` gain optional
`learning_recommendation_id` and `learning_tutor_revision`; require both or neither.
Keep their existing edited creation payloads and responses: path `202 {id}` and
Beat `202 BeatDetailDTO`. This is final acceptance, not a new chat action. Missing
recommendation fields retain ordinary behavior. Supplying them enforces the new
flag as well as all existing authorization/analyst gates. Extra arbitrary fields
must not gain authority.

Errors: `404` unavailable/unowned; `422` invalid input; `409 stale_state`,
`recommendation_unavailable`, `recommendation_capacity`, `operation_in_progress`,
`request_reused`; `410 accepted_target_deleted` for an already accepted target now
gone; existing `429` with retry guidance. Operation failures are explicit status
payloads (`provider_failed`, `invalid_output`, `timed_out`, `interrupted`,
`context_changed`, `feature_disabled`), not successful-empty recommendations.

Update `docs/api.md`, frontend API types, MSW fixtures, and any schema artifact
actually present on the implementation base together. Do not invent a generated
client or touch native scaffolding absent from the active web contract.

## 8. Acceptance is one target transaction

Extract narrowly scoped creation services first, preserving behavior and verifying
existing tests before adding recommendation acceptance. Path insertion must use
the caller-owned transaction; post-commit dispatch remains in generation's owner.
Beat insertion/authorization/limit orchestration leaves the HTTP handler. Ordinary
and recommendation creation use these same services and validations.

For a recommendation acceptance:

1. Authenticate, parse edited fields and validate model overrides using existing
   policy. Verify `learning_tutor` and, for a Beat, `analyst`.
2. Lock the account's users row (shared by ordinary creation for cap serialization),
   then Tutor state, then recommendation, in that order everywhere.
3. If already accepted, compare the canonical accepted payload hash: same payload
   returns the original target without quota/spawn; changed payload is conflict.
   If target was deleted return 410. Check this before revision/cap tests so a lost
   success acknowledgement can be replayed. Clearing must retain this receipt.
   Hash normalized creation fields (including explicit model overrides), excluding
   transient Tutor revision and request time-zone offset; never store a second
   copy of private creation text solely for replay comparison.
4. Otherwise require current revision, active/nonstale owned recommendation and
   matching kind. Validate ordinary path/Beat caps under the account lock. Learner
   edits are authoritative and need not match the original draft's Topic/Guidance.
5. Insert the target and mark accepted with target FK/hash/timestamp; increment
   state revision and commit once. Any failure rolls back both.
6. Only after commit emit creation/acceptance telemetry and invoke existing
   idempotent outline/initial-research dispatch. No network/provider calls in the
   transaction. Return the target even if dispatch fails; expose retryable target
   generation failure rather than reporting that committed creation failed.

The row lock plus persistent accepted state enforces one target per recommendation
across replicas. It is not a promise that ordinary creation is newly idempotent.
Share cap locks for both entry points so recommendation acceptance cannot bypass
limits by racing ordinary creation. Keep existing quota values/deletion semantics.
Account locking is limited to these admissions, not a general lock for all APIs.

Crash between commit and dispatch must leave a recoverable target: use existing
path reconciliation and Beat retry/job mechanisms, testing the actual base's
behavior. If a created Beat has no run and the current recovery cannot pick it up,
LT-3 must add the smallest target-owned recovery change before acceptance passes.
Never “repair” dispatch by re-creating the target or clearing its receipt.

## 9. Frontend behavior

Add `/tutor` and a gated shell entry; extend Home below existing learning work.
Reuse Nocturne tokens, responsive CSS layout, Markdown, pending/error components,
query invalidation and `makePollingRefetchInterval`. Do not change the lesson rail.
Home and Tutor share one account-scoped state/query source; clear queries on logout
or account change, and revalidate on focus before privileged actions.

Pass `?recommendation=<uuid>` to `/new` or `/beats/new`. Fetch the owned draft before
initializing form state; seed once and never overwrite learner edits on refetch.
Show a loading/error boundary rather than silently using blank/default fields if
the seed fails. If both `topic` and `recommendation` are present, recommendation
wins; invalid recommendation never falls back to the topic. Preserve cancel,
ordinary onboarding, Teaching style, admin overrides and target polling behavior.

Retain request UUID while an operation request's result is uncertain. Disable
repeat submit while polling; recover the operation from GET after reload. After
409, reload state and explain the changed card/focus without discarding typed text.
Do not automatically resend a failed model operation or a changed creation body.
Use success receipts, not optimistic hiding alone, to remove accepted cards.

Accessible waiting announcements, keyboard actions, focus restoration after
dismiss/Undo, labels, error text and narrow-screen layout are required. Never label
metadata-only context as “I read your Briefs.” Show sampled context and generation
time. Empty, initial and failed are separate states. Clear requires an explicit
confirmation naming what survives. Explain when focus edits invalidate cards.

**Owner UI scope amendment (2026-10-24):** do not render a dismissed-recommendations
list or later restore controls. Keep immediate Undo, persistent dismissal and the
existing history/restore API. Reuse the Tutor thinking-dot animation while pending;
decorative dots are hidden from assistive technology and stop for reduced motion,
while status/recovery text stays available. Suppress the obsolete refresh-failure
warning when a visible card's `created_at` is later than that failed refresh's
`finished_at`; equal/earlier timestamps or no newer visible cards retain the warning.
This uses the existing wire contract, not browser-local timing or a history scan.

## 10. Telemetry and quality evidence

Extend typed `events.py` fields: operation completed (kind/outcome/duration/tokens),
recommendation interacted (shown/explore/discuss), decision (dismiss/restore), and
accepted (recommendation ID, target kind/ID). Emit server decisions after commit;
client display/explore events are observational and never authority. Deduplicate
display per recommendation/surface/session. Transactional accepted rows, not
best-effort events, are authoritative for acceptance counts.

Add saved queries under `queries/logfire/` or the existing metric query convention
and document in `docs/metrics.md`: mature seven-day path completion and fourteen-day
Beat-read follow-through, deduplicated acceptance, operation failures and usage.
State how deleted targets and immature observation windows are counted. Cleared
receipts keep attribution IDs but not personal text. Do not count retries as extra
acceptances; model admissions and successful operations are separate denominators.

Use an opt-in `learning_tutor` eval mode integrated with `evals/` conventions;
proposed CLI `just evals --learning-tutor --smoke`, documented only when implemented.
Synthetic fixtures cover: cold start, explicit focus, competing interests,
continuation preferable to creation, no suitable recommendation, dismissed
paraphrases, existing exact topics, stale/deleted sources, analyst disabled,
malicious stored text, sensitive inference, and unsupported mastery claims.

Deterministic predicates must all pass. Proposed quality gate for an authorized
live run: at least 20 curated cases, >=90% case pass rate, zero safety/privacy or
fabricated-evidence violations, human inspection of every failure. Calibrate
labels before claiming judge agreement. Offline stub success proves plumbing, not
recommendation quality. Paid runs and flag enablement are separate approvals.

## 11. Verification and constitution mapping

Red-green TDD per repository policy; assert public outcomes rather than internal
call order. Extend existing fixtures. Keep fake agents/provider boundaries; never
need real learner records for tests.

| Guarantee | Enforcement and falsifying evidence |
| --- | --- |
| §§1–2: authorization, models only propose | Ownership/flag checks at reads and acceptance; unowned IDs and forged model target IDs fail; chat alone creates nothing |
| §3: truthful history, atomicity | Whole Turn/card transaction; two-session concurrent accept produces one target; injected failure between target/receipt rolls back; response-loss replay returns same ID |
| §4: outcomes and recovery | Forced invalid output, provider failure, killed worker, stale lease, refresh failure and valid empty return different states; expired worker cannot publish |
| §§5–7: domain and boundaries | Adopt vocabulary; pure predicates and existing agent layering tests; no path-thread schema expansion or service-to-router imports |
| §8: active client | Web API/UI land together; flag-off regressions; no native work |
| §9: untrusted output/quality | Evidence membership and bounds, unsafe Markdown regression, no automatic sensitive logging; separate quality eval evidence |
| §10: guarantee-specific tests | PostgreSQL concurrent state/receipt/clear tests; browser creation journeys; deterministic smoke distinct from live quality |

Also test: simultaneous normal/recommendation creation at the cap; focus save and
clear against delayed completion; same request UUID/different body; successful
operation replay after revision advances; retained quota after clear; missing
source on restore/accept; account deletion cascade; accepted-target deletion;
analyst disabled before and during work; exact-topic normalization with asymmetric
case/spacing/Unicode inputs; max context/output limits on both sides of boundaries.

Commands: `just gate`, `just test-integration`, `just test-e2e`, plus offline eval
smoke once its CLI exists. Run database tests against disposable databases. Use
targeted tests per ticket, then combined suites on the exact integration candidate.
Inspect desktop and narrow browser captures for Home, Tutor, editing, failed/empty
and stale states; record recommendation → discussion → edited creation. Allocate
new workflow IDs from the current catalog when implemented; do not guess IDs now.

## 12. Rollout, rollback and open approval

Add `learning_tutor=False`, not in `ADMIN_DEFAULT_FLAGS`. Tests/stubs opt in
explicitly. No environment or production changes in implementation tickets.
New routes return 404 when disabled, and new optional creation fields are gated.
Old clients omitting fields keep working. An additive migration needs no content
backfill; schema implementation still requires upgrade testing against current
migrations. Do not rewrite old revisions.

Rollback by disabling exposure and deploying compatible code, leaving data intact;
do not run a destructive downgrade on shared data. To disable for everyone, inspect
and remove/disable explicit user-on overrides as well as setting the default off,
with separate authorization. No flag can un-send a provider request already made.

The owner authorized execution against the PRD/TDD baseline into
`mattjmcnaughton/tutor`, starting at current `origin/main`; the ticket guide records
the exact initial revision and permission grant. Neither `main` nor the unrelated
Home Screen feature branch is a permitted PR target. Before dogfooding, authorize
designated accounts, live model spend and rollout separately. This design work has
not run application tests or demonstrated these guarantees.
