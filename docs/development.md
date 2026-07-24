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
  machine's preinstalled chromium via `PW_CHROMIUM_PATH`.
- `tests/external/` — live-provider contract tests, opt-in via `just test-external`.

Use `@pytest.mark.external` for tests that hit external services (they must skip cleanly
without `OPENROUTER_API_KEY`); CI never runs them. Tag a test that proves a PRD workflow
with `@pytest.mark.workflow("W1")` — shared vocabulary only, no enforcement machinery.

## Docker

```sh
# Build image
docker build -t aleph .

# Run container
docker run -p 8000:8000 aleph
```
