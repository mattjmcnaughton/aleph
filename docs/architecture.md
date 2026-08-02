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
  agents/              # pydantic-ai agent definitions (bind no model, no app imports)
  domains/             # Pure domain logic, no I/O (grading, progression, engagement,
                       # Change payloads and their position shifts)
  dtos/                # Pydantic request/response models
  web/                 # Frontend integration
    serve.py           # Static file serving for production
    frontend/          # Frontend app (scaffolded separately)
  db.py                # Async engine/session factory
  models/              # SQLAlchemy async models
  repositories/        # Data access layer
alembic/versions/      # Database migrations
queries/logfire/       # Saved metric queries (docs/metrics.md)
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

## Streaming and feature flags (Phase 2)

The tutor is the one surface that streams. `services/sse.py` owns the wire
framing (four named events plus a `: ping` comment, and the `Cache-Control:
no-store` / `X-Accel-Buffering: no` response headers) and nothing else — it is
pure string work over a Pydantic payload, so the protocol the frontend parses is
pinned by unit tests. `services/tutor.py` owns the turn lifecycle and is the only
module that speaks that protocol: **admit** (every failure that can still be an
ordinary JSON envelope happens before a response object exists), **stream** (a
producer task pushes pre-encoded frames into a queue while the response generator
drains it, so the heartbeat and the timeout are clocks that run while the
generator is suspended), then **settle** (one transaction, or nothing —
`routers/v1/tutor.py` explains why the reservation is released by the response
object rather than by the generator). Context assembly is its own seam,
`services/tutor_context.py`, which does plain `SELECT`s only: asking a question
must never trigger generation. The agent, `agents/tutor.py`, obeys the same
no-model/no-config purity as the Phase 1 agents.

Two things a streaming endpoint must not skip, both of which the tutor route
does deliberately: it **closes the request's database session before returning
the response** (a stream can hold a pooled connection for the whole reply
otherwise), and it runs under its **own** semaphore
(`MAX_CONCURRENT_TUTOR_REPLIES` in `services/lifecycle.py`), separate from
generation's, because a waiting learner and a background generation should not
compete for the same permits.

