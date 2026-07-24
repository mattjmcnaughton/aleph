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

## Frontend

The frontend is scaffolded separately into `src/aleph/web/frontend/` using the `frontend-react` Copier template. In development, the frontend dev server runs independently with API proxying. In production, built static files are served by FastAPI via `web/serve.py`.

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
