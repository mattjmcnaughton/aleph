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
| `EXA_API_KEY` | Every Beat research run's retrieval step (Phase 6 TDD D6/§12). Startup succeeds without it, but research — the feature — cannot run: `services/lifecycle.py` leaves the loud `_UnconfiguredRetriever` bound in place of a live `ExaRetriever`, so a Beat's runs fail visibly (`research_state: failed`, retryable) rather than publishing an uncited Brief. | [AL-502](https://github.com/mattjmcnaughton/aleph/issues/166) |
| `OIDC_ISSUER` | Auth0 tenant issuer used for OIDC discovery. Non-empty is enforced at startup under `ENV=production`. | [AL-102](https://github.com/mattjmcnaughton/aleph/issues/39) |
| `OIDC_CLIENT_ID` | Auth0 Regular Web Application client id. Non-empty is enforced at startup. | [AL-102](https://github.com/mattjmcnaughton/aleph/issues/39) |
| `OIDC_CLIENT_SECRET` | Auth0 Regular Web Application client secret. Non-empty is enforced at startup. | [AL-102](https://github.com/mattjmcnaughton/aleph/issues/39) |

Four of those seven (`SESSION_SECRET_KEY`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET`) are **fail-fast**: a production boot missing any of them raises
at startup rather than serving in a degraded state. `OPENROUTER_API_KEY` and
`EXA_API_KEY` are the other shape — startup succeeds without either, and the
gap surfaces later as a visible, retryable failure on the generation each one
backs (`tests/unit/test_config_analyst.py` pins `EXA_API_KEY`'s no-crash-at-startup
behavior).

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

`OIDC_API_AUDIENCE` joins them when the native (iOS) app ships: it is the
Auth0 **API Identifier** (an audience, not a secret — see "Native app (bearer
token) configuration" below). Unset, bearer-token auth is off and every
`Authorization: Bearer` request is `401`; the web cookie flow is unaffected
either way. Startup rejects an `OIDC_API_AUDIENCE` equal to `OIDC_CLIENT_ID`, since
the web client's ID tokens carry that value as their `aud`.

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

The signed session cookie defaults to a **90-day sliding lifetime**
(`SESSION_MAX_AGE_SECONDS=7776000`, a positive number of seconds). Successful
cookie authentication rewrites the cookie with a fresh signature timestamp and
Max-Age; merely opening a static page does not renew it. Both browser retention
and server signature-age validation use this setting. An untouched cookie older
than the configured window is rejected and requires sign-in again.

P3 explicitly accepts the security tradeoff: there is **no server-side session
store or per-session revocation**. Logout deletes the browser's copy, not a stolen
copy. A leaked signed cookie can be replayed for up to 90 days from its signing
time by default, and continued authenticated use can renew it beyond that window.
Rotating `SESSION_SECRET_KEY` invalidates all signed cookies, not just one.
Controlled-time tests cover renewal and expiry; they are not evidence of an
actual 60-day Home Screen device observation.

### Native app (bearer token) configuration

The iOS app is a separate, **public** OIDC client that runs Authorization Code +
PKCE itself and calls `/api/v1` with `Authorization: Bearer <access token>`
([`api.md`](api.md#authentication)). In Auth0:

1. **APIs → Create API.** Pick an Identifier such as `https://api.<custom-domain>`
   (it never needs to resolve) with signing algorithm **RS256**. Set
   `OIDC_API_AUDIENCE` to exactly that Identifier in `fly.toml` `[env]`. It must
   not equal the web application's client id. Lower the API's **Token
   Expiration** from Auth0's 24-hour default to about an hour: aleph verifies
   access tokens offline (signature and claims), so blocking or deleting a user
   in Auth0 only cuts off a token they already hold when it expires. The app
   renews with a refresh token (step 3; enable refresh-token rotation on the
   Native application).
2. **Applications → Create Application → Native.** Allowed Callback URL (and
   Allowed Logout URL) is the app's custom-scheme or universal-link redirect.
   Grant types: Authorization Code and Refresh Token only (no Password, no
   Implicit). It has no client secret and needs no Fly secret.
3. The app authorizes with `audience=<API Identifier>` and
   `scope=openid profile email` (add `offline_access` for refresh tokens). The
   `openid` scope is load-bearing: aleph resolves the learner's profile and
   **verified email** from Auth0's `/userinfo` with the same token (Auth0 access
   tokens carry no email, and derived admin keys off it), and Auth0 only admits a
   token to `/userinfo` when `openid` was requested.

Nothing else is needed server-side: aleph discovers the JWKS and userinfo
endpoint from `$OIDC_ISSUER/.well-known/openid-configuration`, and a token from
the native app resolves to the same account as the same person's web sign-in
(both are keyed by Auth0's `(iss, sub)`).

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

### Destructive flashcard-removal rollout

The flashcard-removal migration is not safe to bundle into the first release that retires the
old application code. Fly runs `release_command` **before** replacing the old app machine, and
that old process still queries the tables the migration removes. Running the destructive
migration at that point can make the serving release fail until the new machine takes traffic.

Coordinate this transition as two production releases:

1. Release the application/runtime removal **without** the destructive migration at Alembic
   head. Verify the new machine serves retained paths, lessons, progress, tutor, shaping, Flow,
   and analyst behavior and makes no query to the retired tables.
2. In a later release, after every serving machine runs the retired-table-independent code, add
   and run the destructive migration. Verify `/readyz` and the retained workflows again.

Do not rely on release ordering within one Fly deploy to create that separation. If the migration
and runtime removal are already present in one branch, split their release eligibility (for
example, land the runtime release first and add the migration only afterward) rather than merely
splitting commits that semantic-release will package into the same image. Production migration
execution and any maintenance-window alternative require explicit operator approval.

The migration permanently deletes cards, review history, drafting runs, learner settings, and
the retired flashcard overrides. Its downgrade can recreate empty schema but cannot restore that
data or review-only historical streak credit. A rollback after migration therefore restores code
compatibility only if the rollback build also tolerates the removed schema; data recovery requires
an independently managed pre-migration backup. This runbook documents the required coordination;
it does not record that either release or the migration has been performed.

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
> **Phase 5's `streaks` launched the same way.** The retired Phase 3 flashcard
> flag also followed this process while that feature existed. **Phase 6's `analyst`
> (AL-570) has now launched the same way too** — the highest-per-run-cost
> feature in the product spent its whole build-out behind the same posture
> `tutor`/`shaping` did pre-launch, `ADMIN_DEFAULT_FLAGS` forcing it on for
> admins only while every other learner saw `404`.
>
> This section stays the reference for the lever itself, now read mainly for
> **turning a launched flag off** (the kill switch below) and for the next
> phase after this one that ships dark.

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
`tutor`, `shaping`, `streaks`, and `analyst` were launched this way. The retired
flashcard flag is no longer registered.

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

## Account-wide Tutor opt-in and disable runbook (LT6)

**Unshipped, default off, including admins.** This is an operator procedure for
a separately approved experiment, not permission to execute it. Feature-branch
integration does not authorize a main merge, deployment, shared-data migration,
live model spend, or per-user/global enablement. Unlike the older phases above,
`learning_tutor` is deliberately absent from `ADMIN_DEFAULT_FLAGS`. Do not use
their global-default launch recipe for this experiment.

### Before any dogfood enablement

1. Record the reviewed candidate revision, green required CI, combined gate,
   integration/E2E and offline eval results, and outstanding findings. Follow
   [PRD P1–P6](prds/learning-tutor.md#4-journeys-and-acceptance), not just a happy
   creation journey. Offline smoke (`just evals --learning-tutor --smoke --judge`)
   uses synthetic providers and is plumbing evidence, not content quality.
2. **Production migration blocker remains open:**
   `alembic/versions/0022_learning_tutor.py` builds `uq_paths_user_id_id` and
   `uq_beats_user_id_id` with ordinary `CREATE INDEX` on existing tables, not
   `CREATE INDEX CONCURRENTLY`. These builds can block writes. A disposable
   upgrade check does not establish a production lock-duration budget. Before
   any production migration, obtain a separately reviewed operational plan and
   acceptable lock/traffic evidence (or a reviewed migration change). Default-off
   exposure does not protect ordinary path/Beat writes from migration locks.
3. **Live quality, human calibration and judge agreement remain UNPROVEN.**
   Before wider enablement, separately authorize and perform the live evaluation
   in [evals.md](evals.md#account-wide-tutor---learning-tutor-lt5): at least 20
   curated cases, at least 90% whole-case pass, no grounding/privacy/safety
   failures, and human inspection of every failure. Calibrate the new rubric
   before claiming agreement. Neither scripted smoke nor a browser recording
   clears this gate. No real-device acceptance is implied.
4. Obtain explicit rollout and spend approval naming designated account IDs,
   environment, model, operator, budget/stop conditions and two-week review date.
   Review examples only under appropriate consent/access; never place real
   learner content or credentials in test artifacts, tickets or logs. Production
   reporting access is a separate authorization, not granted by this runbook.

### Configure limits, then opt in designated accounts only

The settings are read at process startup. Apply any approved configuration through
the environment's normal change/restart process and verify every serving replica;
editing a local env file does not reconfigure a running service.

| Setting | Current default | Operational meaning |
| --- | --- | --- |
| `MODEL_LEARNING_TUTOR` | `openai/gpt-5.6-luna` | Dedicated model slot resolved through OpenRouter; not a per-message admin override. `stub` is for local synthetic tests and is forbidden in production. |
| `RATE_LIMIT_LEARNING_TUTOR_PER_DAY` | `30` | Combined admitted turns and refreshes per account per UTC day. `0` disables this cap; admins are exempt. Do not mistake admin dogfooding for a quota test. |
| `MAX_CONCURRENT_LEARNING_TUTOR_OPERATIONS` | `2` | Separate process-local execution semaphore (at least 1), not a fleet-wide cost cap. One running operation per account is enforced durably across replicas. |
| `LEARNING_TUTOR_TIMEOUT` | `90` seconds | Positive, at most 90; total execution deadline includes semaphore wait. Database-clock lease is fixed at 120 seconds. |

Context is bounded to 40,000 dynamic characters, focus/input to 2,000 each, reply
to 6,000; refresh yields at most three cards and a turn at most one. Invalid model
output allows one validation retry before visible failure. These are not token
or dollar ceilings. Recommendation acceptance also invokes the existing path/Beat
generation pipeline and its model settings/costs; Tutor admission quota does not
bound that downstream spend. Inspect approved provider-side budget controls too.

Use an authenticated admin session with the existing
[feature-flag API](api.md#feature-flags-admin-apiv1admin-al-203). The following are
HTTP request templates, **not commands executed by LT6**; replace `{user_id}` only
with an approved account ID. Never paste session cookies into evidence.

```text
GET /api/v1/admin/feature-flags
PUT /api/v1/admin/feature-flags/learning_tutor/users/{user_id}
Content-Type: application/json

{"enabled": true}
```

The list returns effective global defaults and override counts, not account IDs.
Retain an access-controlled opt-in roster; ensure `learning_tutor`'s effective
global default stays false. An admin needs the same explicit override as a learner.
As each designated account, revalidate `GET /api/v1/auth/session` and confirm
`user.feature_flags.learning_tutor=true`. Home and `/tutor` should expose the
surface, and `GET /api/v1/learning-tutor/` should read state without model work.
Unselected learner/admin accounts must still resolve false and receive 404 on the
Tutor API. `analyst` independently gates Beat context/cards/acceptance; do not
change `tutor`, `shaping` or `analyst` merely to enable the Account-wide Tutor.

### Observe failures, quotas and the two-week experiment

- Only an explicit send/refresh admits Tutor work. POST returns 202 for running
  work or 200 for a terminal receipt; poll the returned operation. Cold refresh
  is `needs_context` with no model admission. Valid empty `completed` is success,
  unlike `provider_failed`, `invalid_output`, `timed_out`, `interrupted`,
  `context_changed` or `feature_disabled`. Preserve old cards on failed refresh.
- A 429 means the daily admission cap; wait until the next UTC day. Failed
  admitted work counts, including invalid model output; pre-admission input
  validation failures, cold refresh and same-request replay do not. Clear does
  not refund quota. For uncertain HTTP delivery retain the
  request UUID/body; a failed operation needs an explicit new request/current
  revision, never automatic paid replay. A 409 requires recovery/revalidation,
  not blindly resubmitting a changed body.
- Use `queries/logfire/learning_tutor_operations.sql` for observed terminal
  outcomes, duration, admissions and tokens; `learning_tutor_interactions.sql`
  for shown/explore/discuss and dismiss/restore. Events are best effort, not a
  complete billing ledger. Missing events or unknown interrupted usage are not
  zero cost. Reconcile recent durable operation IDs/status/admission/tokens
  without selecting input/output text; cleared receipts can be pruned after
  30 days on later admission. Follow [metrics.md](metrics.md#account-wide-tutor-lt5-receipts-first-observations-second)
  for query scope, denominators and ID-only acceptance reconciliation.
- At the two-week review, inspect approved examples and counts: displayed,
  explored, discussed, dismissed and accepted recommendations; seven-day path
  lesson completion; fourteen-day published Brief reads; duration, failures and
  usage per successful operation/observed acceptance. Use the PostgreSQL durable
  receipt queries for authoritative acceptance/follow-through, not event counts
  alone. Pin `:as_of` and cohort filters consistently. Later acceptances will
  still be immature at day 14: report pending separately, never as failure.
  Deleted targets are unknown, not zero activity; cleared receipts still count.
- Record **continue, revise or stop**, with evidence limitations. A small cohort
  does not establish statistical uplift; acceptance alone is not learning. There
  is no automatic global flip. Any wider rollout or further paid evaluation
  requires its own approval and the outstanding quality gates.

### Disable exposure without deleting learner data

Under separate operator authorization, stop new opt-ins and perform **both**:

1. Ensure the deployed settings default includes `learning_tutor:off`, preserving
   all unrelated `FEATURE_FLAG_DEFAULTS` entries. Apply/restart consistently on
   every replica. The code default is already false, but explicitly verify the
   effective default using the admin list; settings can override code.
2. Inventory **all explicit user-on overrides**, not just the initial roster.
   The admin list's count includes off overrides and cannot enumerate users. With
   authorized read-only database access, this ID-only inventory matches the ORM:

   ```sql
   SELECT user_id
   FROM user_feature_overrides
   WHERE flag_key = 'learning_tutor' AND enabled = TRUE
   ORDER BY user_id;
   ```

   For every returned account, use the admin API to PUT `{"enabled": false}` to
   `/api/v1/admin/feature-flags/learning_tutor/users/{user_id}` (200). Alternatively,
   DELETE that same URL (204) **after verifying the default is off**, which restores
   default resolution. A settings-off entry alone does not defeat user-on.
   Recheck the inventory, defaults and affected learner/admin sessions; Tutor API
   reads and recommendation-bearing creation must reject with 404, while ordinary
   creation and already-created targets retain their normal independent gates.

Disabling fences publication at settlement, but cannot un-send/refund an in-flight
provider request or guarantee immediate cancellation. Already admitted cost can
still arrive. This switch is not a database purge or a global stop for ordinary
path/Beat generation. Keep Learning focus, conversation/usage/acceptance receipts
and created targets intact. If code rollback is needed, deploy separately approved
compatible code while retaining the additive schema; **do not run a destructive
Alembic downgrade, clear conversations, or delete targets as rollback**.

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
| 6 — analyst (AL-540) | 6 | `brief_return` (north star), `brief_read_rate`, `brief_depth_of_read`, `brief_skip_rate`, `brief_wait_tolerance` (guardrail), `cost_per_read_brief` (guardrail) |

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

## Web Push configuration (provisional; not provisioned by #266)

`PUBLIC_BASE_URL` must be an HTTPS origin when `ENV=production`, even with push
disabled; `fly.toml` supplies `https://aleph.mattjmcnaughton.com`. This source
configuration change is not a deployment. The Compose smoke uses a synthetic
HTTPS origin and never sends push.

`VAPID_PRIVATE_KEY` is optional and **unset by default**. Without it,
`GET /api/v1/push-config` returns 404 and the Null sender records `disabled`,
never success and never a network request. Existing generation still works.
Setting or removing this credential requires separately authorized operations;
no production secrets or subscriptions have been provisioned in this ticket.

`just vapid-keys` generates a fresh P-256 pair locally and prints
`VAPID_PRIVATE_KEY` (raw base64url scalar) and `VAPID_PUBLIC_KEY` (uncompressed
base64url point). **The output is secret material: do not put it in logs,
screenshots, commits or chat.** The private key also accepts unencrypted PEM.
`VAPID_PUBLIC_KEY` may be omitted (derived at startup); a supplied mismatch
fails startup. Retain a stable pair: rotating it requires browser subscriptions
to be recreated. Never reuse the signing key as the ephemeral ECDH key.

The sender follows [RFC8291](https://www.rfc-editor.org/rfc/rfc8291) (single
aes128gcm record, rs4096) and [RFC8292](https://www.rfc-editor.org/rfc/rfc8292)
(ES256, audience=push endpoint origin, subject=PUBLIC_BASE_URL, 12-hour expiry,
cached per audience until one hour before expiry). TTLs are 3600/21600/86400
seconds for Path ready/Reminder/Brief published; Topic is the first 24 bytes of
SHA-256 of the logical tag, base64url encoded (32 characters). Redirects are
disabled, endpoints are revalidated at send time, ambient HTTP proxy settings
are ignored, and each network send times out after 10 seconds with no retry.
Delivery outcomes and gone removals emit credential-free product events.

No job scheduling, live provider delivery, phone display or tap-through is
verified here. Those remain T0/T7b/T9 and operational acceptance gates. In the
test-only `scripts/e2e_backend.py` factory the InMemory sender records sends at
`GET /__e2e__/pushes`; production exposes no e2e routes.

## Scheduled machines

Home Screen T8 adds a finite hourly `python -m aleph.jobs.tick` process. It does
not start HTTP, a reconciler, or detached web tasks. The steps run Reminders first,
then Beats, each with its own deadline. Winning durable Brief publications from
the job, creation or retry notify after commit; Skipped/lost fences never notify.
Push failures cannot undo publication. Device and operational acceptance
remain outstanding: no real scheduled Machine was created during implementation.

`deploy/schedules.toml` declares the Machines; `fly.toml` cannot declare schedules.
The existing **main-only**, successful-CI release job retains its concurrency
group `deploy-aleph-prod-mattjmcnaughton`. After a successful `flyctl deploy`,
`scripts/recreate_schedules.py --deploy-log <captured-output>` reads the single
`image: registry.fly.io/<app>:deployment-...` line from **that deploy's output**.
The workflow uses Bash `pipefail` with `tee`, so failed deploys never recreate.
Do not substitute the newest release: it might be a failed release, not the one
just deployed. Unparseable/ambiguous output fails before changing any Machine.

The script lists `flyctl machine list --app <app> --json` and destroys only
explicit IDs with `config.metadata.aleph_schedule` set, including schedules
removed from the file. It never selects by image or destroys untagged web
Machines. It then creates the declared schedules on the exact deployed image:

```sh
flyctl machine destroy <tagged-id> --app <app> --force
flyctl machine run --app <app> --region <primary_region> \
  --schedule hourly --restart no --vm-memory 512 \
  --metadata aleph_schedule=tick --detach \
  --env ENV=production <other-fly.toml-env-flags> \
  -- <exact-deployed-image> python -m aleph.jobs.tick
```

Flags deliberately precede `--`: without it, Python's `-m` is parsed as flyctl's
metadata shorthand. `machine run` builds a fresh Machine config; it does **not**
copy `fly.toml`'s `[env]`, so the script forwards **all** non-secret env entries
(especially `ENV=production`) and the app/primary region explicitly. App secrets
are provided by Fly to Machines in the same app. `--vm-memory` receives megabytes
(`512`, not the schedules file's `512mb`). No service/port or web process-group
metadata is attached. `--detach` avoids waiting on the job; **never use `--rm`**
for a recurring Machine, which must remain available after exiting.

Creation starts the job **immediately**, then on Fly's **fuzzy** hourly cycle,
not at the top of the hour. Rerunning the script replaces only tagged Machines;
it does not run migrations (deploy's release command already did). Any list,
destroy or create failure stops with nonzero status and fails the deploy job.
This is not an atomic replacement: a failure may leave schedules absent or
partially recreated. Inspect the Machines and rerun against the same successful
deploy output after fixing the cause. A force-destroyed in-flight job is recovered
by the existing stale/fenced claim protocol, not silently marked Skipped.

For an explicitly authorized one-off operational check, use the same image,
app/region/env flags, but omit schedule metadata/`--schedule` and add `--rm`:

```sh
flyctl machine run --app <app> --region <primary_region> --restart no \
  --vm-memory 512 --rm --env ENV=production <other-fly.toml-env-flags> \
  -- <exact-deployed-image> python -m aleph.jobs.tick
```

Locally, `uv run python -m aleph.jobs.tick` uses configured database and retrieval/
model credentials; do not run against shared data or live providers as a test.
The disposable-Postgres integration suite uses fake retrieval/models instead.
Reminders scan opted-in accounts with Devices and a stored IANA zone in keyset
pages of 100 (no UTC fallback), comparing the local hour with `>=` using time
sampled after each account's lock, not a frozen tick-start date. Completion
dates use the zone's historical rules at each instant, not today's fixed offset.
An Active day suppresses the Reminder; otherwise its copy reflects the current
Daily streak, targeting the first available lesson in the existing API path order,
or Your paths when none is available. Home and Reminders use the same server-owned
`resume_path` rule; `GET /paths` exposes nullable `resume_path_id` to clients.
Deploy the server first: a new client against an older response safely omits the
Continue card rather than inventing a target (the path list still works).

The `(user_id, local_date)` insert-on-conflict claim commits **before** sending to
Devices. `sent_at` is the attempt/claim timestamp, not proof of delivery. A crash or
cancellation after commit can lose the day's Reminder; `attempted` remains durable
and is never retried. Settled outcomes are `delivered` (push-service acceptance,
not device receipt), `disabled`, `gone` or `error`; any Device error takes precedence
over another Device's acceptance in the account outcome. Current ownership and
preferences are rechecked under each Device lock before delivery. No job ledger,
outbox or network retries are added. Apply additive migration
`0020_reminder_scan_progress` before running this worker. Its singleton frontier
commits before each account's eligibility checks and wraps at most once per tick,
so a repeatedly blocked prefix cannot starve later accounts. As with Beats,
fairness assumes one active scanner and enough time to commit progress. A crash
after checkpoint but before eligibility can defer that account until the next
sweep. Stop/revert the worker before downgrading; the daily attempt rows remain.

Attempt rows currently persist until account deletion (foreign-key cascade), with
at most one row per eligible account/local date; no automatic retention purge is
implemented. Confirm this retention and database growth, and configure alerts for
nonzero tick exits/`job_step_failed` plus notification errors before production
activation. These are outstanding operational gates, not provisioned monitoring
or permission to purge data. Push-service acceptance is not phone receipt.

The step deadline is `JOB_STEP_TIMEOUT_SECONDS` (1500). Candidate pages use
`JOB_BEAT_BATCH_SIZE` (100), claims per tick use `JOB_MAX_BEATS_PER_TICK` (100),
and generation concurrency uses `MAX_CONCURRENT_GENERATIONS`. Permits are acquired
**before claims**, so queued jobs cannot age into stale claims. Scans advance
past capped, not-due and idle Beats rather than pinning the first LIMIT window;
the deadline bounds total scanning time. Web research keeps its separate pool.

Migration `0019_beat_scan_progress` seeds one durable scan frontier. Apply it
before starting the new worker; old code ignores the additive table. Each admitted
Beat advances the frontier in a short transaction before account/Beat lock waits,
so repeated deadlines cannot pin the same prefix. A tick wraps at most once.
Fairness assumes one active scanner and enough time to commit an admission;
overlapping scanners retain claim/fence safety, not strict scan fairness. A crash
between checkpoint and processing can defer that Beat until the next sweep.
Stop or revert the new worker before downgrading this migration. Reverting the
scanner also restores its previous cross-tick starvation risk.

`job_tick_started`, `job_step_finished`/`job_step_failed` and `job_tick_finished`
report counts/outcomes (published, skipped, failed, refused, idle, capped,
not_due, disabled, lost_claim). A timeout, infrastructure failure, or failed
research produces a nonzero exit; a safety refusal or legitimate Skipped result
does not. Reminder errors count as failed; disabled sends do not claim delivery.
Each step has its own deadline/error boundary, so a Reminder failure
will not conceal the Beat result. Cancellation awaits all child tasks; interrupted
Beat claims are left for stale recovery, Reminder claims are never replayed.
Monitor job exit/logs separately: `--detach`
means successful recreation is not proof a tick completed successfully.

Semantics checked against the [official machine-run documentation](https://fly.io/docs/machines/flyctl/fly-machine-run/)
and flyctl source at [`b3263736`](https://github.com/superfly/flyctl/tree/b3263736a487b5c713fc7733633cfbb2e2976dd6):
`internal/command/machine/run.go` (fresh config/env, flags/command, schedule,
detach), `list.go` (raw Machine JSON), `destroy.go` (explicit IDs/force), and
`internal/command/deploy/deploy_build.go` (`image: <tag>` output). Actual Fly
recreation on a released image, hourly waking, env/secrets and delivery remain
separate human/operations gates; fake CLI tests are not that acceptance.

## Troubleshooting

| Symptom | What to check |
| ------- | ------------- |
| `ModuleNotFoundError: No module named 'psycopg2'` during migrate | `DATABASE_URL` still uses Neon's `postgresql://…`. Rewrite to `postgresql+asyncpg://…`. |
| `TypeError: … unexpected keyword argument 'sslmode'` / `'channel_binding'` | Neon libpq params. Use `?ssl=require` only — drop both. |
| Startup raises "Production (ENV=production) requires real auth secrets" | One of `SESSION_SECRET_KEY` / `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` is unset, empty, or still the dev default. |
| iOS app gets `401` `invalid bearer token` on every call | `OIDC_API_AUDIENCE` unset or not exactly the Auth0 API Identifier, or the app did not request `audience=`/`openid`. The rejection reason is in the `bearer_auth_rejected` log event. |
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
