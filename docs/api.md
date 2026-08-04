# API Reference

## Health

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/healthz` | Liveness probe — returns 200 if the process is alive |
| GET | `/readyz` | Readiness probe — returns 200 if the service is ready to accept traffic |

## Paths (`/api/v1`, AL-050, TDD §6)

Session-cookie protected (`401` via the shared envelope when anonymous). All
addressing is by UUID; another learner's path reads as `404` (never `403` — its
existence is not disclosed). Generation is **trigger + poll** (§5.4/D5): `POST`
routes return `202` immediately and the client polls `GET /paths/{id}` until the
status resolves. Nothing blocks on a model call.

| Method | Path | Body | Success | Notes |
| ------ | ---- | ---- | ------- | ----- |
| `POST` | `/api/v1/paths` | `{topic, level, guidance?, model_outline?, model_lesson?}` | `202 {id}` | Creates the path and triggers its outline. Rate-limited (`429 rate_limited` at the daily cap; admins exempt). `topic` is stripped and bounded to 1–500 chars; a violation or an out-of-enum `level` is `422 validation_error`. `guidance` is the learner's optional free text steering the outline's shape (CONTEXT.md: *Guidance*) — stripped, 1–4000 chars, blank/omitted means none; it is **persisted on the path row** alongside `topic` and read into the outline prompt, so the DB-driven resume/reconcile (§5.4/D6) re-runs the outline with it. There is no route to change it after creation. `model_outline`/`model_lesson` are the **admin model-picker** overrides (AL-052, §5.3/D14) — optional bare OpenRouter ids the picker sends, selected from `MODEL_ALLOWLIST`. Enforced **before** the rate-limit and billed work: an override from a **non-admin** is `403 forbidden`; an override outside `MODEL_ALLOWLIST` is `422 validation_error`. Omitted (the common case) uses the configured slot. The validated choice is **persisted on the path row**, so the DB-driven resume/reconcile (§5.4/D6) re-generates with the chosen model, not the default. |
| `GET` | `/api/v1/paths` | — | `200 {paths: [...]}` | "Your paths" switcher, newest first. Each row: `{id, topic, title, level, status, progress}`. **`status` is the effective status** (a stale `generating` reads as `failed`), matching the detail poll. `title` is the display label (CONTEXT.md: *Path title*) and is **always populated** — the server applies the topic fallback (see below), so the client never respells it. |
| `GET` | `/api/v1/paths/{id}` | — | `200` | Poll target. Body: `{id, topic, title, guidance, level, status, refusal_message, progress, units:[{..., lessons:[{..., generation_state, unlock_state}]}]}`. `status` is effective; `refusal_message` is non-null **only** when `status == refused`. Each lesson carries its effective `generation_state` and derived `unlock_state` (the two orthogonal axes). `title` is always populated (topic fallback applied server-side); `guidance` is the learner's free text from creation, or `null` when none was given — display-only here. The poll is itself a trigger: it spawns the idempotent resume, so a chain lost to a crash self-heals within one poll. |
| `PATCH` | `/api/v1/paths/{id}` | `{title}` | `200` | Renames the path's display label (CONTEXT.md: *Path title*). `title` is stripped and bounded to 1–200 chars (the only field on the body); a violation is `422 validation_error`. Display only — `topic` (the generation input) is never touched, and the rename itself writes only `title`, nothing regenerates because of it. Safe at **any** path status (`pending`/`generating`/`ready`/`failed`/`refused`). Returns the same `PathDetailResponse` shape `GET /paths/{id}` does, built through the **same read seam** — so, like `GET`, this route is itself a poll-as-trigger: it can spawn the idempotent resume (including billable lesson generation to fill the prefetch window) if the path has work to resume. Unrated-limited, same as `GET`. |
| `POST` | `/api/v1/paths/{id}/retry` | — | `202 {id}` | Retry a `failed` outline. Rate-limited by its own daily cap (`check_outline_generation`, reusing `RATE_LIMIT_PATHS_PER_DAY`; admins exempt) → `429 rate_limited` at the cap. **A retry on a terminal path (`ready`/`refused`) or a freshly `generating` one still returns `202` but is a silent no-op** — the claim matches nothing, so nothing is re-run (a refusal is terminal; the learner starts a new topic, §5.5). |
| `DELETE` | `/api/v1/paths/{id}` | — | `204` | Hard-delete; `ON DELETE CASCADE` tears down units, lessons, quick checks, and attempts. Not undoable (UI confirms); doubles as reset. |

**Path title vs. Topic (CONTEXT.md).** `topic` is the generation input: it is
frozen once a path exists (there is no route that writes it after `POST /paths`)
because the outline, and every lesson/tutor prompt since, were generated from
that exact string. `title` is the learner-editable **display** label — it
defaults to (falls back to) the topic until the learner renames it via `PATCH`,
and is deliberately never itself a generation input: no agent prompt reads it.
The rename write itself is display-only and triggers no regeneration — but the
response is built through the same read seam `GET /paths/{id}` uses, so, like
`GET`, a `PATCH` can trigger the poll-as-trigger resume (§5.4/D5) if the path
has generation work outstanding.

**Rate limits (TDD §10).** `POST /paths` and `POST /paths/{id}/retry` both count
against the daily paths cap (`RATE_LIMIT_PATHS_PER_DAY`, default 10): create
counts rows by `created_at`; retry counts paths with an outline attempt today
(by the re-stamped `generation_started_at`), bounding cross-path retry storms.
Same-path retry loops are bounded only by claim serialization + client patience
(accepted for MVP — see `services/rate_limit.py`). Admins (email domain in
`ADMIN_EMAIL_DOMAINS`) are exempt.

**Admin model picker (TDD §5.3/D14).** Admins may A/B models on real paths
without redeploying by pinning the outline/lesson model per-path on `POST /paths`
(`model_outline`/`model_lesson`). The selectable ids are `MODEL_ALLOWLIST`, which
`GET /api/v1/auth/session` exposes to admins (`user.model_allowlist`; `[]` for
everyone else) — the frontend renders the picker from that list. Enforcement is
server-side and does not trust the hidden picker: a non-admin override is `403`,
an off-allowlist id is `422`. The chosen id lives on the path row so pollers and
the reconciler route it too; the actual model used is on each Pydantic AI span
(`gen_ai.request.model`), so Logfire cost data already groups by model.

## Lessons (`/api/v1`, AL-051, TDD §6)

Session-cookie protected (`401` via the shared envelope when anonymous). Address
by UUID; a lesson on another learner's path reads/acts as `404` (existence not
disclosed). Same **trigger + poll** model: `POST /lessons/{id}/generate` returns
`202` and the client polls `GET /lessons/{id}` until `generation_state` resolves.
`attempt` and `complete` are synchronous state changes (not generation triggers).

| Method | Path | Body | Success | Notes |
| ------ | ---- | ---- | ------- | ----- |
| `GET` | `/api/v1/lessons/{id}` | — | `200` | Poll target. Body below. The poll is itself a trigger: it spawns the idempotent resume **and** refills the prefetch window, so *viewing* a lesson advances prefetch (§5.4). `generation_state` is effective (a stale `generating` reads as `failed`); `unlock_state` is derived (`locked`/`available`/`complete`). |
| `POST` | `/api/v1/lessons/{id}/generate` | — | `202 {id}` | Ensure/retry this lesson's generation (also refills the prefetch window). Rate-limited by the daily lesson cap (`check_lesson_generation`, `RATE_LIMIT_LESSON_GENERATIONS_PER_DAY`, default 100; admins exempt) → `429 rate_limited` at the cap. Note this cap (100) is now **below** `MAX_LESSONS_PER_PATH` (200): a learner working through a maximal path spans more than one day of lesson-generation quota — accepted, not a bug (the cap bounds daily spend, not path completion speed). Chain-head gated (§5.2/§5.5): it *ensures* a reached `ungenerated` lesson and *retries* a `failed` one only when all predecessors are `generated`; a trigger on a `generated` or non-chain-head lesson still returns `202` but only advances the window. |
| `POST` | `/api/v1/lessons/{id}/attempt` | `{selected_index}` | `200` | Record the Attempt (first-wins) and grade it server-side. Gates on *not locked*, **not** available-only: **locked → `403`**, but a **complete** lesson stays attemptable (a learner may complete a lesson and still answer its Quick check — completion is orthogonal to the Attempt). An ungenerated lesson (no Quick check yet) → `409 conflict`. Returns `AttemptResult` (below) — the reveal boundary. A second submit never overwrites the first: the response is the first Attempt's Outcome, re-derived from its stored index (the `attempts.is_correct` column is a metrics cache, never trusted — AL-012). |
| `POST` | `/api/v1/lessons/{id}/complete` | — | `200 {id, unlock_state}` | Mark complete (non-gating; orthogonal to the Quick-check Outcome). Only the **available** lesson may be completed (AL-012): **locked → `403`** (a later/not-yet-reached lesson cannot be skipped ahead to); already-**complete → idempotent `200`** no-op (no re-stamp). On success the prefetch window advances (`on_lesson_completed`, after commit) so the newly-unlocked next lesson begins prefetching. |

**`GET /api/v1/lessons/{id}` body.**

```json
{
  "id": "uuid", "path_id": "uuid", "title": "…",
  "position_in_path": 1, "position_in_unit": 1,
  "generation_state": "ungenerated|generating|generated|failed",
  "unlock_state": "locked|available|complete",
  "read_passage": "…str… | null",
  "quick_check": { "stem": "…", "options": ["…", "…", "…"] } | null,
  "attempt": { "selected_index": 0, "outcome": "correct|incorrect",
               "correct_index": 2, "explanation": "…" } | null,
  "generation_error": "generic message | null"
}
```

- `read_passage` / `quick_check` are non-null **only** when `generation_state ==
  generated`. `generation_error` is non-null **only** when `generation_state ==
  failed` (a generic, learner-safe message — never raw provider text).
- **`read_passage` is GitHub-Flavored Markdown**, served as source and rendered
  at the edge (`web/frontend/src/components/markdown.tsx`). The agent is
  prompted for a bounded subset — headings (`##`/`###`), lists, emphasis, inline
  code, fenced code blocks, GFM tables, blockquotes — and no raw HTML or images.
  A client that renders it must treat it as untrusted model output: no raw-HTML
  plugin, and dangerous URL protocols stripped. `explanation` (inside `attempt`)
  may carry **inline** Markdown only. The Quick check's `stem` and `options` are
  plain text.
