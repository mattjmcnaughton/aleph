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
| `POST` | `/api/v1/paths` | `{topic, level}` | `202 {id}` | Creates the path and triggers its outline. Rate-limited (`429 rate_limited` at the daily cap; admins exempt). `topic` is stripped and bounded to 1–500 chars; a violation or an out-of-enum `level` is `422 validation_error`. |
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
