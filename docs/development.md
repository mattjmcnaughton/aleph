# Development

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [just](https://just.systems/)
- [Docker](https://www.docker.com/) (for dependencies)
- [Node.js](https://nodejs.org/) 20+ and [pnpm](https://pnpm.io/) (for frontend)

## Setup

```sh
# Install backend dependencies
uv sync --dev

# Copy environment file
cp .env.example .env

# Install frontend dependencies
cd src/aleph/web/frontend && pnpm install
```

## Regenerating from templates (`copier update`)

This repo was scaffolded from the
[`mattjmcnaughton/templates`](https://github.com/mattjmcnaughton/templates)
monorepo with [Copier](https://copier.readthedocs.io/): the `python-web`
template at the repo root and the `frontend-react` template composed into
`src/aleph/web/frontend/`. The recorded answers live in `.copier-answers.yml`
(backend) and `src/aleph/web/frontend/.copier-answers.yml` (frontend), and are
already committed — you do **not** re-run `copier copy`.

To pull later upstream template changes, clone the `templates` repo as a
sibling of this repo (so it resolves at `../templates`) and run both updates
**from the aleph repo root** — each `_src_path` is stored relative to that
directory:

```sh
# backend (python-web) template
copier update --trust
# composed frontend (frontend-react) template
copier update --trust src/aleph/web/frontend
```

## Backing services (Postgres + Keycloak)

The app needs Postgres, and — once app-side auth lands (AL-020) — a Keycloak
OIDC realm for local login and browser tests. Bring your own services if you
already run them, or start the checked-in Docker Compose stack.

### Docker Compose (default)

```sh
# Postgres on localhost:5432 (waits until it accepts connections)
just compose-db-up

# Run migrations against it
uv run alembic upgrade head

# Keycloak (dev realm) on localhost:18080 — start when you need auth
just compose-keycloak-up

# Stop everything
just compose-down
```

`compose-db-up` matches `.env.example`
(`DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/aleph`).
Port clashes with an existing Postgres/Keycloak? Override the published host
ports: `ALEPH_DB_PORT=15432 just compose-db-up`,
`ALEPH_KEYCLOAK_PORT=28080 just compose-keycloak-up`. Overriding a port also
means updating the matching `.env` value — `DATABASE_URL` for the db port,
`OIDC_ISSUER` for the Keycloak port.

### Keycloak dev realm

`just compose-keycloak-up` starts Keycloak in dev mode and imports the realm
from `docker/keycloak/aleph-realm.json` (mounted read-only into the container's
`/opt/keycloak/data/import/`). It provisions:

| Thing | Value |
| ----- | ----- |
| Realm | `aleph` — issuer `http://127.0.0.1:18080/realms/aleph` |
| Client | `aleph` (confidential, standard authorization-code flow) |
| Client secret | `aleph-dev-secret` (well-known dev secret, not a real credential) |
| Redirect URIs | `http://{127.0.0.1,localhost}:{8000,5173}/auth/callback` (backend + Vite dev server) |
| Test user | `dev` / `dev`, email `dev@example.com` (verified, non-admin) |
| Admin test user | `admin-dev` / `admin-dev`, email `admin@mattjmcnaughton.com` (verified; admin via `ADMIN_EMAIL_DOMAINS`) |
| Keycloak admin | `admin` / `admin` at `http://127.0.0.1:18080/` |

The realm keeps direct access grants **disabled** (browser authorization-code
flow only, matching production). Verify it is serving:

```sh
curl -fsS http://127.0.0.1:18080/realms/aleph/.well-known/openid-configuration
```

Corresponding OIDC env for the native app lives in `.env.example`
(`OIDC_PROVIDER`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`). The
app does not consume these until AL-020 wires up auth.

### Using your own services

If you already run Postgres 16 (and Keycloak, or another OIDC provider such as
Auth0), skip Compose and point `DATABASE_URL` / the `OIDC_*` vars at them — see
`.env.example`.

## Running Locally

```sh
# Start both dev servers
just dev

# Or start separately
just dev-be   # Backend at http://localhost:8000
just dev-fe   # Frontend dev server
```

The API will be available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

**Seeing the tutor and the shaping rail locally.** Nothing to configure: `tutor`
and `shaping` are both launched and default **on** in code
([`api.md`](api.md#feature-flags-admin-apiv1admin-al-203)), so a fresh clone with
no `.env` shows both surfaces to every account. Sign in as the realm's `dev` user
and the in-lesson rail and the path view's shaping mark are simply there.

This is deliberate: for most of Phase 2 and 2B these flags defaulted **off**, and
the local story was "sign in as `admin-dev` or set an environment variable, or the
feature you are working on answers `404`" — a papercut on every fresh clone and a
confusing first run for anyone who had not read this paragraph. A launched feature
should be on by default everywhere, including on a laptop.

**Turning one off locally** — to reproduce a `404` gate, or to check how a surface
degrades — is the same lever production uses, in `.env`:

```bash
FEATURE_FLAG_DEFAULTS=shaping:off      # or tutor:off, or tutor:off,shaping:off
```

An explicit `:off` outranks the code default *and* the admin baseline, so it
silences admin accounts too. A phase still under construction should register its
flag `False` and ride `ADMIN_DEFAULT_FLAGS`, exactly as these two did — that
posture is unchanged, it just no longer applies to a launched feature
([deploy.md](deploy.md#launching-a-flagged-phase-al-270--al-370)). Two things
that are not flag problems: the rail's entry only renders on a **`ready`** path
(sending is `409` before the outline exists), and a real model proposes when it
judges an ask concrete. To force the branches, run the shaper slot on the
deterministic stub (`MODEL_SHAPER=stub` — what `scripts/e2e_backend.py` wires for
the Playwright suite, and what `ENV=production` forbids) and put a sentinel in
the message: `[force-proposal-add]`, `[force-proposal-revise]`,
`[force-shaping-decline]` or `[force-shaping-failure]`
(`services/stub_model.py`).

**Seeing the analyst (`beats`/`briefs`) locally, and the honest limit on `just
dev-be` alone.** Nothing to configure: `analyst` is now launched and defaults
**on** in code, the same as `tutor`/`shaping` above, so a fresh clone with no
`.env` shows the deploy form and the Beats section on home to the `dev` user
too — no admin sign-in required.

That gets the surface rendering, but **`just dev-be` on its own cannot
actually research a Beat without `EXA_API_KEY`.** `services/lifecycle.py`
only binds a live `ExaRetriever` when the key is configured; with no key, the
loud `_UnconfiguredRetriever` default stays bound, so a deployed Beat's first
research run fails immediately and visibly (`research_state: "failed"`,
retryable) — correct behavior (TDD §12: startup must still succeed without
the key, and research must fail visibly rather than publish uncited), but it
means a plain `just dev-be` never produces a real Brief. There is no dev-mode
wiring that points the ordinary app factory at `FixtureRetriever` or
`StubRetriever` instead — both exist only in other harnesses:

- **The eval harness, offline** — `just evals --smoke --briefs` runs the
  actual researcher/analyst agents against `FixtureRetriever` replaying
  `evals/fixtures/retrieval/*.yaml` (today, hand-authored placeholders — see
  [`evals.md`](evals.md)), with no key and no network call. This exercises the
  pipeline's logic end to end, but it is the eval CLI, not the web app — there
  is no Beat row, no rail, nothing to click through.
- **The Playwright e2e suite** — `just test-e2e` boots
  `scripts/e2e_backend.py`, which wires `StubRetriever` (not
  `FixtureRetriever`) into `briefing_service` in place of the lifespan's own
  binding, alongside the stub model on every slot. This is the one path that
  produces an actual, clickable Beat/Brief through the real HTTP API with no
  key: sign in, deploy a Beat, and its research resolves deterministically
  against the stub's synthetic documents. Two sentinels in the Beat's topic
  string force the two non-happy branches, on the Phase 1 precedent:
  `[force-retrieval-failure]` (→ a `failed` run) and `[force-no-findings]` (→
  a `skipped` row — the researcher reports zero findings from documents it
  genuinely retrieved, not zero documents; see `services/retrieval.py::
  StubRetriever`'s docstring and the Phase 6 TDD §11's amendment for why).
  `tests/e2e/journeys/w29.spec.ts` and `w31.spec.ts` are the scripted version
  of exactly this walkthrough.

With a real `EXA_API_KEY` set in `.env`, `just dev-be` researches for real —
useful for a one-off manual check, but it spends money and reads the live
web, so prefer the two no-key paths above for routine local development.

## Common Tasks

```sh
# Format and lint (backend + frontend)
just fmt-fix
just lint-fix

# Backend only
just fmt-fix-be
just lint-fix-be

# Frontend only
just fmt-fix-fe
just lint-fix-fe

# Type check
just typecheck

# Run tests
just test-unit
just test-all

# Full pre-push check
just gate

# Full check including integration and e2e
just gate-expensive
```

## Testing

Tests are organized by type (see [`ci.md`](ci.md) for the CI job that runs each):

- `tests/unit/` — fast, isolated tests (backend `just test-unit-be`, frontend `just test-unit-fe`)
- `tests/integration/` — tests against real Postgres + Keycloak (`just test-integration`)
- `src/aleph/web/frontend/tests/e2e/` — the Playwright browser suite at the phone
  viewport (`just test-e2e`); it boots the stub-model backend (`scripts/e2e_backend.py`)
  plus the dev frontend. Locally it creates an `aleph_e2e` database and uses the
  machine's preinstalled chromium via `PW_CHROMIUM_PATH`. **Needs compose
  Keycloak** (`just compose-keycloak-up`): `tests/e2e/journeys/` are the PRD §8
  workflows — Phase 1's `@w1`..`@w8`, the tutor's `@w9` and `@w11`..`@w16`
  (W10 is reserved for the deferred selection-to-quote), shaping's
  `@w17`..`@w21`, streaks' `@w22`/`@w23`, flashcards' `@w24`..`@w28` (their own
  `mobile-390x844-flashcards` project, isolated accounts — see that project's
  own comment in `playwright.config.ts`), and the analyst's `@w29`/`@w31`
  (W30/W32/W33 are integration cases instead, PRD §7.1) — and they sign in for
  real. The journeys run in the `mobile-390x844` project only (flashcards'
  five in their own sibling project); the `desktop` project runs the
  non-journey specs. See [`ci.md`](ci.md).
- `tests/external/` — live-provider contract tests, opt-in via `just test-external`.

Use `@pytest.mark.external` for tests that hit external services (they must skip cleanly
without `OPENROUTER_API_KEY`); CI never runs them. Tag a test that proves a PRD workflow
with `@pytest.mark.workflow("W1")` — shared vocabulary only, no enforcement machinery.

## Docker (the production image)

`Dockerfile` builds what Fly runs (see [deploy.md](deploy.md)): a pnpm/Vite frontend
build, a uv-installed backend virtualenv with no dev or eval dependencies, and a slim
runtime that serves the API and the built SPA from one process.

```sh
# Build it
docker build --target production -t aleph .

# Boot the whole stack and prove it serves (build + migrate + HTTP assertions)
just compose-smoke
```

`just compose-smoke` builds the image, migrates a throwaway database, and asserts the
app serves — it is also a CI job. It runs against its own `smoke-db` service, so it
cannot disturb the `db` service (and `pgdata` volume) your local database lives in;
override `ALEPH_APP_PORT` if 8000 is busy. Full description:
[deploy.md § The Compose smoke](deploy.md#the-compose-smoke-just-compose-smoke).

Running the image by hand needs a reachable database and, under `ENV=production`, real
auth secrets — `docker compose up app` supplies both.