- **A ` ```mermaid ` fence is a diagram**, at most one per lesson. The renderer
  (`components/mermaid.tsx`) loads mermaid lazily, draws at
  `securityLevel: "strict"`, and falls back to showing the chart source as a code
  block when it doesn't parse — model-written mermaid is often subtly invalid, so
  a client that renders diagrams needs that fallback. The prompt also requires the
  surrounding prose to explain the diagram, so a client that renders it as plain
  source loses nothing but the picture.
- **Answer-hiding (W6, TDD §6).** `quick_check` carries **only** `stem` +
  `options` — never the keyed answer. `correct_index` and `explanation` live
  **only** inside `attempt`, which is `null` until the learner records an
  Attempt. So a pre-Attempt payload contains no correct answer anywhere; grading
  is server-side.

**`POST /api/v1/lessons/{id}/attempt` response (`AttemptResult`).**

```json
{ "selected_index": 0, "outcome": "correct|incorrect",
  "correct_index": 2, "explanation": "…" }
```

`selected_index` is the recorded (first-wins) Attempt's index; `outcome` is
re-derived deterministically from it (`domains/grading`). An incorrect Attempt
still reveals `correct_index` + `explanation` (formative, non-gating).

**Known dead-end: completing past a failed head.** Completion gates on the
**unlock** axis only (§6) and is orthogonal to generation, so an *available* but
`failed` (or `ungenerated`) lesson **can** be completed (`200`). Doing so advances
`first_incomplete` past a lesson whose generation never succeeded. Because the
serial prefetch chain **stops at a real `failed` head** (§5.4) and retry only
re-runs the current chain head, the skipped-over lesson and every successor then
stay ungenerated — `POST .../generate` on a successor returns `202` but no-ops
(it is not the chain head, and the head is a real failure the auto-walk will not
burn). This is spec-conformant, not a bug: recovery is to retry the **failed head
itself** (`POST /lessons/{failed-id}/generate`), which re-claims it and, on
success, resumes the chain. A future ticket may make completion refuse (or warn
on) an ungenerated head; today the gating is deliberately generation-agnostic.

## Tutor (`/api/v1`, AL-220/AL-221, Phase 2 TDD §5.4/§6)

Session-cookie protected (`401` via the shared envelope when anonymous). Address
by UUID; another learner's path or message reads/acts as `404` (existence not
disclosed) — Phase 1's conventions verbatim.

**The whole surface is feature-flagged.** Every route below sits behind a
router-level `require_tutor_enabled` dependency: when the `tutor` flag (see
*Feature flags* below) resolves **off** for the caller, the route answers `404`
— for that account the tutor does not exist. `404`, not `403`, for the same
reason ownership failures are: `403` would confirm the feature exists. The gate
runs *after* authentication, so an anonymous request is still `401`. This is
what let Phase 2 merge and deploy dark while admins (`ADMIN_DEFAULT_FLAGS`)
dogfooded it in production; AL-270 launched it by flipping the code default on,
and the gate now only fires for an account with an explicit `off`.

| Method | Path | Body | Success | Notes |
| ------ | ---- | ---- | ------- | ----- |
| `POST` | `/api/v1/paths/{id}/conversation/messages` | `{lesson_id, content, source?, model?}` | `200 text/event-stream` | **Send a turn** and stream the reply (§5.4). `content` ≤ 2000 chars; `source` is `typed` (default) or `suggestion` — a suggestion sends as if typed. `model` is the **admin-only per-message model override**: `403 forbidden` for a non-admin (checked *before* the allowlist, so a non-admin never learns its shape), `422 validation_error` off the shared `MODEL_ALLOWLIST`, resolved per request and **persisted nowhere**. Pre-stream failures are ordinary JSON error envelopes: `401`, `404` (path not the caller's, or the lesson not on this path), `409 conflict` (the lesson has no generated content — lesson scope is empty until a Read passage exists; or a reply is already in flight on this conversation), `422`, `429 rate_limited` (the tutor daily cap, `RATE_LIMIT_TUTOR_MESSAGES_PER_DAY` — **disabled at its default of 0**, so `429` is unreachable on the default configuration; admins are exempt regardless). **SSE starts only once the turn is admitted**, so a `200` means the turn is running, not that it succeeded. |
| `GET` | `/api/v1/paths/{id}/conversation` | — | `200 {messages: [...]}` | The path's whole thread, oldest first. Each message: `{id, role, content, lesson_id, lesson_title, tutor_check, created_at}`. **`200` with an empty list when no conversation exists** (the row is created lazily on the first completed turn) — never `404`. There is one conversation **per path**, not per lesson, so `lesson_id`/`lesson_title` (the lesson the message was asked in, PRD §5.8) vary down a single thread. **Unpaginated this phase** (accepted risk, TDD §14). |
| `DELETE` | `/api/v1/paths/{id}/conversation` | — | `204` | **New conversation** (PRD §5.8): drops the conversation row; `ON DELETE CASCADE` removes its messages. **Idempotent** — clearing an already-empty thread is still `204`. Touches no Phase 1 state (the path and its lessons are untouched). Never refunds quota (D8 — the tutor cap is disabled at its default of 0, so there are no usage rows to refund). |
| `POST` | `/api/v1/messages/{id}/tutor-check-answer` | `{selected_index}` | `204` | Records the learner's choice into the message's Tutor check as `answered_index`, so a revisit renders the revealed card. Ownership walks message → conversation → path → user (`404` otherwise). A message with **no** Tutor check is `409 conflict`; a `selected_index` outside the stored `options` is `422 validation_error`. Answering twice **overwrites** (a Tutor check is not graded, so there is no first-wins rule to protect). **Creates no Attempt** and changes no lesson, progression, or Attempt-derived metric (PRD §5.5 / W12). |

### The send endpoint's event stream (§5.4)

The response is `text/event-stream` with `Cache-Control: no-store` (and
`X-Accel-Buffering: no`, so a buffering reverse proxy cannot silently turn
progressive rendering back into a blocking reply). Read it with `fetch` +
`ReadableStream` — **not** `EventSource`, which cannot POST or carry a body.

| Event | Data | When |
| ----- | ---- | ---- |
| `delta` | `{text}` | Each streamed fragment of the reply's Markdown. Concatenated in order, the deltas *are* the reply. |
| `tutor_check` | `{stem, options, correct_index, explanation, answered_index}` | The tutor posed a Tutor check (`answered_index` is always `null` here). Emitted mid-stream the moment the posed payload passes the agent's validator — a rejected call delivers nothing. At most one per turn, and it may land before, between or after `delta` frames (the tutor is told to keep writing around it), so a client should attach it to the message rather than to a position in the text. |
| `done` | `{learner_message_id, tutor_message_id}` | Terminal **success**: the turn is persisted. Both ids, because a turn is a unit — the tutor id is what a Tutor-check answer is posted to. |
| `error` | `{code, message}` | Terminal **failure**: nothing is persisted. `code` is `timeout`, `upstream_error` or `internal_error`; `message` is learner-facing copy that never blames the reader's connection. |
| *(comment)* | `: ping` | Every `SSE_HEARTBEAT_SECONDS` (15s) of model silence, so a proxy idle-timeout never kills a healthy stream. Ignore it. |

Exactly one terminal event ends every stream the client is still reading — a
hung provider ends in `error`, never in a dead stream. (A stream the learner
stops is the client aborting the request; nothing terminal is sent, because
there is nobody left to send it to.)

`TUTOR_REPLY_TIMEOUT` (90s) bounds the **model run**, not the wall clock from
`POST`: a reply first takes a permit from the tutor's own semaphore
(`MAX_CONCURRENT_TUTOR_REPLIES`, default 8, deliberately separate from
generation's), and that queue wait is not charged against the budget. Under a
burst a send therefore waits rather than failing — the only pre-stream refusals
are the ones tabulated above — and a slow reply's total time can exceed 90s.
`: ping` comments cover the whole wait, queueing included.

**A turn exists whole or not at all** (TDD D2). The learner message and the tutor
reply are written in one transaction when the reply settles; a failed, timed-out,
stopped or disconnected stream persists **nothing**, and the client discards its
partial text too. Stop is simply aborting the request — there is no stop
endpoint, and no reply state to reconcile afterwards.

**One reply at a time per conversation** (D9). A second send while one is in
flight is `409 conflict` before any stream opens.

**`tutor_check` payload.** On a tutor message that posed one (`null` everywhere
else): `{stem, options, correct_index, explanation, answered_index}`.
`answered_index` is `null` until the check-answer route records one.

**A deliberate asymmetry with the Quick check.** `QuickCheckDTO` hides
`correct_index`/`explanation` until an Attempt exists — Phase 1's answer-hiding
invariant (W6). `TutorCheckDTO` **carries** both on delivery, by design: a Tutor
check is the tutor's own non-scoring question, its feedback is immediate and
client-side, and nothing downstream grades it. The invariant protected is the
*Quick check's* answer, and that protection is behavioural (TDD D7 — prompt
rule, deterministic pre-filter, W13), not a property of this DTO.

## Shaping (`/api/v1`, AL-320, Phase 2B TDD §5.4/§6)

The **shaping rail**'s surface: a second conversation on the same path, about
the path itself rather than about a lesson. Session-cookie protected (`401` via
the shared envelope when anonymous); address by UUID; another learner's path
reads/acts as `404` (existence not disclosed) — Phase 1's conventions verbatim,
and the transport is the tutor's §5.4 stream **plus one named event**.

**The whole surface is feature-flagged.** Every route below sits behind a
router-level `require_shaping_enabled` dependency: when the `shaping` flag (see
*Feature flags* below) resolves **off** for the caller, the route answers `404`
— for that account shaping does not exist. It is a **separate** key from
`tutor`: the two surfaces launch and can be killed independently (the in-lesson
tutor's launch was AL-270, shaping's AL-370). Both are now launched and default
on; shaping spent 2B's build-out dark behind this key while admins dogfooded it —
see [*Launching a flagged phase*](deploy.md#launching-a-flagged-phase-al-270--al-370).

**Two threads, one path.** A path carries at most one conversation of each kind
(`UNIQUE (path_id, kind)`). The routes below reach only the `shaping` one, and
2A's `/paths/{id}/conversation` routes reach only the `lesson` one — neither
rail ever shows the other's turns, and clearing one leaves the other untouched.
Shaping messages are **path-level**, so they carry no `lesson_id`/`lesson_title`
at all (the column is `NULL`; migration `0006`).

| Method | Path | Body | Success | Notes |
| ------ | ---- | ---- | ------- | ----- |
| `POST` | `/api/v1/paths/{id}/shaping/conversation/messages` | `{content, source?, model?}` | `200 text/event-stream` | **Send a shaping turn** and stream the reply (§5.4). No `lesson_id` — shaping is about the path as a whole. `content` ≤ 2000 chars; `source` is `typed` (default) or `suggestion`. `model` is the **admin-only per-message override** binding the `MODEL_SHAPER` slot: `403 forbidden` for a non-admin (checked *before* the allowlist), `422 validation_error` off the shared `MODEL_ALLOWLIST`, resolved per request and **persisted nowhere**. Pre-stream failures are ordinary JSON error envelopes: `401`, `404` (path not the caller's, or the flag is off), **`409 conflict` when the path is not `ready`** (there is no structure to shape yet — PRD §5.1, server-enforced) or when a reply is already in flight on this shaping conversation, `422`, `429 rate_limited` (`RATE_LIMIT_SHAPING_MESSAGES_PER_DAY` — **disabled at its default of 0**, so `429` is unreachable on the default configuration; admins are exempt regardless). **SSE starts only once the turn is admitted.** |
| `GET` | `/api/v1/paths/{id}/shaping/conversation` | — | `200 {messages: [...]}` | The path's whole shaping thread, oldest first. Each message: `{id, role, content, proposal, created_at}` — **no lesson fields**. `proposal` is non-null only on a tutor message that made one, and carries the stored payload plus its **derived** `resolution` (below). **`200` with an empty list when no conversation exists** (created lazily on the first completed turn) — never `404`. Readable on a non-`ready` path: the `ready` rule bounds *sending*. **Unpaginated this phase** (accepted risk, TDD §14). |
| `DELETE` | `/api/v1/paths/{id}/shaping/conversation` | — | `204` | **New conversation** (PRD §5.8): drops the shaping conversation row; `ON DELETE CASCADE` removes its messages. **Idempotent**. Touches no Phase 1 state, and does not touch the in-lesson thread. **The Change history survives it** — `path_changes` hangs off the path and its `message_id` is `ON DELETE SET NULL`, so clearing the thread nulls the reference and keeps every row (an applied Change is real path structure; "new conversation" is not "undo everything"). Never refunds quota (the shaping cap is disabled at its default of 0, so there are no usage rows to refund). |

### The shaping stream (§5.4)

Identical to [the tutor's](#the-send-endpoints-event-stream-54) — same headers,
same `delta` / `done` / `error` frames, same `: ping` heartbeat, same "exactly
one terminal event", same `TUTOR_REPLY_TIMEOUT` semantics — **plus** one event.
Shaping replies also share the tutor's concurrency pool
(`MAX_CONCURRENT_TUTOR_REPLIES`) and its timeout (TDD D11: both are a learner
waiting mid-sentence), while keeping their own one-in-flight lock per
conversation, so a shaping reply and an in-lesson reply can run at once on one
path.

| Event | Data | When |
| ----- | ---- | ---- |
| `proposal` | `{operations, summary}` | The shaper called `propose_path_edit` and the payload passed validation. The data is the **bare validated payload** — no `resolution` (a proposal just made is pending). At most one per turn, emitted the moment the tool call is accepted, so it may land before, between or after `delta` frames: attach it to the message, not to a position in the text. A call the validator rejected delivers nothing. |

**A turn exists whole or not at all.** The learner message and the reply — and
the Proposal payload on the reply's row — are written in one transaction when
the reply settles; a stream that fails, times out, is stopped or disconnects
**before the reply settles** persists nothing. After it settles the turn is
committed, so a client that disconnects between that commit and the `done` frame
finds the whole turn — Proposal included — on its next read of the thread. Either
way there is never half a turn.

**Proposal payload.** `{operations, summary}`, where each operation is exactly
one of two shapes (the closed vocabulary, TDD D1):

- **Addition** — `{insert_at_position, lessons: [{title}], new_unit: {title, summary} | null, rationale, estimated_minutes}`
- **Revision** — `{lesson_id, instruction, new_title | null, rationale}`

Validated server-side by pure predicates shared with the evals: additions land
at or after the learner's first non-engaged position, revisions name an
unengaged lesson on this path, titles are non-empty and distinct, and the whole
proposal stays inside `MAX_LESSONS_PER_PROPOSAL` and `MAX_LESSONS_PER_PATH`.

**Persisting a Proposal is not applying one.** A stored proposal changes no path
structure — no unit, no lesson, no progress — until the learner taps **Apply**
below.

**`resolution` is derived, never stored** (TDD D3). On the conversation read
each proposal reports one of:

| Value | Meaning |
| ----- | ------- |
| `pending` | No change references it and nothing has superseded it — the card still offers **Apply**. |
| `applied` | A live `path_changes` row references it. |
| `undone` | That change was undone. |
| `superseded` | A *later* proposal in the thread was applied and this one no longer validates against live path state. |

**A declined edit is an ordinary turn.** An ask outside the two-shape vocabulary
(remove, reorder, touch engaged work) gets a plain reply saying so and naming
what shaping can do — no `proposal` event, no payload, and **no machine-readable
marker of any kind**. The same is true of a safety refusal. The whole record of
either is the text the learner read.

### Apply, Undo & the Change history (AL-321, TDD §5.6–§5.8)

The write half of the shaping surface, on the same router and behind the same
`shaping` flag. **Apply is the only write path into path structure outside
Phase 1's generation pipeline**, and only from a stored, re-validated Proposal on
an explicit learner tap — never inferred from conversation text.

| Method | Path | Body | Success | Notes |
| ------ | ---- | ---- | ------- | ----- |
| `POST` | `/api/v1/messages/{id}/apply-proposal` | — | `200 {change, path}` | **Apply** the Proposal on a shaping message. Ownership walks message → conversation → path → account; a message that is not the caller's, is not a **shaping** message, or carries no proposal is a plain `404`. The whole payload is **re-validated against live path state** first (below), then applied in **one transaction** under a per-path lock — the path is never half-changed. Added lessons are inserted `ungenerated` and generate through Phase 1's untouched pipeline; a revised lesson is snapshotted, cleared and reset to `ungenerated` with its instruction. `path` is exactly what `GET /paths/{id}` returns, so the rail swaps its ghost rows for real rows in one round trip; requesting it also kicks the prefetch driver. Every refusal is a coded `409` (below). |
| `POST` | `/api/v1/changes/{id}/undo` | — | `204` | **Undo** a Change, restoring the path exactly: added rows deleted, positions unshifted, revised lessons restored byte-identical (passage, Quick check, title, `generated_at`, state `generated`, instruction cleared). `status` becomes `undone` — undo is never a delete. Ownership walks change → path → account (`404` otherwise). **Undo is last-in-first-out**: only the newest *live* Change on a path may be undone, and an older one is `409 not_latest` until the Changes above it are undone first (below). The **engagement re-check is server-side and is the rule**: a Change whose content the learner has met is `409 engaged` and is permanent history. Undo never touches progress. |
| `GET` | `/api/v1/paths/{id}/changes` | — | `200 {changes: [...]}` | The **Change history**, newest first: `{id, summary, kinds, status, applied_at, undone_at}`. Read-only — a record, not a second edit surface — and **undone Changes are included**. `200` with an empty list when nothing has shaped the path, and readable on a non-`ready` path. Scoped by *path*, so it survives **new conversation** (the rows outlive the thread that produced them). |

**A Change is applied when the structure lands, not when generation finishes.**
Added and revised lessons then ride Phase 1's `ungenerated → generating →
generated` states, its retries and its caps, exactly like path creation.

**One Apply is one Change** — the unit of history *and* of undo. A Proposal that
mixes an Addition with a Revision lands as a single row whose `kinds` lists both,
because undoing half of what the learner consented to as one edit would leave the
path in a shape nobody proposed.

**Coded conflicts.** Every refusal is an ordinary `409 conflict` in the shared
envelope, carrying `details.reason` beside a learner-facing `message` so the
proposal card can render the right state and the right affordance. A Proposal
going stale is normal (the learner chats, walks away, starts the target lesson,
comes back and taps), so this is a first-class path, not an error corner.

| `details.reason` | Applies to | Meaning / what the card offers |
| ---------------- | ---------- | ------------------------------ |
| `already_applied` | apply | A live change row already references this proposal. Nothing to do. |
| `already_undone` | apply | This proposal was applied and then undone; ask again to redo it. |
| `not_applied` | undo | The Change is already undone. Idempotent-friendly. |
| `not_latest` | undo | A later live Change was applied on top of this one. **Undo the newest one first** — nothing is wrong with this Change, it is simply not on top of the stack. |
| `path_cap_reached` | apply | The path no longer has room under `MAX_LESSONS_PER_PATH`. Ask again. |
| `insert_position_taken` | apply | The insertion point is now before the learner's first non-engaged position, or past the end of the path. Ask again. |
| `revision_target_engaged` | apply | The revision target has been started since — or is no longer on this path. Ask again. |
| `title_conflict` | apply | A proposed title now collides with one already on the path. Ask again. |
| `positions_shifted` | apply | A Change applied *after* this proposal moved the slot its positions named. The payload is still well formed; it just no longer means what the learner was shown. Ask again. |
| `invalid_proposal` | apply | The payload no longer satisfies the shared predicates for some other reason. Ask again. |
| `target_generating` | apply, undo | A revision target is being written right now (a prefetch holds the claim). **Retryable** — the same tap works in a moment. |
| `engaged` | undo | The learner has started something this Change created or revised, so undo is closed and the Change is permanent history. |

The stale reasons are labels on the **shared** rulebook, never a second one: the
predicates that drafted the proposal decide *whether* it is still valid, and the
reason only names which rule fired. `positions_shifted` is the one apply-time
check that is not a predicate — it asks whether the recorded `insert_at_position`
still names the slot the learner saw, which a payload can fail while remaining
perfectly in bounds. It fires on any structural shift **since the proposal was
made**, at or below the last position the payload names: an Apply *and* an Undo
both move positions, and both count.

**Why undo is LIFO.** A Change stores its inverse as *absolute* positions,
recorded against the path as it stood when that Change was applied. Replaying
them against a path a later Change has since moved is wrong in two ways — it can
collide with the later Change's slot, and (worse, because it is silent) it can
reorder the later Change's lessons around a lesson the learner placed them
against. Nothing in the payload relates the two Changes' coordinate frames, so
the restriction is the correctness boundary rather than a simplification. It
costs nothing PRD §5.5 promises: a Change stays undoable until it is engaged, by
undoing the ones above it in turn.

## Progress (`/api/v1`, Phase 5 TDD §5.4/§6)

The Streaks slice's whole API: one read-only route folding the global **Daily
streak**, the 49-day activity strip, and the per-path **Path streak** breakdown
into a single payload (TDD D4) — a `GROUP BY` over `lessons.completed_at`
(TDD D1), nothing stored, nothing written. Session-cookie protected (`401` via
the shared envelope when anonymous); scoped to the caller's own completions by
the query's own `paths.user_id` predicate, so there is no ownership parameter to
get wrong.

**The whole surface is feature-flagged.** The single route sits behind a
router-level `require_streaks_enabled` dependency: when the `streaks` flag (see
*Feature flags* below) resolves **off** for the caller, it answers `404` — for
that account the surface does not exist. It shipped dark, the same playbook
`tutor`/`shaping` used — admins dogfooding it in production while it was off for
everyone else — and is now **launched**: the code default is on, so the gate is
open for every learner and the flag is a kill switch.

| Method | Path | Query | Success | Notes |
| ------ | ---- | ----- | ------- | ----- |
| `GET` | `/api/v1/progress/summary` | `tz_offset_minutes` (optional, default `0`, `-900..900`) | `200 {today, current_streak, best_streak, completed_today, activity, paths}` | The learner's whole streak snapshot. `tz_offset_minutes` is the client's `getTimezoneOffset()` value **verbatim** — minutes to *subtract* from UTC to reach local time, so a zone ahead of UTC sends a negative number. Out of range is `422 validation_error`. |

```jsonc
{
  "today": "2026-08-02",
  "current_streak": 5,
  "best_streak": 12,
  "completed_today": 1,
  "activity": [                       // exactly STREAK_ACTIVITY_WINDOW_DAYS (49)
    { "date": "2026-06-19", "count": 0 },   // entries, oldest first, zero-filled
    { "date": "2026-06-20", "count": 2 }
  ],
  "paths": [                          // paths with at least one completion;
    { "path_id": "…", "current_streak": 3, "best_streak": 7, "completed_today": 1 }
    // absent means zero — a path with no completions is not listed at all
  ]
}
```

**A day is the learner's local calendar day** (PRD §4.1). The server derives it
from `tz_offset_minutes` after pinning `completed_at` to UTC first
(`(completed_at AT TIME ZONE 'UTC') - make_interval(mins => tz_offset_minutes)`,
TDD D3) — casting a `timestamptz` to `date` directly would resolve in whatever
the database session's `TimeZone` happens to be, which nothing in this codebase
sets or asserts, so the UTC pin is what makes the day boundary independent of
server configuration.

**The current streak does not break at midnight** (PRD §4.4): it is the run of
consecutive days ending today, **or yesterday if today has no completion yet**.
`best_streak` is the longest run ever, all-time (not windowed to the 49-day
strip) — it can exceed `current_streak`, and the frontend renders it only when
it does.

**`completed_today`** counts today's completions — globally at the top level,
per path inside `paths` — and is what the client's optimistic bump on a
completion keys off (no round trip needed to move the number in the same
interaction).

**Absent means zero.** A path with no completions produces no row in `paths`
(the `GROUP BY` this endpoint is built on cannot manufacture one), which the
frontend already treats identically to "streak below 2 days, no chip shown."
**Deleting a path erases its completion days from the global streak** — the
accepted cost of storing nothing new for this feature (PRD §4.6): there is no
warning on delete, and the behavior is pinned by a test rather than left to be
discovered.

## Flashcards (`/api/v1`, Phase 3 TDD §5.3-§5.6/§6)

Session-cookie protected (`401` via the shared envelope when anonymous). Two
halves of one router: drafting (trigger, poll, keep — CONTEXT.md: *Draft*,
*Kept card*) and review (the queue, its summary, and grading — CONTEXT.md:
*Due*, *Daily queue*, *Review*, *Lapse*).

**The whole surface is feature-flagged, router-level.** Every route here sits
behind a single `flashcards` flag gate (TDD D10): off (the code default —
Phase 3 has not launched) → `404` on every route, before any work, and the
Progress summary's streak silently loses its second signal (§5.5). It ships
the same dark-then-flip playbook `tutor`/`shaping`/`streaks` each took —
admins dogfood it via the admin baseline while every other learner sees
nothing at all.

### Drafting (§5.2)

| Method | Path | Query / Body | Success | Notes |
| ------ | ---- | ------------- | ------- | ----- |
| `POST` | `/api/v1/lessons/{lesson_id}/flashcard-drafts` | — | `202 {id}` | Trigger drafting for a generated lesson (CONTEXT.md: *Draft*) — the client fires this on lesson *open*, not on completion (AL-400), so the cards are usually ready by the time the learner finishes. Idempotent — a second `POST` while the run is `generating`, or once it is `generated`, is a structural no-op (D7): the claim wins at most once, so this is safe to fire from a mutation `onSuccess` React may run twice, or from a mount effect on a route React may also re-run. `409 lesson_not_generated` if `generation_state != 'generated'`; `429` over `FLASHCARD_DRAFTS_PER_DAY`; an unowned/unknown lesson is `404`. |
| `GET` | `/api/v1/lessons/{lesson_id}/flashcard-drafts` | — | `200 {state, cards}` | Poll target. `state` is `"not_started"` (never triggered), `"generating"`, `"generated"` (with every pending draft, creation order), or `"failed"` — retryable by re-`POST`ing the trigger route, rendering the existing retry affordance rather than a dead spinner. Abandoned drafts wait: revisiting a lesson long after the run resolved still re-serves them. |
| `POST` | `/api/v1/lessons/{lesson_id}/flashcard-drafts/keep` | `{kept_ids, tz_offset_minutes}` | `200 {kept_ids}` | Keep the listed drafts (`kept_at = now()`, `rung = 0`, `due_on = today + ladder[0]` — never today, D1); every other pending draft of this lesson is deleted in the same transaction (PRD §3: discarded, not soft-deleted). `kept_ids: []` is "Skip — keep none." A `kept_id` that is not a pending draft **of this lesson** is `404` and mutates nothing. `tz_offset_minutes` is the client's `getTimezoneOffset()` value, same band as everywhere else — the service is the sole owner of "today" for the `due_on` arithmetic. |

```jsonc
// GET /api/v1/lessons/{lesson_id}/flashcard-drafts
{
  "state": "generated",
  "cards": [
    { "id": "…", "front": "What does `extends` mean in `<T extends X>`?",
      "back": "It constrains T — T must be assignable to X." }
  ]
}
```

**Wire codes (drafting):** `401 unauthenticated` · `404 not_found` (flag off;
an unowned/unknown lesson; or, on keep, a `kept_id` that is not a pending draft
of this lesson) · `409 conflict` with `details.reason == "lesson_not_generated"`
(drafting a lesson whose `generation_state` is not yet `generated`) · `429`
over the daily drafting cap.

### Review (§5.3-§5.4)

| Method | Path | Query / Body | Success | Notes |
| ------ | ---- | ------------- | ------- | ----- |
| `GET` | `/api/v1/reviews/summary` | `tz_offset_minutes` (optional, default `0`, `-900..900`) | `200 {today, due_count, estimated_minutes, paths}` | Home's *Due today* card, the app-bar pill, and the per-path chips — one route, its own kill switch, deliberately not folded into `/progress/summary` (D9). `paths` omits any path with no due cards; an orphaned card (D12) counts toward `due_count` but no `paths` row. |
| `GET` | `/api/v1/reviews/queue` | `tz_offset_minutes` (optional, default `0`), `path_id` (optional) | `200 {today, total, completed, scope_path_id, other_due_count, cards}` | The day's cards in serve order. `path_id` filters **display only** — `total`/`completed` are always the **global** selected set's numbers, even in a filtered session (§5.3's invariant); `other_due_count` is non-zero only when `path_id` is set. |
| `POST` | `/api/v1/reviews` | `{card_id, grade, rung_before, tz_offset_minutes}` | `200 {card_id, rung, due_on}` | Grade one card. `grade` is `"again"` \| `"got_it"` — the fixed two-outcome ladder (CONTEXT.md: *Review*). `rung_before` is optimistic concurrency: the client already holds it (it rendered `got_it_interval_days` from it), so a mismatch is `409 stale_rung` rather than a round trip to re-fetch first. |

```jsonc
// GET /api/v1/reviews/queue
{
  "today": "2026-08-04",
  "total": 10,                          // the day's selected set — the `of 10` denominator
  "completed": 3,                       // distinct cards already answered Got it, today
  "scope_path_id": null,
  "other_due_count": 0,                 // > 0 only when scope_path_id is set
  "cards": [                            // unsatisfied only, in serve order
    {
      "card_id": "…",
      "front": "What does `extends` mean in `<T extends X>`?",
      "back": "It constrains T — T must be assignable to X. It is not class inheritance.",
      "rung": 2,
      "got_it_interval_days": 14,       // what the Got it button previews — the *promoted*
                                         // rung's interval (ladder[min(rung + 1, len - 1)]),
                                         // not ladder[rung]
      "path_id": "…",
      "source": {                       // D12 — a discriminated object, never three flat fields
        "kind": "linked",               // "linked" | "degraded"
        "lesson_id": "…",               // absent entirely (not null) when kind == "degraded"
        "lesson_title": "Generic constraints",
        "path_title": "Learn TypeScript"
      }
    }
  ]
}
```

**The queue is derived, never stored** (D3). The day's ten are decided once,
on the first request of the learner's local day, and held stable for the rest
of it: grading a card moves its *live* `due_on` into the future, but the
candidate the `GET` reads is pinned to its **start-of-day** value, so a reload
mid-session never reshuffles the set. `total`/`completed`/`other_due_count`
all derive from that same pinned selection — `GET /reviews/summary` reads the
identical population, reduced to counts, so the pill and the queue can never
disagree about how many cards today holds.

**A lapse (`grade: "again"`) never costs the set a slot** (D8, CONTEXT.md:
*Lapse*): it demotes the card one rung (floor 0) and sets its `due_on` to
**today**, so it re-shows later the same session rather than tomorrow — and
because the selection counts distinct cards, not attempts, a re-shown lapse
is not a new one. Serve order is never-attempted first, then lapses
least-recently-seen first. `got_it_interval_days` (a *Got it* preview) and the
returned `due_on` (after grading) always come from the server's
`FLASHCARD_LADDER_DAYS` — the client holds no second copy of the ladder.

**The citation degrades honestly** (D12, CONTEXT.md: the source line under a
card). `source.kind` is `"linked"` iff the source lesson row still exists
*and* its live `generated_at` still equals the stamp taken when the card was
drafted; otherwise `"degraded"`, carrying only the two titles copied at draft
time. The shapes are genuinely different on the wire — `LinkedCitationDTO`
carries `lesson_id`, `DegradedCitationDTO` has no such field at all — so a
client can never dereference a citation link that does not exist. A path
delete, a Revision, or any regeneration of the source lesson each degrade the
citation the same way; the card itself is untouched and stays fully
reviewable either way.

**Wire codes (review):** `401 unauthenticated` (anonymous) · `404 not_found`
(flag off, before any work; or an unowned/unknown card) · `409 conflict` with
`details.reason == "not_due"` (the card is not part of today's set, or was
already satisfied today) · `409 conflict` with `details.reason ==
"stale_rung"` (a double-tapped grade, or a retry of a request that already
succeeded — absorbed as a no-op rather than a double promotion) · `422
validation_error` (`tz_offset_minutes` out of `[-900, 900]`).

## Feature flags (admin) (`/api/v1/admin`, AL-203)

Flags are **defined in code** (`services/feature_flags.py`); the database stores
only per-user *exceptions*. Resolution order, highest first:

**per-user override > `FEATURE_FLAG_DEFAULTS` > admin default > code default.**

(The module docstring in `services/feature_flags.py` is the authoritative
statement; this section restates it for API readers.) Each step is consulted only
when the one above it is silent — in particular the **admin default applies only
to flags `FEATURE_FLAG_DEFAULTS` does not mention**, so an explicit
`FEATURE_FLAG_DEFAULTS=tutor:off` is a real kill switch that reaches admins too;
an admin who wants to keep dogfooding takes a per-user override, which still wins
over everything.

Keys outside the code registry — a stale `FEATURE_FLAG_DEFAULTS` entry, a row for
a deleted flag — are ignored everywhere, so removing a flag is a pure code change
with no data migration.

Every route below is **admin-only** (email domain in `ADMIN_EMAIL_DOMAINS`;
anonymous → `401`, signed-in non-admin → `403`). Regular learners never call
them: they receive their resolved map on the session probe (below).

| Method | Path | Body | Success | Notes |
| ------ | ---- | ---- | ------- | ----- |
| `GET` | `/api/v1/admin/feature-flags` | — | `200 {flags: [...]}` | Every registered flag, sorted by key: `{key, enabled_default, override_count}`. `enabled_default` is the **global** default (code default with `FEATURE_FLAG_DEFAULTS` applied) — not the admin baseline, which is a property of the reader. `override_count` counts per-user override rows. |
| `PUT` | `/api/v1/admin/feature-flags/{flag_key}/users/{user_id}` | `{enabled}` | `200 {flag_key, user_id, enabled}` | Force the flag on/off for one learner. Upsert: a repeat `PUT` updates in place (never a second row). `404` for an unregistered `flag_key` or an unknown `user_id`. |
| `DELETE` | `/api/v1/admin/feature-flags/{flag_key}/users/{user_id}` | — | `204` | Clear the override so the default applies again. **Idempotent** — clearing an absent override is still `204`. `404` only for an unregistered `flag_key`. |

**Session delivery.** `GET /api/v1/auth/session` carries the signed-in learner's
resolved map as `user.feature_flags` (`{"tutor": false}`), so gating a surface
costs no extra request. The frontend reads it through `useFeatureFlag(key)`
(`lib/feature-flags.ts`), which resolves an unknown or not-yet-loaded key to
**off** — a gate never flashes open before the session lands.

**Registered flags.**

| Key | Code default | Admin default | Purpose |
| --- | ------------ | ------------- | ------- |
| `tutor` | **on** | on (redundantly — the code default already carries it) | The Phase 2 in-lesson tutor — the rail, its API, and its stream. Shipped **dark** at `off` through Phase 2's build-out (epic #82 amendment 1) while admins dogfooded it; **launched at AL-270**, which flipped this code default on. Kill it without a code deploy with `FEATURE_FLAG_DEFAULTS=tutor:off`. |
| `shaping` | **on** | on (redundantly, as above) | Phase 2B shaping — the shaping rail, its API and its stream, and the apply/undo endpoints. Same history on its own key (epic #114, adopted convention 1): dark through 2B's build-out, **launched at AL-370**. Independent of `tutor`, so either can be killed without disturbing the other. |
| `streaks` | **on** | on (redundantly, as above) | Phase 5 streaks — `GET /progress/summary` and everything under it (see [Progress](#progress-apiv1-phase-5-tdd-546)). Same history again on its own key (TDD D7): dark at `off` through the slice's build-out while admins dogfooded it, then **launched** by flipping this code default on, exactly as AL-270/AL-370 did. Kill it with `FEATURE_FLAG_DEFAULTS=streaks:off`. |
| `flashcards` | **off** | on | Phase 3 flashcards — every route under [Flashcards](#flashcards-apiv1-phase-3-tdd-53-56-6) (drafting, the daily queue, grading) and the progress summary's second streak signal (§5.5). This phase's **only** kill switch: one flag gates drafting, the queue, review and the due pill together (TDD D10), because a queue with no drafting is an empty queue and drafting with no queue is a card sink. Dark at `off` through the build-out while admins dogfood it via the admin baseline; launch is a separate `FEATURE_FLAG_DEFAULTS=flashcards:on` change after dogfooding, the same playbook `tutor`/`shaping`/`streaks` each took. |

**Operating it.** `FEATURE_FLAG_DEFAULTS` is a comma-separated list of
`key:on` / `key:off` entries (`FEATURE_FLAG_DEFAULTS="tutor:on"`). Malformed and
unknown entries are dropped rather than raising — it is a knob turned under
pressure, and a typo must not keep the app from booting. Per-user overrides still
win over it, so flipping the global default does not disturb anyone holding one;
the admin baseline does **not** — an entry here is the last word for everyone
without an override.
No product events are emitted for admin flag ops (structlog only): these are
operator actions, not learner behaviour, and PRD §5.7's event set is unchanged.