`services/feature_flags.py` is the flag registry: flags are defined in code, the
database stores only per-user exceptions, and the resolved map rides on the auth
session probe (that module's docstring is the authoritative statement of the
resolution order — see [`docs/api.md`](api.md)). Phase 2 shipped dark behind the
`tutor` flag, which is why the whole tutor router hangs off one `404` gate;
Phase 2B repeated the pattern on its own `shaping` key. Both are now launched and
default on in code, and the gates remain as kill switches — turning either off is
one committed config change ([`docs/deploy.md`](deploy.md#launching-a-flagged-phase-al-270--al-370)).

## Shaping: the one write path into path structure (Phase 2B)

Phase 2B adds a second conversation per path and, through it, the only way path
structure changes outside Phase 1's generation pipeline. The claim is enforced by
module topology rather than by review: **`services/shaping.py` is the only module
that writes `units`/`lessons` outside generation**, and only inside `apply_change`
/ `undo_change`. Nothing reads conversation text into a mutation — the learner's
tap on a stored, re-validated **Proposal** is the sole trigger.

The surface reuses rather than rebuilds. `agents/shaper.py` obeys the same
no-model/no-config purity as every other agent and exports the proposal
predicates, so the agent (at draft time), the evals and the apply-time
re-validation run *the same* functions. The turn lifecycle, the SSE framing and
the rail component tree are 2A's, extended by one named event (`proposal`), one
mount (`shaping-rail` on the path route) and one payload column; shaping replies
share the tutor's permit pool and timeout. Context assembly is the seam Phase 2
promised: `assemble_shaping_context` sits beside `assemble_lesson_context` in
`services/tutor_context.py` and, like it, does plain `SELECT`s only.

Three pieces are genuinely new and worth knowing before touching them:

- **`domains/changes.py`** — the Change payload and its inverse, as plain data.
  It lives in `domains/` because two services need it and neither may import the
  other (`services/shaping.py` writes it; `services/generation.py` reads the
  pre-revision snapshot back out for the lesson prompt's revision block).
  Position shifts are an ordered *plan* of single-row moves, not a set-based
  `UPDATE`, because `UNIQUE (path_id, position_in_path)` is non-deferrable and
  checked per row: insertion shifts run descending, and undo replays their
  inverses last-first.
- **`services/shaping.py`'s apply/undo transactions** — one transaction each,
  under a per-path lock, re-validating against live path state first because a
  path moves between drafting and tapping. Every refusal is a coded `409` the
  proposal card can render. The lock is per-process, so the database carries the
  backstop: a partial unique index on `path_changes(message_id) WHERE status =
  'applied'` (migration `0007`) makes "a Proposal is applied at most once" true
  across a rolling deploy's two machines.
- **`domains/engagement.py`** — the engagement predicate (an Attempt exists, or
  the lesson is complete). It is derived, never stored, and the same derivation
  gates proposal validation, apply and undo. The UI's disabled states are
  conveniences; these checks are the rule.

Ghost rows have no server component at all: the path rail merges the pending
proposal payload client-side, so a preview can be optimistically stale while the
mutation cannot (apply re-validates). Applied rows are ordinary data from the
refreshed path the apply response returns.

## Frontend

The frontend is scaffolded separately into `src/aleph/web/frontend/` using the `frontend-react` Copier template. In development, the frontend dev server runs independently with API proxying. In production, built static files are served by FastAPI via `web/serve.py`.

**Generated content is Markdown, rendered at the edge.** The lesson agent writes the Read passage as GitHub-Flavored Markdown (a bounded subset — see [`docs/api.md`](api.md)); it is stored and served as source, and `src/components/markdown.tsx` is the single place that turns it into DOM. That component is the security boundary for model-generated text: `react-markdown` + `remark-gfm`, no `rehype-raw`, so raw HTML is escaped rather than executed and dangerous URL protocols are stripped. Any new surface showing generated prose should render through it rather than reaching for its own Markdown pipeline.

A ` ```mermaid ` fence routes to `src/components/mermaid.tsx` instead of a code block. Mermaid is ~635 kB, so it is behind a dynamic `import()` — Vite code-splits it and only a lesson that actually draws something pays for it. It renders at `securityLevel: "strict"` (mermaid's own DOMPurify pass over the SVG, HTML labels and `click` directives disabled); that sanitised SVG is the one `dangerouslySetInnerHTML` in the codebase, and only mermaid's output may ever reach it. An unparseable chart falls back to its source as a code block rather than an error, because model-written mermaid is often subtly invalid and a lesson must stay readable regardless.

**The desktop shell is CSS-only, at Tailwind's `lg` breakpoint (1024px).** No `matchMedia`, no JS viewport state, no width-conditional rendering — every route ships the same markup at every width, and `lg:` utilities alone widen it. `src/components/workspace.tsx` owns the column layout (a `sidebar` beside a widened `main`) and each route's own content cap at `lg`; `src/components/sidebar.tsx` owns the sidebar's two sections — the "Your paths" Switcher and, on a lesson, the current path's condensed lesson list (the path rail; the component keeps its `OutlineSection` name). The phone surface is unchanged by construction: below `lg` every desktop-only element collapses to `hidden` or its ordinary single-column layout, so the existing mobile routes, tests, and Playwright journeys never had to move.

The tutor rail (`src/components/tutor/`) is the same idea taken one column further: `workspace.tsx` mounts it as a **third** column that is a bottom sheet over the lesson below `lg` and a docked right column at `lg`, from one tree — open/closed is the only JS state, and the presentation is decided entirely by `lg:` utilities. While the sheet is open, `main` carries bottom padding so the tail of the lesson can still be scrolled out from under it. Three surfaces, three names, kept apart on purpose: the tutor **rail**, the path view's **path rail**, and the desktop **Sidebar** ([`docs/CONTEXT.md`](CONTEXT.md)).

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
