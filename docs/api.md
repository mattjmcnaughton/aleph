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
