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
| `POST` | `/api/v1/paths` | `{topic, level, model_outline?, model_lesson?}` | `202 {id}` | Creates the path and triggers its outline. Rate-limited (`429 rate_limited` at the daily cap; admins exempt). `topic` is stripped and bounded to 1–500 chars; a violation or an out-of-enum `level` is `422 validation_error`. `model_outline`/`model_lesson` are the **admin model-picker** overrides (AL-052, §5.3/D14) — optional bare OpenRouter ids the picker sends, selected from `MODEL_ALLOWLIST`. Enforced **before** the rate-limit and billed work: an override from a **non-admin** is `403 forbidden`; an override outside `MODEL_ALLOWLIST` is `422 validation_error`. Omitted (the common case) uses the configured slot. The validated choice is **persisted on the path row**, so the DB-driven resume/reconcile (§5.4/D6) re-generates with the chosen model, not the default. |
| `GET` | `/api/v1/paths` | — | `200 {paths: [...]}` | "Your paths" switcher, newest first. Each row: `{id, topic, level, status, progress}`. **`status` is the effective status** (a stale `generating` reads as `failed`), matching the detail poll. |
| `GET` | `/api/v1/paths/{id}` | — | `200` | Poll target. Body: `{id, topic, level, status, refusal_message, progress, units:[{..., lessons:[{..., generation_state, unlock_state}]}]}`. `status` is effective; `refusal_message` is non-null **only** when `status == refused`. Each lesson carries its effective `generation_state` and derived `unlock_state` (the two orthogonal axes). The poll is itself a trigger: it spawns the idempotent resume, so a chain lost to a crash self-heals within one poll. |
| `POST` | `/api/v1/paths/{id}/retry` | — | `202 {id}` | Retry a `failed` outline. Rate-limited by its own daily cap (`check_outline_generation`, reusing `RATE_LIMIT_PATHS_PER_DAY`; admins exempt) → `429 rate_limited` at the cap. **A retry on a terminal path (`ready`/`refused`) or a freshly `generating` one still returns `202` but is a silent no-op** — the claim matches nothing, so nothing is re-run (a refusal is terminal; the learner starts a new topic, §5.5). |
| `DELETE` | `/api/v1/paths/{id}` | — | `204` | Hard-delete; `ON DELETE CASCADE` tears down units, lessons, quick checks, and attempts. Not undoable (UI confirms); doubles as reset. |

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
| `POST` | `/api/v1/lessons/{id}/generate` | — | `202 {id}` | Ensure/retry this lesson's generation (also refills the prefetch window). Rate-limited by the daily lesson cap (`check_lesson_generation`, `RATE_LIMIT_LESSON_GENERATIONS_PER_DAY`, default 100; admins exempt) → `429 rate_limited` at the cap. Chain-head gated (§5.2/§5.5): it *ensures* a reached `ungenerated` lesson and *retries* a `failed` one only when all predecessors are `generated`; a trigger on a `generated` or non-chain-head lesson still returns `202` but only advances the window. |
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
what lets Phase 2 merge and deploy dark while admins (`ADMIN_DEFAULT_FLAGS`)
dogfood it in production; launch is AL-270 flipping the default.

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
| `tutor` | off | **on** (only while `FEATURE_FLAG_DEFAULTS` is silent about `tutor`) | The Phase 2 in-lesson tutor — the rail, its API, and its stream. Phase 2 merges and deploys **dark** behind it (epic #82 amendment 1) while admins dogfood in production; launch (AL-270) is setting `FEATURE_FLAG_DEFAULTS=tutor:on`, no code deploy. |
| `shaping` | off | **on** (only while `FEATURE_FLAG_DEFAULTS` is silent about `shaping`) | Phase 2B shaping — the shaping rail, its API and its stream, and the apply/undo endpoints. Same posture, its own key (epic #114, adopted convention 1): Phase 2B merges and deploys **dark** while admins dogfood it, and launch (AL-370) is setting `FEATURE_FLAG_DEFAULTS=shaping:on`. Independent of `tutor`, so either can be flipped or killed without disturbing the other. |

**Operating it.** `FEATURE_FLAG_DEFAULTS` is a comma-separated list of
`key:on` / `key:off` entries (`FEATURE_FLAG_DEFAULTS="tutor:on"`). Malformed and
unknown entries are dropped rather than raising — it is a knob turned under
pressure, and a typo must not keep the app from booting. Per-user overrides still
win over it, so flipping the global default does not disturb anyone holding one;
the admin baseline does **not** — an entry here is the last word for everyone
without an override.
No product events are emitted for admin flag ops (structlog only): these are
operator actions, not learner behaviour, and PRD §5.7's event set is unchanged.
