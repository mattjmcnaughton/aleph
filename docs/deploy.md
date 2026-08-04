# Deploy: Fly.io cutover and runbook

Production is a public Fly.io app (`aleph-prod-mattjmcnaughton`) with Postgres on
[Neon](https://neon.tech/)
(Neon project **`aleph`**, Free tier, pooled endpoint) — TDD
[§13/D3](tdds/phase-1-path-generation.md). Continuous deployment: merge to `main` →
CI green → semantic-release → (in parallel) publish the image to GHCR **and**
`flyctl deploy --remote-only` (Fly builds from the release tag) → Fly's
`release_command` runs Alembic migrations once, on its own machine, before any app
machine takes traffic. GHCR is an artifact mirror; Fly does not pull from it (a
private GHCR package is not pullable by Fly).

The MVP runs a **single** Fly machine (`max_machines_running = 1`). The claim
protocol (§5.4) already tolerates more than one, so this is a cost decision — but
the reconciler loop and the `MAX_CONCURRENT_GENERATIONS` semaphore are both
per-process, so raising the machine count multiplies the effective concurrency
ceiling rather than sharing it.

Local prod-like smoke uses Docker Compose (`just compose-smoke`) and is unrelated to
Fly cutover — see [The Compose smoke](#the-compose-smoke-just-compose-smoke).

## The artifacts

| File | Role |
| ---- | ---- |
| [`Dockerfile`](../Dockerfile) | Three stages: pnpm/Vite frontend build → uv-installed backend venv → slim `production` runtime. The built SPA lands in the package tree at `src/aleph/web/frontend/dist`, which is where `aleph.web.serve.mount_frontend` looks, so one process serves the API and the shell. |
| [`docker/release.sh`](../docker/release.sh) | `alembic upgrade head` with retries. Fly's `release_command`, and the Compose smoke's one-shot `migrate` service. |
| [`fly.toml`](../fly.toml) | App `aleph-prod-mattjmcnaughton`, region `ewr`, `internal_port` 8000, `/readyz` check, single machine, non-secret `[env]`. |
| [`.releaserc.json`](../.releaserc.json) + [`package.json`](../package.json) | semantic-release config and its toolchain (repo root — *not* the frontend manifest). |
| [`.github/workflows/release.yml`](../.github/workflows/release.yml) | semantic-release → GHCR image → `flyctl deploy`, triggered only by a green CI run on `main`. Hand dispatch is a hardcoded dry run. |

## Prerequisites

- Fly account + [flyctl](https://fly.io/docs/flyctl/install/) installed and
  authenticated (`flyctl auth login`).
- Neon project **`aleph`** with a **pooled** connection string, rewritten as below.
- GitHub repo access to add the `FLY_API_TOKEN` Actions secret.
- An Auth0 tenant and a **Regular Web Application** for the production login flow
  (AL-102). Auth0 is the production OIDC provider; Keycloak stays the local and CI
  provider — the app is coupled to neither (TDD §7/D2).
- A custom domain you control (e.g. `aleph.mattjmcnaughton.com`); DNS is managed in
  [mattjmcnaughton/nuage](https://github.com/mattjmcnaughton/nuage), not in a
  registrar UI. Certs need an allocated Fly IP, so create the app and do a first
  deploy **before** DNS.

### The Neon URL rewrite (do this before setting `DATABASE_URL`)

Neon's console hands you a libpq-flavoured URL:

```text
postgresql://neondb_owner:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

Aleph runs SQLAlchemy async + asyncpg, which rejects all three of those details.
Rewrite it:

1. `postgresql://` → `postgresql+asyncpg://` — the plain scheme makes SQLAlchemy
   load **psycopg2**, which is not installed.
2. Prefer the **pooled** hostname (the one containing `-pooler`).
3. `sslmode=require` → `ssl=require` — asyncpg rejects `sslmode`.
4. **Drop `channel_binding=…` entirely** — asyncpg rejects it too.

Final form:

```text
postgresql+asyncpg://USER:PASSWORD@HOST/DB?ssl=require
```

> Unlike habagou, `src/aleph/config.py` does **not** normalize this URL on load.
> There is no safety net: paste the rewritten form, or the release command dies on
> `ModuleNotFoundError: No module named 'psycopg2'` or
> `TypeError: connect() got an unexpected keyword argument 'channel_binding'`.

## Secrets

Everything below is set with
`flyctl secrets set -a aleph-prod-mattjmcnaughton`. Non-secret configuration lives
in `fly.toml` `[env]` and is committed.

### Required

| Secret | Why it is required | Set by |
| ------ | ------------------ | ------ |
| `DATABASE_URL` | Neon pooled URL, rewritten as above. The default (`postgresql+asyncpg://localhost:5432/aleph`) points at nothing in production, so `release_command` and `/readyz` both fail without it. | [AL-101](https://github.com/mattjmcnaughton/aleph/issues/38) |
| `SESSION_SECRET_KEY` | Signs the first-party session cookie. The dev default is published in this repo, so signing production cookies with it yields forgeable sessions — `_enforce_production_auth` in `config.py` rejects both an empty value and the dev default whenever `ENV=production`. | [AL-101](https://github.com/mattjmcnaughton/aleph/issues/38) |
| `OPENROUTER_API_KEY` | Every outline and lesson model call (TDD §5.3/D4). Startup succeeds without it, but generation — the product — cannot run. | [AL-101](https://github.com/mattjmcnaughton/aleph/issues/38) |
| `OIDC_ISSUER` | Auth0 tenant issuer used for OIDC discovery. Non-empty is enforced at startup under `ENV=production`. | [AL-102](https://github.com/mattjmcnaughton/aleph/issues/39) |
| `OIDC_CLIENT_ID` | Auth0 Regular Web Application client id. Non-empty is enforced at startup. | [AL-102](https://github.com/mattjmcnaughton/aleph/issues/39) |
| `OIDC_CLIENT_SECRET` | Auth0 Regular Web Application client secret. Non-empty is enforced at startup. | [AL-102](https://github.com/mattjmcnaughton/aleph/issues/39) |

Four of those six (`SESSION_SECRET_KEY`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET`) are **fail-fast**: a production boot missing any of them raises
at startup rather than serving in a degraded state.
`tests/unit/test_deploy_config.py` pins this table against the guard, so the doc
cannot drift away from the code.

### Optional

| Secret | Effect when unset | Set by |
| ------ | ----------------- | ------ |
| `LOGFIRE_TOKEN` | A clean no-op: `send_to_logfire="if-token-present"` creates no exporter and nothing dials the network. The app works; traces and product events (§9) are simply not exported, and the PRD §7 metric queries in [`queries/logfire/`](../queries/logfire) have nothing to read. | [AL-103](https://github.com/mattjmcnaughton/aleph/issues/40) |

Treat Logfire as a store of user content: Pydantic AI spans include the full
generation conversation (topic, replayed history, model responses). Apply an
appropriate retention and access policy.

### Committed, non-secret (`fly.toml` `[env]`)

`ENV=production`, `LOG_LEVEL`, `LOG_FORMAT`, `SESSION_COOKIE_SECURE=true`,
`OIDC_PROVIDER=auth0`.

There is deliberately no `HOST`/`PORT` here (nor in the image's `ENV`): the `CMD`
hardcodes `--host 0.0.0.0 --port 8000` and nothing reads `Settings.host` /
`Settings.port`, so an entry would look like a binding knob without being one.
`[http_service] internal_port` is the real one.

`ENV=production` is the load-bearing one and is deliberately **not** a secret:
forgetting it would silently *disable* the guards it arms — the `stub` model becomes
unreachable, real auth secrets become mandatory, and the session cookie is forced
`Secure` regardless of the supplied flag.

Everything else in `Settings` (model slots and the admin `MODEL_ALLOWLIST`,
`ADMIN_EMAIL_DOMAINS`, the §14 caps and timings, `OTEL_EXPORTER_OTLP_ENDPOINT`) has a
production-ready default and only needs an `[env]` entry to override it.
`ADMIN_EMAIL_DOMAINS` already defaults to `mattjmcnaughton.com`, which is what makes
derived admin (D14) work for the operator's own Auth0 login with no extra
configuration. `MODEL_JUDGE` is eval-only and is never read on the request path — it
does not belong in production config at all.

## One-time cutover (order matters)

App + secrets must exist before the first deploy, because `release_command` has to
reach Neon on its first run. Custom domain and certs come **after** the first deploy.

### 1. Create the Fly app

Use `apps create` (not `fly launch`) — `fly.toml` is already in the repo:

```sh
flyctl apps create aleph-prod-mattjmcnaughton
```

Confirm `app = 'aleph-prod-mattjmcnaughton'` in `fly.toml` matches.

### 2. Set secrets

```sh
# Required — Neon pooled URL, rewritten per the recipe above.
flyctl secrets set -a aleph-prod-mattjmcnaughton \
  DATABASE_URL='postgresql+asyncpg://neondb_owner:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?ssl=require'

# Required — session signing key and the Auth0 client credentials.
python -c 'import secrets; print(secrets.token_urlsafe(32))'
flyctl secrets set -a aleph-prod-mattjmcnaughton \
  SESSION_SECRET_KEY='<random value above>' \
  OIDC_ISSUER='https://<tenant>.auth0.com/' \
  OIDC_CLIENT_ID='<Auth0 Regular Web Application client ID>' \
  OIDC_CLIENT_SECRET='<Auth0 Regular Web Application client secret>' \
  OPENROUTER_API_KEY='<OpenRouter API key>'

# Optional — trace + product-event export (AL-103).
flyctl secrets set -a aleph-prod-mattjmcnaughton LOGFIRE_TOKEN='<Logfire write token>'
```

On a brand-new app, secrets are often **Staged** until Machines exist
(`flyctl secrets list -a aleph-prod-mattjmcnaughton` says so) and
`flyctl secrets deploy` cannot run yet
("no machines available"). The first-deploy steps below create Machines, which makes
staged secrets live.

### Auth0 configuration (AL-102)

Create an Auth0 **Regular Web Application**. Its callback URL is the public HTTPS
application URL followed by `/auth/callback`:

```text
https://<custom-domain>/auth/callback
```

The app discovers the provider at
`$OIDC_ISSUER/.well-known/openid-configuration`; do not set `OIDC_METADATA_URL` for a
standard Auth0 tenant. Requested scopes default to `openid profile email` — `email`
is not optional, because derived admin (D14) classifies on the email domain.

`POST /auth/logout` clears only aleph's local session; it does not redirect to
Auth0's logout endpoint, so an Auth0 Allowed Logout URL is not required yet.

### 3. First manual deploy

Watch the release machine so migrations succeed before enabling CD.

If secrets are still **Staged** and a deploy failed during `release_command` (the
usual chicken-and-egg: no Machines yet, so secrets never went live), bootstrap the
Machines first:

```sh
# 1) Create Machines without running migrations
flyctl deploy --app aleph-prod-mattjmcnaughton --remote-only --skip-release-command

# 2) Confirm secrets are no longer Staged
flyctl secrets list -a aleph-prod-mattjmcnaughton
flyctl secrets deploy -a aleph-prod-mattjmcnaughton   # only if still Staged

# 3) Full deploy — release_command migrates with DATABASE_URL live
flyctl deploy --app aleph-prod-mattjmcnaughton --remote-only
```

If secrets are already live, one full deploy is enough:

```sh
flyctl deploy --app aleph-prod-mattjmcnaughton --remote-only
```

What to expect:

- Fly runs `release_command` (`/app/docker/release.sh`) on a throwaway machine:
  `alembic upgrade head`, retried up to `ALEPH_MIGRATE_ATTEMPTS` (default 10) times
  because a Neon Free endpoint may be cold.
- After the release succeeds, app machines start and serve on the Fly proxy
  (`https://aleph-prod-mattjmcnaughton.fly.dev`).

```sh
flyctl logs -a aleph-prod-mattjmcnaughton
flyctl releases -a aleph-prod-mattjmcnaughton
curl -fsS https://aleph-prod-mattjmcnaughton.fly.dev/healthz
curl -fsS https://aleph-prod-mattjmcnaughton.fly.dev/readyz
```

### 4. Custom domain + DNS + TLS

Only after a successful deploy, so the app has allocated IPs.

```sh
flyctl certs add <custom-domain> -a aleph-prod-mattjmcnaughton
flyctl certs setup <custom-domain> -a aleph-prod-mattjmcnaughton
flyctl ips list -a aleph-prod-mattjmcnaughton
# If IPv4/IPv6 are missing:
flyctl ips allocate-v4 -a aleph-prod-mattjmcnaughton
flyctl ips allocate-v6 -a aleph-prod-mattjmcnaughton
```

DNS for `mattjmcnaughton.com` is managed in
[mattjmcnaughton/nuage](https://github.com/mattjmcnaughton/nuage) — add records
there. Pick one pattern; do not mix conflicting records for the same name:

| Hostname | Recommended records |
| -------- | ------------------- |
| Apex (`example.com`) | **A** → Fly IPv4 and **AAAA** → Fly IPv6 from `fly ips list` / `fly certs setup`. Prefer A/AAAA over CNAME at the apex unless the provider supports CNAME flattening (ANAME/ALIAS). |
| Subdomain (`aleph.mattjmcnaughton.com`) | **CNAME** → the `*.fly.dev` target shown by `fly certs setup` (for this app, `aleph-prod-mattjmcnaughton.fly.dev`), **or** A/AAAA like the apex. |

Certificate issuance also needs domain validation via at least one of: AAAA pointing
at the app, an `_acme-challenge` CNAME (DNS-01), or a `_fly-ownership` TXT (when
behind a CDN/proxy). Use exactly the values `fly certs setup` shows.

**Cloudflare (orange-cloud proxy):** SSL/TLS mode must be Full or Full (strict) —
Flexible causes redirect loops — and add the `_fly-ownership` TXT.

Then:

```sh
flyctl certs check <custom-domain> -a aleph-prod-mattjmcnaughton
curl -fsSI https://<custom-domain>/readyz
```

Finally, update the Auth0 application's Allowed Callback URL to the custom domain.

### 5. Enable continuous deploy

```sh
flyctl tokens create deploy -a aleph-prod-mattjmcnaughton
# GitHub → Settings → Secrets and variables → Actions → New repository secret
# Name: FLY_API_TOKEN
```

## Ongoing CD

1. Merge to `main` with a [conventional commit](https://www.conventionalcommits.org/).
   `fix` / `feat` / `perf` cut a release and therefore deploy; `docs`, `chore`,
   `refactor`, `test`, `style`, `ci`, `build` do not. **If a change needs to reach
   production, it must be committed as `fix` or `feat`.**
2. CI (`gate`, `integration`, `e2e`, `compose-smoke`) goes green.
3. `release.yml` runs semantic-release: version bump, tag, changelog, GitHub release.
4. In parallel:
   - `image` — builds and pushes `ghcr.io/mattjmcnaughton/aleph:<version>`.
   - `deploy` — checks out that tag and runs `flyctl deploy --remote-only`.
5. Fly's release machine migrates; app machines start.

### Rehearsing a release without deploying

Actions tab → **Release** → *Run workflow*, or:

```sh
gh workflow run release.yml
```

semantic-release prints the next version and the release notes it *would* publish and
stops. **A dispatch can only ever be a rehearsal** — `--dry-run` is hardcoded and there
is no input to flip. That is on purpose: a dispatched real release would ship a commit
that never faced the CI gate, and "please don't tick the box" is not a control. Green
CI on `main` (the `workflow_run` trigger) is the only path that tags, builds, or
deploys.

The rehearsal is doubly fenced. `--dry-run` never reaches semantic-release's publish
step, so the `@semantic-release/exec` plugin never sets `new-release-published`; and
the `image` and `deploy` jobs additionally require `github.event_name ==
'workflow_run'`, which a dispatch cannot satisfy.

If you genuinely need to ship without a qualifying merge, deploy by hand
(`flyctl deploy --app aleph-prod-mattjmcnaughton --remote-only`) — visible and
deliberate, rather than a button that looks like a rehearsal.

The same plan can be computed locally without any credentials — this is version
arithmetic over the commit history, nothing more:

```sh
pnpm install --frozen-lockfile
pnpm exec semantic-release --dry-run --no-ci \
  --branches "$(git rev-parse --abbrev-ref HEAD)" \
  --plugins @semantic-release/commit-analyzer,@semantic-release/release-notes-generator
```

## Launching a flagged phase (AL-270 / AL-370)

Phases 2 and 2B both merged and deployed **dark**: the code went to production
while the routes answered `404` and the surface did not render for anyone whose
`tutor` / `shaping` flag resolved off. Launch is therefore not a deploy of new
code — it is one committed configuration change.

> **Both are now launched, by flipping their code defaults on** (AL-270, AL-370)
> rather than by setting the environment variable — so `fly.toml` says nothing
> about either key, and a laptop, a CI run and production all resolve them the
> same way with no configuration. That is the intended end state for a launched
> phase: the env var is the *override*, not the statement of what is live.
> **Phase 5's `streaks` launched the same way, and Phase 3's `flashcards` then
> did too**, so all four registered flags now default on and the registry
> currently holds nothing dark.
>
> This section stays the reference for the lever itself, now read mainly for
> **turning a flag off** (the kill switch below) and for launching the next phase
> that ships dark.

This section is the whole of it; the ship
tickets ([AL-270](https://github.com/mattjmcnaughton/aleph/issues/97) for the
tutor, [AL-370](https://github.com/mattjmcnaughton/aleph/issues/127) for
shaping) add the pre-flip dogfooding and the PRD §12 release-criteria walk.

**The lever.** Flags are defined in code (`services/feature_flags.py`); the
global default is overridden by the `FEATURE_FLAG_DEFAULTS` environment
variable — a comma-separated list of `key:on` / `key:off` entries — which lives
in [`fly.toml`](../fly.toml) `[env]`, committed and non-secret like the rest of
that block. Resolution order and the full semantics are in
[api.md § Feature flags](api.md#feature-flags-admin-apiv1admin-al-203).

**Launching a dark phase.** Flip its entry in `FLAG_DEFAULTS`
(`services/feature_flags.py`) from `False` to `True`. That is the launch: it
reaches production *and* every developer's laptop and every CI run at once, which
is why it beats setting the env var — one statement of what is live, not two.
`tutor`, `shaping`, `streaks`, and `flashcards` were all launched this way.

Use the `fly.toml` env var instead only when the flip must be **reversible
without a code deploy** — a staged rollout you expect to roll back, or an early
open of a flag still under construction:

1. Edit `fly.toml` `[env]`, adding the key alongside anything already there:

   ```toml
   [env]
     FEATURE_FLAG_DEFAULTS = 'shaping:on'
   ```

2. Commit it as a **`fix`** (`fix(config): launch shaping`) — `chore`/`docs`
   cut no release and therefore never deploy (see *Ongoing CD* above). No code
   change, no migration, no image rebuild beyond the ordinary release.
3. Wait for the release job's `deploy` step, then verify as a **non-admin**
   account (the realm's `dev` user in staging, a real learner account in
   production): `GET /api/v1/auth/session` carries `user.feature_flags.shaping =
   true`, and the shaping mark is on a `ready` path's view.

**What `shaping:on` makes visible.** The whole Phase 2B surface, for every
learner: the shaping rail on the path view (its floating mark, its own
conversation, the four suggestions), the six shaping routes — the conversation
read/send/clear, `POST /messages/{id}/apply-proposal`, `POST /changes/{id}/undo`
and `GET /paths/{id}/changes` — stop answering `404`, and Apply becomes reachable,
which is the only write path into path structure outside generation. Learners with
no `ready` path see nothing new (sending is `409` until the outline exists).
Nothing about the in-lesson tutor changes: the keys are independent, and the two
can be flipped or killed separately.

**Turning it off again** is the same edit with `shaping:off` and one more
release. Note the asymmetry that makes it a real kill switch: an **explicit**
entry beats the admin baseline, so `shaping:off` reaches admins too (silence is
what leaves admins on). An admin who still needs to dogfood takes a per-user
override — `PUT /api/v1/admin/feature-flags/shaping/users/{user_id}` — which
wins over everything.

## Logfire saved queries (import checklist)

[`queries/logfire/`](../queries/logfire) is the source of truth for the PRD §7
metrics ([metrics.md](metrics.md) maps each file to its metric, and
`tests/unit/test_metrics_queries.py` keeps the two honest). Logfire has no
import API here: each file is **pasted in by hand** as a saved query, then
pinned as a dashboard tile. Do it once per phase, at that phase's launch, so a
panel is never reading a window the feature was dark for.

| Phase | Import | Files |
| ----- | ------ | ----- |
| 1 (AL-103) | 9 | `activation_rate` (north star), `first_lesson_activation`, `path_start_rate`, `continuation`, `return_rate`, `breadth`, `cost_per_path`, `generation_failure_latency`, `quick_check_correctness` |
| 2 — tutor (AL-240) | 8 | `tutor_assisted_continuation` (primary), `tutor_adoption`, `tutor_repeat_use`, `tutor_depth`, `tutor_entry_mix`, `tutor_check_uptake`, `tutor_completion_guardrail`, `tutor_reply_failure_latency` |
| 2B — shaping (AL-340) | 8 | `shaping_yield` (primary), `shaping_adoption`, `proposal_acceptance`, `edit_shape_mix`, `undo_rate`, `depth_to_proposal`, `shaped_path_completion_guardrail`, `shaping_reply_failure_latency` |

Two things to expect on the shaping set:

- **`shaping_yield` reads empty for the first week after the flag flip.** It is
  the phase's primary panel and it clamps to *closed* 7-day windows (a Change
  applied an hour ago cannot have been engaged with yet, and would drag the
  metric down exactly while adoption climbs — `activation_rate.sql`'s call,
  made again). Deleting the single clamp line
  (`AND start_timestamp < now() - INTERVAL '7 days'`) in a scratch copy is the
  deliberate way to read that first week; the saved tile keeps the clamp.
- **Read the two reply-latency panels side by side.**
  `shaping_reply_failure_latency` is `tutor_reply_failure_latency` column for
  column because both rails share one permit pool — a rise in both is the pool,
  a rise in one is that rail.

`LOGFIRE_TOKEN` must be set for any of this to have data (see *Optional*
secrets above).

## Manual redeploy / rollback

```sh
# Redeploy a checked-out tag (Fly rebuilds)
git checkout vX.Y.Z
flyctl deploy --app aleph-prod-mattjmcnaughton --remote-only

# Or redeploy a previous Fly-built image (from `fly releases --image`)
flyctl deploy --app aleph-prod-mattjmcnaughton \
  --image registry.fly.io/aleph-prod-mattjmcnaughton:<deployment-tag> \
  --remote-only

flyctl releases -a aleph-prod-mattjmcnaughton
flyctl logs -a aleph-prod-mattjmcnaughton
```

## The Compose smoke (`just compose-smoke`)

The local stand-in for "the artifact works", and the one thing that runs the image Fly
runs — everything else (`just gate`, `test-integration`, `test-e2e`) runs the app from a
source checkout. It also runs in CI as the `compose-smoke` job ([ci.md](ci.md)).
**This section is the description; other docs link here rather than repeat it.**

What it does, in order:

1. `docker build --target production` — the multi-stage frontend + backend build.
2. Brings up `smoke-db` (a throwaway Postgres) and runs the one-shot `migrate`
   service, which executes `docker/release.sh` — the *same* script `fly.toml` uses as
   its `release_command`.
3. Waits (`docker compose up --wait`) for `migrate` to exit 0 and the app's healthcheck
   to pass, then asserts five things over HTTP: `/healthz`, `/readyz` (a real query
   against the migrated database), the SPA shell (`<div id="root">`) served by the same
   process, and the auth boundary in both directions — an unauthenticated session
   endpoint that answers, and a protected endpoint that refuses.
4. **Streams a tutor turn** (Phase 2 TDD §12) — the smoke's one authenticated check.
   It seeds an account, a path and a generated lesson *inside the container* through
   the app's own models, signs a session cookie with the stack's
   `SESSION_SECRET_KEY`, and POSTs `/api/v1/paths/{id}/conversation/messages`.
   Keycloak stays down: bringing an identity provider up to test a transport would
   be a much bigger stack for no more signal.
5. Tears down its own containers and their anonymous volumes, dumping `migrate` and
   `app` logs first.

### Why the smoke streams a tutor turn

Streaming is Phase 2's headline operational risk: a buffering reverse proxy or a
stripped header turns progressive rendering back into a blocking reply, with no
error anywhere to show for it. Everything below the HTTP boundary is covered by the
integration suite against a real ASGI app; what only the image can prove is that
`text/event-stream`, `Cache-Control: no-store`, `X-Accel-Buffering: no` and a
**flushed** event survive uvicorn and the port publish — so the smoke asserts those
headers, that the response is `Transfer-Encoding: chunked` with **no**
`Content-Length` (the signature of a buffered whole-body response), and that a
well-formed SSE frame arrived.

The stack ships **no** `OPENROUTER_API_KEY` (the smoke must never spend money) and
`ENV=production` forbids the deterministic stub, so the model call fails at once and
the frame that arrives is `event: error`. That a terminal failure still reaches the
learner *as a stream event* is exactly TDD §5.6's promise — never a dead stream — so
it is a sound thing to assert; the check is written against the frame's shape, not
its contents, so it keeps holding if the stack ever gains a real model.

The smoke also sets `FEATURE_FLAG_DEFAULTS=tutor:on`, because Phase 2 ships dark and
the tutor routes answer `404` while the `tutor` flag is off. That is the switch in
front of the production configuration, not part of it — the same lever AL-270 pulls
at launch (see [Launching a flagged phase](#launching-a-flagged-phase-al-270--al-370)).
`shaping` is deliberately left off there: the one authenticated check is a tutor turn,
and the transport it proves is the same stream both rails use.

Both `migrate` and `app` boot with `ENV=production` (a shared YAML anchor, so they
cannot drift), which arms the config guards — no `stub` model, real auth secrets
required, `Secure` cookie forced. That matters on the migration leg too: `alembic/env.py`
imports `aleph.config.settings`, and Fly's release machine carries the app's
environment, so a migrate service without `ENV=production` would test the one thing
production does differently.

**It cannot touch your data.** The smoke uses its own `smoke-db` service with no named
volume and no published port — not the `db` service `just dev` and `just compose-db-up`
use, which owns the `pgdata` volume your local database lives in. Teardown is scoped to
the smoke's three containers, so neither `pgdata` nor a running `keycloak` is disturbed,
and the machine's own Postgres on 5432 is never involved. Set `ALEPH_APP_PORT` if
something else is holding 8000 (the only port the smoke publishes).

|  | Compose smoke | Fly app machines | Fly release machine |
| - | ------------- | ---------------- | ------------------- |
| Migrations | one-shot `migrate` service (`docker/release.sh`) | no | yes (`release_command`) |
| Database | throwaway `smoke-db`, Compose-network only, discarded on teardown | Neon | Neon |
| OIDC | configured but never contacted (discovery is lazy; the one authenticated check signs its own session cookie) | Auth0 | n/a |
| `ENV` | `production` — on purpose, so the guards are exercised | `production` | `production` |

## Troubleshooting

| Symptom | What to check |
| ------- | ------------- |
| `ModuleNotFoundError: No module named 'psycopg2'` during migrate | `DATABASE_URL` still uses Neon's `postgresql://…`. Rewrite to `postgresql+asyncpg://…`. |
| `TypeError: … unexpected keyword argument 'sslmode'` / `'channel_binding'` | Neon libpq params. Use `?ssl=require` only — drop both. |
| Startup raises "Production (ENV=production) requires real auth secrets" | One of `SESSION_SECRET_KEY` / `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` is unset, empty, or still the dev default. |
| Startup raises "The 'stub' model is not allowed in production" | A `MODEL_*` slot or `MODEL_ALLOWLIST` entry is `stub`. The deterministic CI model must never be reachable in production (D9). |
| Release fails on migrate / `Connect call failed` | Neon cold start (the retry budget is `ALEPH_MIGRATE_ATTEMPTS`), wrong host/password, or secrets still **Staged** on a first deploy — use `--skip-release-command`, then a full deploy. |
| `fly secrets deploy`: no machines available | Expected before the first Machine-creating deploy. See §3. |
| Login redirects to `http://` and fails | The proxy headers flags in the image `CMD` were lost, or `SESSION_COOKIE_SECURE` is not `true`. |
| Path creation returns 503 | `OPENROUTER_API_KEY` unset — the rest of the app is unaffected. |
| A tutor reply ends in `event: error` with `code: upstream_error` | The model call failed upstream — most often `OPENROUTER_API_KEY` unset or rejected (this is the *expected* outcome in the Compose smoke). Nothing is persisted, so the learner can simply ask again. |
| The tutor's routes all answer `404` for a real account | The `tutor` flag resolves off for that caller. Check `FEATURE_FLAG_DEFAULTS`, the account's per-user override, and whether the email domain is in `ADMIN_EMAIL_DOMAINS`. |
| The shaping routes all answer `404`, or the path view shows no shaping mark | The `shaping` flag resolves off for that caller — the same three checks, on its own key (the two flags are independent). Since AL-370 this should not happen for anyone: `fly.toml` sets `shaping:on`, so suspect a per-user override (which beats it) or an edited `FEATURE_FLAG_DEFAULTS`. See [Launching a flagged phase](#launching-a-flagged-phase-al-270--al-370). |
| Sending a shaping message returns `409 conflict` | The path is not `ready` — there is no structure to shape yet. Reading the thread and the Change history still works. |
| Apply or Undo returns `409` with a `details.reason` | Expected, first-class UX rather than an error: the reason names which rule fired (stale proposal, `not_latest`, `engaged`, `target_generating` …). The table of reasons is in [api.md](api.md), under *Shaping → Apply, Undo & the Change history*. |
| Cert stuck / invalid | `fly certs check <domain>`; DNS matches `fly certs setup`; ownership TXT if behind a proxy. |
| Scale-to-zero cold start | First request after idle wakes the machine. Acceptable for this app. **Note:** an in-flight generation does not survive the machine stopping — the reconciler re-claims it after `GENERATION_STALE_AFTER` on the next start (§5.4). |
