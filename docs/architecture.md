# Architecture

## Overview

aleph is a Python web application using FastAPI for the backend and a separately-scaffolded frontend.

## Project Structure

```
src/aleph/
  app.py               # FastAPI app factory
  config.py            # Pydantic-settings config from env vars
  logging.py           # structlog setup
  telemetry.py         # OpenTelemetry setup
  routers/             # HTTP endpoint definitions
    health.py          # /healthz, /readyz
  services/            # Business logic
  dtos/                # Pydantic request/response models
  web/                 # Frontend integration
    serve.py           # Static file serving for production
    frontend/          # Frontend app (scaffolded separately)
  db.py                # Async engine/session factory
  models/              # SQLAlchemy async models
  repositories/        # Data access layer
tests/
  unit/                # Fast, isolated unit tests
  integration/         # Tests with real dependencies (Postgres, Keycloak)
  external/            # Live-provider contract tests (opt-in, @pytest.mark.external)
evals/                 # Agent eval harness — a peer of tests/, dev-only and
                       # never packaged; opt-in runs (docs/evals.md)
scripts/
  e2e_backend.py       # Stub-model app factory the Playwright harness boots
src/aleph/web/frontend/
  playwright.config.ts # E2E harness config (desktop + mobile-390x844 projects)
  tests/e2e/           # Playwright browser suite (end-to-end user journeys)
```

## Layering

```
routers (HTTP endpoints, parse requests into DTOs)
  -> services (business logic, framework-agnostic)
    -> agents (pydantic-ai definitions; bind no model, import no services/config/db)
    -> repositories (data access, return SQLAlchemy models)
      -> models (SQLAlchemy)
```

DTOs (Pydantic models) are used at the router and service level for API I/O. They are always separate from database models.

`evals/` sits outside this stack entirely: it is development tooling that imports the `agents/` factories (which is what their no-model, no-config purity is *for*) and is excluded from the wheel — see [`docs/evals.md`](evals.md). It reaches into `services/` and `config` only to bind a model — `services/openrouter.resolve_model` plus `config.settings` for the key and slot ids (lazily, so `--smoke` needs no configuration), and `services/stub_model` for the offline stub. The direction is one-way: nothing under `src/aleph/` imports `evals/`.

## Frontend

The frontend is scaffolded separately into `src/aleph/web/frontend/` using the `frontend-react` Copier template. In development, the frontend dev server runs independently with API proxying. In production, built static files are served by FastAPI via `web/serve.py`.

**Generated content is Markdown, rendered at the edge.** The lesson agent writes the Read passage as GitHub-Flavored Markdown (a bounded subset — see [`docs/api.md`](api.md)); it is stored and served as source, and `src/components/markdown.tsx` is the single place that turns it into DOM. That component is the security boundary for model-generated text: `react-markdown` + `remark-gfm`, no `rehype-raw` and no `dangerouslySetInnerHTML`, so raw HTML is escaped rather than executed and dangerous URL protocols are stripped. Any new surface showing generated prose should render through it rather than reaching for its own Markdown pipeline.

## Toolchain

| Tool | Purpose |
| ---- | ------- |
| uv | Package management, virtual environments |
| hatchling | Build backend |
| ruff | Formatting and linting |
| ty | Type checking |
| pytest | Testing |
| FastAPI | Async web framework |
| uvicorn | ASGI server |
| structlog | Structured logging |
| pydantic-settings | Configuration |
| OpenTelemetry | Distributed tracing |
| pnpm | Frontend package management |
| SQLAlchemy | Async ORM |
| Alembic | Database migrations |

## Conventions

- All configuration is in `pyproject.toml`.
- Version is the single source of truth in `pyproject.toml`, read via `importlib.metadata`.
- `py.typed` marker enables downstream type checking (PEP 561).
- Health endpoints are always available at `/healthz` and `/readyz`.
- Just targets have `-be`/`-fe` variants for backend/frontend (e.g. `just fmt-be`, `just fmt-fe`).
