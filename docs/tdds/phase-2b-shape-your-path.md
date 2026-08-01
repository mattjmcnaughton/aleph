# TDD — Phase 2B: Shape your path (learner-initiated)

**Status:** Draft · **Owner:** solo builder · **Companion to:** [Phase 2B PRD](../prds/phase-2b-shape-your-path.md)
**References:** [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) · [Phase 1 TDD](phase-1-path-generation.md) · [Phase 2 TDD](phase-2-tutor.md) · mock: [phase-2 tutor, Turn 3](../mocks/aleph-phase-2-tutor.html)

> The PRD owns the product boundary. This TDD owns everything the PRD delegated: the
> proposal payload and its validation, apply/undo transaction mechanics, position
> renumbering, revision regeneration and its consistency posture, storage schema, context
> assembly, model routing, and how W17–W21 and the proposal evals actually run.

Decision numbers restart at D1, scoped to this document. References into earlier TDDs are
always qualified ("Phase 1 D5", "Phase 2 §5.4").

> **Read §16 beside this document.** The phase shipped, and where the build learned
> something the design had not settled, [§16](#16-shipped-deltas-amendments) records it as a
> dated amendment rather than a quiet rewrite. Sections below carry an `(A*n*)` marker where
> an amendment applies; the marked text is corrected in place only when leaving it would
> state something the code does not do.

## 1. Decision record

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| D1 | Edit vocabulary | Exactly two operations in the proposal payload — `add_lessons` (optionally grouping new lessons as a new unit) and `revise_lesson` — validated by pure predicates shared with the evals | PRD §4/§5.3. A tiny closed vocabulary is what makes "consent is structural" checkable; every out-of-vocabulary ask dies in the prompt (declined edit), not in a validator |
| D2 | Engagement boundary | **Engaged = an Attempt exists on the lesson's Quick check, or `completed_at` is set.** Derived from existing columns, never stored; enforced server-side at proposal validation, apply, and undo | PRD §6's invariant amendment needs one DB-derivable predicate, used identically in three places. Viewing is deliberately not engagement — it isn't recorded in the DB, and a learner-initiated revision of a merely-viewed lesson is the feature working |
| D3 | Storage | `conversations` gains `kind` (`lesson \| shaping`, UNIQUE `(path_id, kind)` replacing UNIQUE `(path_id)`); `messages` gains `proposal jsonb NULL`; new `path_changes` table; `lessons` gains `revision_instruction text NULL`. Migrations `0005`–`0007` (**A1** — the phase's numbering moved) | PRD §5.8/§6 verbatim: two threads per path, proposals persist like Tutor checks, changes outlive threads. All additive except the widened unique constraint |
| D4 | Proposal transport | One no-op agent tool — `propose_path_edit` — observed by the service from the event stream, exactly Phase 2 D5; one proposal per reply; new SSE event `proposal` | The 2A pattern transfers whole: streaming forbids output unions, a tool gives a validated payload with invisible `ModelRetry`, and the service stays the only writer |
| D5 | Apply mechanics | `POST /messages/{id}/apply-proposal` **re-validates the full payload against live path state, then applies in one transaction** (insert rows / snapshot + reset), creating the `path_changes` row. Per-path apply lock; stale → 409 with a reason the card can render | PRD §5.4's staleness rule. The proposal was validated at draft time, but the path moves (the learner attempts the revision target, another change lands); apply-time truth is the only truth |
| D6 | Position renumbering | Insertions shift `position_in_path` (and unit/`position_in_unit` rows) **in descending order inside the apply transaction** | The `UNIQUE (path_id, position_in_path)` constraint stays load-bearing (Phase 1 §4); descending updates never collide with it mid-shift, so no deferred constraints, no temporary offsets |
| D7 | Revision mechanics | Apply snapshots the passage + Quick check into the change payload, deletes the `quick_checks` row, clears content, sets `generation_state='ungenerated'` + `revision_instruction`; regeneration rides the **unchanged Phase 1 pipeline**, whose lesson prompt gains one revision block (old passage + instruction + consistency rule). `revision_instruction` clears on success | Reuses claims, stale recovery, prefetch, retry, and the poll-as-trigger loop with zero orchestration changes. Deleting the check is safe by D2 — an unengaged lesson has no Attempts. The old passage stays in the prompt so the revision *re-pitches* rather than re-invents, keeping already-generated neighbors coherent (PRD §6/§11) |
| D8 | Undo mechanics | One transaction, guarded by D2 re-check: delete rows the change added (reversing D6's shifts **last-first** — **A4**), restore snapshots for revisions; `status='undone'`. Undo is **last-in-first-out** over a path's live Changes (**A3**). In-flight generation of a deleted row ends harmlessly (guarded `UPDATE … WHERE id` matches nothing) | PRD §5.5's "restores exactly" is a transactional claim. The snapshot lives in the change row, so undo needs no second source of truth |
| D9 | Context assembly | `assemble_shaping_context(session, *, path) -> AssembledContext` in the **same** `services/tutor_context.py` seam; shaping scope = digest + per-lesson Outcome + engaged flag + change-history summary; carried turns use the same bounded-window discipline (`TUTOR_CONTEXT_TURNS`) | The seam the Phase 2 PRD §6 promised 2B would land behind — it does, just for shaping scope instead of path-Q&A scope. No lesson bodies keeps the context small relative to the lesson-scope turn (≈4.5k tok at the old 30-lesson cap, ≈8k at the current 200-lesson cap, §5.2) — it scales with `MAX_LESSONS_PER_PATH`, not flat, but grows slowly since it is titles/outcomes only |
| D10 | Model routing | Fifth slot `MODEL_SHAPER`, default `anthropic/claude-sonnet-5`; admin picker extends to it as a per-message override, never persisted | Uniform-start discipline. Shaping is outline-shaped work (structure under constraints), so the refinement direction is *up or sideways*, not down — the opposite of the tutor slot, worth its own knob |
| D11 | Interactive concurrency | Shaping replies share the **tutor** semaphore and timeout (`MAX_CONCURRENT_TUTOR_REPLIES`, `TUTOR_REPLY_TIMEOUT`) and get their own one-in-flight-per-conversation lock; **apply/undo** take a per-path lock instead | Both reply kinds are the same workload class (a learner waiting mid-sentence); splitting the pool would just let one starve the other. Apply/undo contend on the *path*, not the conversation — a different lock for a different resource |
| D12 | E2E determinism | The Phase 2 streamed stub gains shaping sentinels (`[force-proposal-add]`, `[force-proposal-revise]`, `[force-shaping-decline]`, `[force-shaping-failure]`); stateless, stripped from output | Phase 1/2 D10 discipline: W17–W21 must not depend on a real model choosing to call the tool |
| D13 | Evals | `path_proposal` as a fourth artifact kind in the same harness; rubric item 1 (well-formed) is wholly deterministic via the shared D1 predicates; added/revised *content* re-judged by the Phase 1 lesson rubric | PRD §9. The proposal payload is exactly the kind of fixed, checkable object Phase 1's harness was built for |
| D14 | Frontend | Same rail component tree, third mount: `shaping-rail` testids on the path route; ghost rows render **client-side** by merging the pending proposal payload into the path-rail data; no server preview endpoint | Phase 2 D12's one-tree-two-presentations rule extends to a second mount unchanged. The payload is already the full statement of the edit, so a preview endpoint would be a second implementation of it |

## 2. Extension map

| Concern | Existing asset | Phase 2B change |
| --- | --- | --- |
| Conversations & messages | Phase 2 schema, `repositories/conversations.py` | **Extend:** `kind` column + widened unique (D3); repository queries take a kind; `proposal` payload column parallel to `tutor_check` |
| Reply transport | Phase 2 §5.4 SSE (deltas, heartbeats, timeout, error envelope) | **Reuse verbatim**; one new named event `proposal` |
| Turn lifecycle | `services/tutor.py` (admit → assemble → stream → persist → events) | **Extend:** the shaping turn runs the same lifecycle with shaper agent + shaping context; proposal payload persisted like a check payload |
| Agent conventions | `agents/tutor.py` purity rules, layering test auto-discovery, `pose_tutor_check` no-op tool pattern | **New** `agents/shaper.py` under the same rules; `propose_path_edit` follows the D5-observed-tool pattern; predicates exported for evals |
| Generation machinery | Phase 1 §5.4 (claims, stale recovery, prefetch, reconciler, poll-as-trigger, retry) | **Reuse untouched** for added and revised lessons; the lesson prompt gains a revision block (D7) — the orchestrator does not change |
| Continuity context | `build_prior_context()` walks `position_in_path` | **Reuse:** an inserted lesson at position *k* gets passages *1…k−1* by construction |
| Model resolution | `services/openrouter.py`, `_forbid_stub_in_production` tuple, allowlist picker | **Extend:** `model_shaper` joins config, the offenders tuple, the picker, and `scripts/e2e_backend.py` |
| Rate limiting | `services/rate_limit.py` (`_exempt`, cap ≤ 0 disabled) | **Extend:** `check_shaping_message` over live shaping learner-message rows; applied additions already bounded by `RATE_LIMIT_LESSON_GENERATIONS_PER_DAY` + `MAX_LESSONS_PER_PATH` |
| Auth, ownership, envelope | `get_current_user`, `OwnedPath`, 404-never-403 | **Reuse verbatim** |
| Product events | `events.py` manifest + emitters + saved queries + three-test loop | **Extend:** six shaping events + queries + `docs/metrics.md` rows (§9) |
| Frontend shell | Rail tree (Phase 2 D12), `Markdown` renderer, TanStack Query state | **Extend:** path-route mount, proposal card, ghost-row merge, change-history sheet; replies render through `Markdown` (still the only pipeline) |
| E2E harness | Streamed stub + sentinels, mobile-390x844 journeys | **Extend:** shaping sentinels (D12), `journeys/w17…w21.spec.ts` |
| Evals | Harness, `[judge-fail:]` stub, calibration | **Extend:** `path_proposal` kind, `shaper_seed_set.yaml`, proposal judge prompt (§10) |

**Built new:** migrations `0005`–`0007` (**A1**) + `models/path_change.py` (§4),
`agents/shaper.py` (§5.1), `assemble_shaping_context` (§5.2), `services/shaping.py` (turn +
apply + undo orchestration, §5.5–§5.7), `repositories/changes.py`, `routers/v1/shaping.py` +
`dtos/shaping.py` (§6), and the shaping frontend surfaces (§8). Two pure modules the design
did not name fell out of the build: `domains/engagement.py` (the D2 predicate) and
`domains/changes.py` (the Change payload, its inverse and the position-shift plans — it sits
in `domains/` because `services/shaping.py` writes it and `services/generation.py` reads the
revision snapshot back out, and neither may import the other).

## 3. Architecture overview

Layering unchanged: `routers → services → (agents, repositories)`. The structural claim the
PRD makes — *the only write path into path structure is Apply on a validated Proposal* — is
enforced by module topology: `services/shaping.py` is the **only** module that writes to
`units`/`lessons` outside Phase 1's generation pipeline, and it does so only inside
`apply_change`/`undo_change`. The shaper agent remains pure and the turn service persists
only conversation rows.

```
src/aleph/
  agents/
    shaper.py           # shaping agent: ShaperDeps → streamed Markdown + propose_path_edit
  routers/v1/
    shaping.py          # shaping conversation read/clear/send, apply, undo, change history
  services/
    shaping.py          # shaping turn orchestration + apply/undo transactions
    tutor_context.py    # gains assemble_shaping_context (the promised seam extension)
  repositories/
    changes.py          # path_changes
  models/
    path_change.py      # (+ conversation.kind, message.proposal, lesson.revision_instruction)
  dtos/
    shaping.py
```

## 4. Data model & storage schema (migrations `0005`–`0007` — **A1**)

```
0005_shaping
conversations   + kind enum(lesson | shaping) NOT NULL DEFAULT 'lesson'
                  UNIQUE (path_id) → UNIQUE (path_id, kind)   (backfill: existing rows 'lesson')

messages        + proposal jsonb NULL          (tutor rows in shaping threads only, by role —
                                                app-enforced like source/tutor_check)

lessons         + revision_instruction text NULL   (set by apply, cleared on generated)

path_changes    path_id FK→paths ON DELETE CASCADE ·
                message_id FK→messages ON DELETE SET NULL ·
                kind enum(add_lessons | revise_lesson) ·      (the payload's *dominant* shape — A9)
                payload jsonb ·
                status enum(applied | undone) ·
                applied_at timestamptz · undone_at timestamptz NULL

0006_shaping_message_lesson
messages        lesson_id NOT NULL → NULLABLE   (a shaping turn is asked in no lesson — A1)

0007_applied_change_uniqueness
path_changes    + partial UNIQUE (message_id) WHERE status = 'applied'
                                               (cross-process "applied at most once" — A1)
```

- **`path_changes.message_id` is `SET NULL`, not cascade:** the change history must survive
  **new conversation** (PRD §5.8) — clearing the thread deletes messages, never history.
  Path deletion cascades everything, as always.
- **Proposal payload** (validated at draft time by the agent tool, re-validated at apply —
  D5): `{operations: [AddLessons | ReviseLesson], summary}` where
  `AddLessons = {insert_at_position, new_unit: {title, summary} | null, lessons: [{title}],
  rationale, estimated_minutes}` and `ReviseLesson = {lesson_id, instruction, new_title |
  null, rationale}`. Positions are `position_in_path` values in the payload's snapshot of
  the path; apply re-resolves them (D5).
- **Change payload** = the applied operations **plus inverses**: created lesson/unit ids for
  additions; the full pre-revision snapshot (`read_passage`, quick check row, title,
  `generated_at`) for revisions. The change row is self-sufficient for undo (D8).
- **One Apply is one Change** (**A9**), even when the Proposal mixes Additions and
  Revisions: a Change is the unit of Apply *and* of Undo, so undoing half of what the
  learner consented to as one edit would leave the path in a shape nobody proposed. The
  `kind` column therefore records the payload's *dominant* shape (adds anything →
  `add_lessons`), and the wire's `ChangeDTO.kinds` is a list **derived** from the payload,
  so a mixed edit reports both.
- **Proposal resolution state is derived, not stored:** a proposal message is *applied* if a
  live `path_changes` row references it, *undone* if that row is undone, *superseded* if a
  later proposal in the thread was applied first and re-validation now fails, else
  *pending*. No status column to keep consistent.
- **State machine amendment (the one Phase 1 change):** `generated` is no longer terminal —
  `generated → ungenerated` exists, reachable **only** inside `apply_change` for a
  `revise_lesson` on an unengaged lesson (D2 guard). Phase 1's diagram note and CONTEXT.md
  already carry the amended invariant (*immutable once engaged*). Stale recovery, retry,
  and every other edge are untouched.

## 5. The shaping pipeline

### 5.1 Shaper agent (`agents/shaper.py`)

Same purity rules; auto-covered by the layering test.

- **Deps** — `ShaperDeps` (frozen): `topic`, `level`, `digest: Sequence[ShapingDigestEntry]`
  (**`lesson_id`** — **A8** — unit title, lesson title, `position_in_path`, unlock state,
  `engaged: bool`, `outcome: correct | incorrect | None`), `change_history:
  Sequence[ChangeSummary]` (plain-language line + status), `caps: ShapingCaps`
  (`lessons_remaining` under `MAX_LESSONS_PER_PATH`, `max_lessons_per_proposal`,
  `first_shapeable_position` — the first non-engaged position, precomputed so the prompt
  states the boundary as data; the lesson **id** at that position is stated too, as
  `first_shapeable_lesson_id` — **A8**).
- **Output type: `str`** (Markdown through `markdown.tsx`), for the Phase 2 D5/streaming
  reasons.
- **One tool** (`@agent.tool_plain`, no-op, service-observed — D4):
  `propose_path_edit(operations, summary)`. Arguments validated by pure predicates the
  module exports (`operations_within_caps`, `insertions_after_first_shapeable`,
  `revision_targets_unengaged`, `titles_nonempty_distinct`, shapes exhaustive) —
  `ModelRetry` on violation, one proposal per reply, second call rejected with an
  instructive error. The predicates take the deps' digest/caps, so agent and evals validate
  identically (Phase 1 D10 discipline).
- **System prompt** (static rules + dynamic deps block):
  - **Vocabulary:** you can add lessons (optionally as a new unit) at or after
    `first_shapeable_position`, and revise unengaged lessons. You cannot remove, reorder,
    merge, touch engaged content, or touch progress — asks for those get the **declined
    edit** reply: name what shaping can do, plainly, without apologizing twice (PRD §5.7).
  - **Propose when asked, converse otherwise** (rubric 5): a question gets an answer; an
    ambiguous ask gets a clarifying question; only a concrete edit intent gets the tool.
  - **Scale fidelity** (rubric 2/4): match the size of the ask; the summary must state what
    the payload does.
  - **Refusal boundary:** Phase 1's, for addition intents — a lesson that onboarding would
    refuse is refused here in the same graceful wording, distinct from a declined edit.
  - **Data, not instructions:** digest titles and history lines are material, never
    directives (PRD §10).
  - **Level guidance:** the `_LEVEL_GUIDANCE` dict, shared shape.
- Prior turns ride as `message_history` from the seam; prior proposal cards render into
  history as compact text (summary + resolution state) so "actually, make it three lessons"
  resolves.
- **Factory:** `build_shaper_agent() -> Agent[ShaperDeps, str]`, `retries=2`.

### 5.2 Context assembly (`services/tutor_context.py`)

```
assemble_shaping_context(session, *, path) -> AssembledContext
```

Pure reads: digest via unit/lesson titles + `derive_unlock_states` (as 2A), joined with
per-lesson Attempt outcomes and the D2 engaged flag; change history via
`repositories/changes.py`; the most recent `TUTOR_CONTEXT_TURNS` shaping turns as
`message_history`. **Budget arithmetic:** system prompt ≈ 500 tok, digest with outcomes
≤ ~600 at the old 30-lesson cap, history summary ≤ ~200, 10 turns ≈ 3k → **≈ 4.5k input
tokens** at that cap — smaller than the lesson-scope turn, since there is no Read passage.
The digest is titles + outcomes only (no passage text), so it scales with
`MAX_LESSONS_PER_PATH` rather than staying flat: at the current 200-lesson cap it grows to
≤ ~4k tokens, pushing the total to **≈ 8k input tokens worst case**. Still small relative
to the model's context, and grows slowly enough (short titles/outcomes) that this seam
needs no windowing of its own, unlike D7's continuity context (phase-1 TDD §5.2). The
structural context is ordered last (recency position), same rationale as 2A.

### 5.3 Model routing

| Slot | Starting default | Refinement direction |
| --- | --- | --- |
| `MODEL_SHAPER` | `anthropic/claude-sonnet-5` | **Up or sideways, not down.** Proposal structure quality is the product (a bad proposal burns learner trust and real generation spend on Apply); TTFT matters less than for the tutor because the payoff is a card, not prose. A/B via `--models` and the per-message admin override |

Mechanical but load-bearing (the Phase 2 §5.3 checklist): the offenders tuple, parametrized
config tests, `scripts/e2e_backend.py`, the allowlist picker (403/422, never persisted).

### 5.4 Transport

Phase 2 §5.4 verbatim — same streamed POST, heartbeats, timeout, pre-stream JSON errors —
plus one named event:

| Event | Data | When |
| --- | --- | --- |
| `proposal` | full validated payload | When `propose_path_edit` is observed |

### 5.5 Shaping turn lifecycle (`services/shaping.py`)

Phase 2 §5.5's admit → assemble → stream → persist-atomically → emit shape, with:
conversation resolved/created lazily by `(path_id, kind='shaping')`; path must be `ready`
(409 otherwise — no structure to shape, the PRD §5.1 rule, server-enforced); the observed
proposal payload persisted on the tutor message row; `proposal_shown` emitted alongside the
reply events. Failure/stop/refusal semantics are Phase 2 §5.6 unchanged; the **declined
edit** is, like a refusal, an ordinary persisted turn distinguished only by wording (no
machine tag — same D5-cut posture as 2A, same additive path back).

### 5.6 Apply lifecycle

`POST /api/v1/messages/{id}/apply-proposal`, under the per-path apply lock (D11):

1. Resolve ownership (message → conversation → path → user, 404), kind = shaping, message
   carries a proposal, proposal not already applied (409 `already_applied`) and not
   superseded.
2. **Re-validate against live state** (D5): re-run the D1 predicates with fresh digest/caps;
   re-resolve insertion positions; revision targets must exist, be unengaged (D2), and not
   be `generating` right now (409 `target_generating`, retryable — a prefetch may hold the
   claim). Any failure → a 409 whose `details.reason` names the rule that fired (**A5** —
   shipped as a closed set of reasons rather than one `stale_proposal`, so the card can
   offer the matching affordance); the card renders why. One of those checks is not a
   predicate: **position freshness** (**A5**) refuses when any structural shift event —
   an Apply *or* an Undo — has landed since the Proposal was made, at or below the last
   position the payload names (`409 positions_shifted`).
3. **One transaction:** insert the `path_changes` row (payload + inverses); for additions —
   shift positions descending (D6), insert unit/lesson rows (`ungenerated`); for revisions —
   snapshot into the change payload, delete the Quick check, clear content, set
   `ungenerated` + `revision_instruction`.
4. Emit `change_applied`; kick the prefetch driver (`ensure_generated_through`) so new work
   starts without waiting for a poll; return the change + **the refreshed path** — exactly
   the `GET /paths/{id}` body (**A2**) — so the client swaps ghosts for real rows in one
   round trip.

Generation of the new/revised rows is then entirely Phase 1's problem, on Phase 1's states,
retries, and caps — a shaping Change is *applied* when structure lands, not when generation
finishes (PRD §5.7).

### 5.7 Undo lifecycle

`POST /api/v1/changes/{id}/undo`, same lock:

1. Ownership via path; `status='applied'` (409 otherwise, idempotent-friendly wording); and
   **newest live Change on this path** (**A3**) — an older one is `409 not_latest` until the
   Changes above it are undone.
2. **Engagement re-check (D2):** any Attempt or completion on a lesson the change created or
   revised → 409 `engaged` — the UI's disabled state is a convenience, this check is the
   rule.
3. One transaction: delete added lessons/unit (guarded — step 2 proved them unengaged),
   replay the recorded shifts' inverses **last-first** (**A4** — which is ascending for the
   ordinary single-insertion plan, and stays correct when one payload carries several
   Additions), restore revision snapshots
   (passage, Quick check row, title, `generated_at`, state `generated`, clear
   `revision_instruction`); set `status='undone'`.
4. Emit `change_undone`. An in-flight generation task for a deleted row finishes into a
   guarded zero-row `UPDATE` and is dropped; a claim on a *restored* revision row is
   impossible — restoring requires the row not `generating` (409 `conflict`, retry in a
   moment) — checked in step 2.

### 5.8 Failure semantics (delta over Phase 2 §5.6)

| Case | Wire result | State | Learner sees |
| --- | --- | --- | --- |
| Reply failure/stop/timeout | Phase 2 §5.6 verbatim | Nothing persisted | Same states, shaping rail |
| Proposal tool arguments invalid after retries | Reply completes without a proposal | Turn persists, no payload | An honest reply; no card. Rubric 1 + `ModelRetry` make this rare |
| Apply: stale / engaged / generating / cap exceeded | 409 + coded reason | Nothing | Card explains ("this lesson has been started since"), offers re-ask |
| Apply: transaction failure | 500 envelope | Nothing — atomic | Retry on the card; path never half-changed (PRD §5.7) |
| Undo: engaged since | 409 `engaged` | Change stays applied | History says why undo closed |
| Undo: a later live Change sits on top (**A3**) | 409 `not_latest` | Change stays applied | History says to undo the newer one first |
| Generation of added/revised lesson fails | — (async) | Phase 1 `failed` + retry | Phase 1's lesson error UI; the Change stays applied |

No shaping state touches lesson reading, Quick checks, or completion — the shaping router
has no routes into them (W21's guarantee is structural, as in 2A).

## 6. API design

New router `routers/v1/shaping.py`; all Phase 1/2 conventions verbatim (cookie auth, UUIDs,
404-never-403, error envelope). `docs/api.md` gains a `## Shaping` section.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/paths/{id}/shaping/conversation` | The shaping thread: messages with role, content, proposal (+ derived resolution state), timestamps. 200 + empty list when none. Unpaginated (2A's accepted risk, same shape) |
| `POST /api/v1/paths/{id}/shaping/conversation/messages` | Send a turn `{content, source, model?}` → SSE per §5.4. Path not `ready` → 409. Admin `model` override semantics identical to 2A |
| `DELETE /api/v1/paths/{id}/shaping/conversation` | New conversation. 204, idempotent. Change history unaffected (D3) |
| `POST /api/v1/messages/{id}/apply-proposal` | §5.6 → 200 `{change, path}` (**A2** — the refreshed `PathDetailResponse`, the same body `GET /paths/{id}` returns) |
| `POST /api/v1/changes/{id}/undo` | §5.7 → 204 |
| `GET /api/v1/paths/{id}/changes` | Change history, newest first: plain-language summary, kind(s), status, timestamps |

DTOs (`dtos/shaping.py`): `ShapingConversationResponse`, `ShapingMessageDTO`,
`ProposalPayloadDTO` / `ProposalDTO` (+ `resolution`), `ChangeDTO` (+ derived `kinds`, **A9**),
`ChangeHistoryResponse`, `ApplyProposalResponse` (**A2**), `ShapingConflictReason` (**A5**)
and `SendShapingMessageRequest` (`TutorMessageStr` reused). Object-wrapped lists, shared
`StrEnum`s. The whole reason set the card renders is `docs/api.md`'s *Coded conflicts* table.

## 7. Rate limiting

`RATE_LIMIT_SHAPING_MESSAGES_PER_DAY = 0` — the 2A posture verbatim (knob exists, behavior
doesn't, one builder behind a provider-side cap), counting live shaping learner-message
rows, with the same recorded thread-clear-refunds caveat as the enablement precondition.
**Spend from applies needs no new limiter:** added/revised lessons are ordinary generations
under `RATE_LIMIT_LESSON_GENERATIONS_PER_DAY`, and path size is capped by
`MAX_LESSONS_PER_PATH`, checked at proposal *and* apply time.

## 8. Frontend

- **Shaping rail:** the rail tree's third mount (D14) on the path route at `lg` /
  bottom-sheet below, floating mark entry, `shaping-rail` testids, iris accent. Entry
  renders only on `ready` paths (server backstop 409). Context chip: *Shaping · {title}* —
  the learner-facing **Path title** (**A11**), never the Topic, which stays prompt material.
  Header: new conversation, collapse, change-history button, admin model picker.
- **Suggestions:** the PRD §5.3 four, client-side constants, `source=suggestion`.
- **Proposal card:** operations grouped with rationale + cost line; states pending (Apply +
  Not now) / applying / applied (→ view in path) / stale (reason + "ask again") / undone,
  plus a sixth **`dismissed`** (**A7**) — "Not now" is pure UI dismissal, so it is a
  client-only state: no request, nothing persisted, and a reload restores the card as
  *pending* (PRD: declining is never destructive).
- **Ghost rows:** the path rail merges the pending proposal payload client-side (D14) —
  insertions as iris ghost rows in place, revisions as an iris "will be revised" marker.
  Ghosts exist only while a proposal is pending in the open thread; applied rows are real
  (teal) data from the refreshed outline.
- **Change history sheet:** read-only list from `GET /changes`; undo button where
  `status='applied'` and the client-known engagement state allows — the 409 is still the
  enforcer.
- **Streaming client:** `lib/tutor-stream.ts` reused; it gains the `proposal` event the way
  it carries `tutor_check`. Conversation state is TanStack Query, apply invalidates the
  path outline query (the same one Phase 1 polling populates — ghosts swap for real rows
  without new plumbing).

## 9. Instrumentation & observability

Six events through `events.py` (manifest entry, emitter, `test_events` case each), Phase 1
field shape (`account_id`, `path_id`, timestamps; no `lesson_id` — shaping is path-level,
operations carry their lesson ids in payload-derived fields):

| Event | Extra fields | When |
| --- | --- | --- |
| `shaping_conversation_started` | — | Shaping conversation row created |
| `shaping_message_sent` | `source` | Turn admitted |
| `shaping_reply_completed` | `outcome`, `success` (**A6**), `ttft_ms`, `duration_ms`, tokens, `has_proposal` | Every reply resolution |
| `proposal_shown` | `n_add_lessons`, `n_revisions`, `new_unit` | Payload observed |
| `change_applied` | `change_id`, kinds/counts as above, `lesson_ids` (**A6**) | Apply commits |
| `change_undone` | `change_id`, `minutes_since_apply` | Undo commits |

Workflow tags: `W17` on apply-path events, `W18` revision fields, `W19` on `change_undone`,
`W20` implicitly (a declined edit is a normal reply — no tag), `W21` on the guardrail
queries.

**Saved queries** (one per PRD §7 metric + `docs/metrics.md` rows): `shaping_yield.sql`
(the primary — applied changes joined to later `quick_check_attempted`/`lesson_completed`
on their created/revised lesson ids, 7-day window), `shaping_adoption.sql`,
`proposal_acceptance.sql`, `edit_shape_mix.sql`, `undo_rate.sql`,
`depth_to_proposal.sql`, `shaped_path_completion_guardrail.sql` (completion on shaped vs
unshaped paths), `shaping_reply_failure_latency.sql`. Generation spend stays on
pydantic-ai spans; revised-lesson Quick-check correctness reads off Phase 1's existing
events filtered by revised lesson ids.

## 10. Evals

Fourth artifact kind, same harness (extend, never a second one).

- **Artifact:** `path_proposal` — input is (path state fixture: digest + outcomes + engaged
  flags + history, conversation, instruction); output is the payload + reply text.
- **Layer 1 (deterministic, gates judge spend):** rubric item 1 entirely — the shared D1
  predicates against the fixture state (shapes, positions vs `first_shapeable_position`,
  engagement of revision targets, caps, distinct non-empty titles) — plus reply non-empty,
  and **proposes-only-when-asked** for the conversational cases (a pure-question case must
  produce no payload; deterministic because presence of the tool call is observable).
- **Layer 2 (judge):** rubric items 2–6 (responsive, coherent, honest, bounded, safe) via
  `build_path_proposal_judge_prompt`; judge sees the fixture state, the ask, the payload,
  and the reply. `[judge-fail:<item>]` honored. **Hard floor: safe** (item 6).
- **Content follow-through:** for a subset of cases the harness applies the proposal to the
  fixture and generates the added/revised lessons through the real pipeline, judging them
  with the **existing Phase 1 lesson rubric** (accuracy, level, continuity with neighbors —
  the D7 consistency posture's check). Shaping must not be a side door to worse content.
- **Seed set** (`evals/shaper_seed_set.yaml`, ~24 cases over the existing seed topics ×
  levels): add-missing-subtopic, add-with-new-unit, revise-simpler, revise-deeper,
  scale-fidelity ("a couple" must stay ≤ 2–3), at-cap ask, remove/reorder asks (declined
  edit), revise-engaged ask (declined), pure question (no proposal), over-the-boundary
  addition (refusal), a clarify-first ambiguous ask.
- **Gates:** ≥ 90% to merge shaper-prompt or shaping-context changes; any item-6 failure
  hard-blocks; `human_labels.yaml` + `--agreement` extended; `--models` binds the shaper
  slot (other slots held).

## 11. Testing strategy

**Stub sentinels (D12),** question-text triggered, stateless, stripped:

| Sentinel | Effect |
| --- | --- |
| `[force-proposal-add]` | Calls `propose_path_edit` with a deterministic valid 2-lesson addition at `first_shapeable_position` |
| `[force-proposal-revise]` | Deterministic revision of the first unengaged lesson; the regenerated stub passage embeds a recognizable revision marker so W18 asserts the instruction landed structurally |
| `[force-shaping-decline]` | Streams the declined-edit wording — W20's target |
| `[force-shaping-failure]` | Raises mid-stream after ≥ 2 deltas |

- **Unit:** D1 predicates (exhaustive over shapes/edges); engagement derivation (D2);
  position shift/unshift round-trip under the unique constraint (D6/D8, property-style over
  random insert points); shaping context assembly (outcomes, engaged flags, history
  serialization); shaper prompt blocks; proposal-payload DTO mapping + derived resolution;
  config guard (shaper slot in offenders tuple); event emitters vs manifest; stub
  sentinels.
- **Integration** (real Postgres, stub models): full shaping turn with `proposal` SSE
  event; apply → rows inserted `ungenerated`, positions correct, prefetch generates them
  through the untouched Phase 1 pipeline; revision apply → snapshot, regenerate with
  `revision_instruction`, instruction cleared; **stale apply matrix** (target attempted
  since / second change shifted positions / cap now exceeded / target generating → each 409
  code); undo restores byte-identical passage + check and positions (assert full-table
  equality against a pre-apply snapshot); undo-after-engagement 409; apply lock (concurrent
  applies, one wins); thread-clear leaves `path_changes`; per-kind conversation uniqueness;
  cascade delete removes both threads + changes; ownership 404s end-to-end.
- **E2E** (Playwright, mobile-390x844): W17–W21 as tagged journeys per the PRD §8
  definitions — W17 ghost-rows → apply → complete an added lesson; W18 revise via sentinel
  marker; W19 undo → bit-identical rail + history states; W20 decline with zero mutation;
  W21 lesson flow + in-lesson rail untouched while a proposal is pending and a revision
  regenerates.
- **Frontend unit** (vitest + MSW): proposal event parsing, card state machine (pending →
  applying → applied/stale), ghost-row merge (insert positions, revision markers), history
  sheet, undo-disabled derivation.
- **External:** one live shaping round trip that yields a valid proposal on a real model —
  drift canary; quality stays §10's job. **Not built this phase** (**A10**): `tests/external/`
  still holds only Phase 1's outline+lesson contract test, exactly as 2A's own live-tutor
  canary went unbuilt. Nothing depends on it — it is opt-in, never in CI — so it stays a
  named gap rather than a silent one.

## 12. Deployment & ops

No new secrets, services, or fly.toml changes **until launch**, which is one committed
`FEATURE_FLAG_DEFAULTS` entry (AL-370 — [`docs/deploy.md`](../deploy.md#launching-a-flagged-phase-al-270--al-370)).
Migration `0005` (**A1**) is additive except the widened conversation uniqueness (backfill
`kind='lesson'` first, then swap the constraint — safe on Neon in one transaction at this
table's size); `0006` drops a `NOT NULL` and `0007` adds one partial index, both online-safe.
The streaming path is 2A's; `compose-smoke` already proves it. Rollback: the down-revisions
drop the additions; shaping conversations, shaping messages and `path_changes` are
data-loss-on-downgrade like any table, standard posture.

## 13. Configuration (provisional numbers)

| Setting | Default | Notes |
| --- | --- | --- |
| `MODEL_SHAPER` | `anthropic/claude-sonnet-5` | Fifth slot (§5.3); joins the stub production guard |
| `MAX_LESSONS_PER_PROPOSAL` | 5 | Validator + prompt cap on the lessons one Proposal **adds or revises in total**: one proposal stays legible (PRD §5.4 "small and legible"); a bigger ask becomes two proposals |
| `RATE_LIMIT_SHAPING_MESSAGES_PER_DAY` | 0 (disabled) | §7 |
| Carried-turn window | reuses `TUTOR_CONTEXT_TURNS` (10) | Same knob deliberately — one notion of "recent conversation" |
| Reply timeout / semaphore | reuses `TUTOR_REPLY_TIMEOUT` (90s) / `MAX_CONCURRENT_TUTOR_REPLIES` (8) | D11 |
| Latency budgets (guardrails) | 2A's: TTFT p95 ≤ 3s · complete p95 ≤ 30s | Via `shaping_reply_completed` |

## 14. Risks & open questions

- **Position renumbering under load is the fiddliest correctness surface** (D6/D8): shifts
  must survive concurrent polls reading the outline mid-transaction (they read committed
  state — fine) and concurrent generation claims (claims are by lesson id, not position —
  fine by construction). The integration matrix and the property-style unit test are the
  insurance; if a real anomaly appears, `SELECT … FOR UPDATE` on the path's lessons inside
  apply is the escalation, already compatible with the apply lock.
- **Revision consistency is a prompt-level promise, not a structural one** (D7): the old
  passage + "preserve factual commitments, change the pitch" instruction is the mechanism;
  rubric 3 and the content follow-through evals are the check. If revised lessons still
  contradict already-generated neighbors, the escalation ladder is: include neighbor
  passages in the revision prompt (cost), then offer downstream regeneration of unengaged
  lessons as part of apply (product change — back through the PRD).
- **Stale proposals will happen in normal use** (learner chats, walks away, attempts the
  target lesson, returns, taps Apply). The 409-with-reason path is therefore a first-class
  UX, not an error corner — W-adjacent coverage lives in the integration matrix, and
  `proposal_acceptance.sql` should be read alongside a stale-rate follow-up query if
  acceptance looks oddly low.
- **Derived proposal resolution** (D3) trades a status column for a join; if thread reads
  grow a visible cost, materializing `resolution` onto the message payload at
  apply/undo time is additive.
- **Ghost rows render from the payload, not the server** (D14): a pending proposal drawn
  against a path that has since changed can preview slightly stale positions. Accepted —
  apply re-validates (D5), so the preview can be optimistic but the mutation cannot.
  Re-rendering ghosts against the refreshed outline on window focus is a cheap mitigation
  if it reads badly.
- **Two interactive reply kinds share one semaphore** (D11): a shaping burst can queue
  tutor replies. Accepted at one-builder scale; the knob split is trivial if Logfire shows
  contention.
- **Open: does `MAX_LESSONS_PER_PROPOSAL=5` match how learners ask?** The declined/clamped
  ask rate in real conversations (Logfire spans) is the datum.
- **Open: the PRD §11 removal question** — the declined-edit rate for remove/reorder asks
  decides whether Phase 4 prioritizes destructive shapes. This TDD deliberately builds
  nothing speculative for them (no soft-delete columns, no tombstones): the change-history
  pattern is the extension point if and when they come.

## 15. Tickets

GitHub issues, cut from this document in a follow-up PR — issues are the source of truth
(the Phase 1/2 pattern):

- **Label:** `tdd-shape-your-path`; parent epic carrying shared context and the dependency
  graph (the Phase 2 epic, [#82](https://github.com/mattjmcnaughton/aleph/issues/82), is
  the template). `for-ai` / `for-human` split as before — expected `for-human` surface:
  eval labeling and the production ship verification.
- **Numbering:** AL-3xx. Natural seams, in dependency order: schema + migration 0004 +
  changes repository (§4) → config + shaper slot (§5.3) → stub shaping sentinels (§11) →
  shaper agent + predicates (§5.1) → shaping context seam (§5.2) → shaping turn service +
  proposal SSE (§5.4–§5.5) → apply/undo transactions + endpoints + change history (§5.6–
  §5.7, §6) → shaping rail frontend (§8) → proposal card + ghost rows + history UI (§8) →
  instrumentation + queries (§9) → evals (§10, post-launch per the 2A convention if the
  owner repeats it) → e2e W17–W21 (§11) → docs sweep (`api.md`, `metrics.md`, `evals.md`,
  CONTEXT.md) → ship verification 👤.

## 16. Shipped deltas (amendments)

*Recorded 2026-08-01 by AL-361, the phase's docs sweep, from the deltas each ticket flagged
on its PR.* This document is a record, so the design above stands as it was written; every
place the build settled something differently is listed here and marked `(A*n*)` where it
applies. Nothing here re-opens a decision — each entry is what shipped, and why.

| # | Delta | Where it landed |
| --- | --- | --- |
| **A1** | **Migration numbering.** The epic reserved `0004`; that number was taken by `0004_user_feature_overrides` (AL-203) before this phase started. Shipped as **`0005_shaping`** (D3's schema verbatim), **`0006_shaping_message_lesson`** — `messages.lesson_id` becomes NULLABLE, the one column §4 did not name and a shaping turn cannot fill, so `load_thread` outer-joins the lesson — and **`0007_applied_change_uniqueness`**, a partial `UNIQUE (message_id) WHERE status='applied'` that makes "a Proposal is applied at most once" hold across the two machines a rolling deploy briefly runs (the per-path lock is per-process). The owner's `0008_path_title_and_guidance` follows, outside this phase. | §2, §4, §12, D3 |
| **A2** | **Apply returns `{change, path}`,** not `{change, outline}` — the whole refreshed `PathDetailResponse`, exactly what `GET /paths/{id}` answers, so the client drops it into the cache Phase 1 polling already populates instead of learning a second shape. | §5.6, §6 |
| **A3** | **Undo is last-in-first-out.** Only the newest **live** Change on a path may be undone; an older one is `409 not_latest` until the ones above it are. PRD §5.5's "restores exactly" is a claim about *this* Change's recorded inverse, which is a list of **absolute** positions in the coordinate frame of the path as it stood at apply time. Replaying that against a path a later Change has moved can collide with the later Change's slot and — worse, silently — reorder its lessons around one the learner placed them against; nothing in the payload relates the two frames. The restriction is therefore the correctness boundary, not a simplification, and it costs nothing PRD §5.5 promises: a Change stays undoable until it is engaged, by undoing the ones above it in turn. Uniform over kinds (a per-kind matrix would be right for a reason nobody can see). | §5.7, §5.8, D8 |
| **A4** | **Undo replays inverses last-first,** not "ascending". For the ordinary single-insertion plan the two are the same thing — D6's plan is descending, so its reverse is ascending — but once one payload carries **two** Additions a lesson appears in the plan twice, and a global sort interleaves the two lessons' moves and collides under `UNIQUE (path_id, position_in_path)`. Reverse chronology cannot: it is the literal inverse of a sequence that never collided. Property-tested (`tests/unit/test_change_payload.py`). | §5.7, D8 |
| **A5** | **Coded `409` reasons, and a position-freshness rule.** §5.6 said "409 `stale_proposal` + which operation went stale"; shipped is a closed `ShapingConflictReason` set (`already_applied`, `already_undone`, `not_applied`, `not_latest`, `path_cap_reached`, `insert_position_taken`, `revision_target_engaged`, `title_conflict`, `positions_shifted`, `invalid_proposal`, `target_generating`, `engaged`) in `details.reason`, because the card has to offer the *matching* affordance — ask again, retry in a moment, or "this is permanent now". Eleven of the twelve are labels on the shared predicates; **`positions_shifted`** is the one apply-time check that is not a predicate: any structural shift event (an Apply **or** an Undo) since the Proposal was made, at or below the **last** position the payload names, refuses. It bounds on the payload's max insert point (a Change landing *between* two Additions leaves only the first meaning what the learner saw) and counts undos (the row that moved the path is the one no longer in force). Conservative by construction: a false `409` costs an "ask again", a false accept writes lessons into a slot nobody consented to. | §5.6, §5.8, §6 |
| **A6** | **Two event fields §9's table did not spell out.** `change_applied` carries **`lesson_ids`** — every lesson the Change created or revised — which is §9's own header sentence made concrete ("operations carry their lesson ids in payload-derived fields") and the join key `shaping_yield.sql` needs, shaping being path-level. `shaping_reply_completed` carries a derived **`success`** beside `outcome`, mirroring 2A's `tutor_reply_completed` so the two latency panels are column-for-column readable. | §9 |
| **A7** | **A sixth, client-only card state: `dismissed`.** "Not now" is a pure UI dismissal (no request, nothing persisted), so it is a state of the card and not of the Proposal — a reload finds it *pending* again, which is the honest rendering of "declining is never destructive". | §8 |
| **A8** | **The digest carries lesson ids.** §5.1's `ShapingDigestEntry` listed no id, while §4's `revise_lesson` names its target *by* `lesson_id` — a Revision is inexpressible without one. `ShapingDigestEntry.lesson_id` and the deps block's `first_shapeable_lesson_id` marker are that fix, and the streamed stub's `[force-proposal-revise]` reads the marker to pick its target (flagged on AL-302/AL-310). | §5.1, §5.2 |
| **A9** | **One Apply is one Change, kinds derived.** A Proposal mixing Additions and Revisions lands as a single row: a Change is the unit of Apply *and* of Undo. `path_changes.kind` records the payload's **dominant** shape (adds anything → `add_lessons`) and `ChangeDTO.kinds` is a list derived from the payload, so history and events agree by construction — the same dominance rule tags `W17` vs `W18`. | §4, §6, §9 |
| **A10** | **The live shaping round trip (§11 "External") was not built,** as 2A's live tutor canary was not. Opt-in, never in CI, nothing depends on it — recorded as a gap rather than quietly dropped. | §11 |
| **A11** | **The caps this phase drafts against moved** (owner's [#141](https://github.com/mattjmcnaughton/aleph/issues/141), landed alongside the phase): `MAX_UNITS` 6 → **25** and `MAX_LESSONS_PER_PATH` 30 → **200**, which is why §5.2/D9 now state the shaping context's budget at both caps. Also from #141: **Path title** is a display label distinct from **Topic** — the rail's chip and the switcher show the title, while the Topic stays frozen prompt material, and no shaping surface reads a title into a prompt. | §5.2, D9 |

### Post-2B follow-ups (recorded, not scheduled)

None of these is a defect; each is a seam the phase left where a later phase should widen it.

- **Extract the shared turn-lifecycle engine.** `services/tutor.py` and
  `services/shaping.py` run the same admit → assemble → stream → settle → emit shape over
  two agents. The duplication was deliberate this phase — W21 froze the in-lesson tutor, and
  refactoring under a bit-identical constraint buys risk, not safety — but the next surface
  that streams should land on one engine rather than a third copy. `_text_of` (the "what did
  the model actually say" helper) is the smallest piece to promote first.
- **Split `services/shaping.py`.** It is past 1900 lines because the turn lifecycle and the
  apply/undo transactions live in one module. `services/shaping_changes.py` is the natural
  seam: apply/undo already share only the per-path lock and the repositories with the turn
  half.
- **Unify the guarded / unguarded emitter asymmetry.** 2B's stamps go through a guarded
  `_emit_guarded` seam (they sit after commits or beside held reservations, where raising
  would misreport landed work); 2A's do not. One posture, applied to both, is the follow-up —
  it touches 2A emitters, which is exactly what W21 forbade this phase.
- **The three shaping e2e timing rules** (AL-360) are written up where the next journey
  author will meet them: [`ci.md` § The journeys](../ci.md#the-journeys-al-090-al-260-al-360).

## Appendix — traceability (PRD's TDD-owned items)

| PRD delegation | Here |
| --- | --- |
| Proposal payload shape & validation ("a contract, not prose", §6) | §4, §5.1, D1 |
| Proposal transport | §5.4, D4 |
| Apply transaction, staleness re-validation (§5.4) | §5.6, D5, D6 |
| Undo mechanics & engagement enforcement (§5.5, §10) | §5.7, D2, D8 |
| Revision regeneration & neighbor consistency (§6) | D7, §10 content follow-through, §14 |
| Storage schema (§6) | §4, D3 |
| Model routing / fifth slot (§6) | §5.3, D10 |
| Context assembly, shaping scope bound (§6) | §5.2, D9 |
| Failure/declined-edit mechanics (§5.7) | §5.5, §5.8 |
| Caps & spend rails (§5.7, §10) | §7, §13 |
| Instrumentation for every §7 metric (§5.9) | §9 |
| Eval harness mechanics (§9) | §10, D13 |
| E2E for W17–W21 (§8) | §11, D12 |
