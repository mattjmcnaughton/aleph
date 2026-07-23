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

## Database

```sh
# Start Postgres
docker compose up -d

# Run migrations
uv run alembic upgrade head
```

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

Tests are organized by type:

- `tests/unit/` — fast, isolated tests
- `tests/integration/` — tests with real dependencies
- `tests/e2e/` — end-to-end tests

Use `@pytest.mark.external` for tests that hit external services. These run via `just test-external`.

## Docker

```sh
# Build image
docker build -t aleph .

# Run container
docker run -p 8000:8000 aleph
```
