# CLAUDE.md

Mobile-friendly AI tutor: name a topic, get a generated learning path.

Read [`CONSTITUTION.md`](CONSTITUTION.md) before planning, implementation, or review.
It owns durable product and technical principles; report conflicts rather than
silently weakening them to match existing code.

Python web application using FastAPI, uvicorn, OpenTelemetry, uv, ruff, ty, and pytest. The one active learner client is the React web frontend in `src/aleph/web/frontend/`, including its planned Home Screen presentation (see "One Client" below).

> **Vocabulary is authoritative: [`docs/CONTEXT.md`](docs/CONTEXT.md).** Same word,
> same meaning, in prose, prompts, and code alike. Use the exact term — **path** not
> "course", **Quick check** not "quiz", **Read passage** in prose and schemas. Read it before
> naming anything new.

## Quick Reference

| Command | Purpose |
| ------- | ------- |
| `just fmt` | Check formatting (backend + frontend) |
| `just fmt-fix` | Fix formatting (backend + frontend) |
| `just lint` | Check linting (backend + frontend) |
| `just lint-fix` | Fix linting (backend + frontend) |
| `just typecheck` | Run type checker (backend + frontend) |
| `just test-unit` | Run unit tests (backend + frontend) |
| `just test-integration` | Run integration tests |
| `just test-e2e` | Run e2e tests |
| `just test-all` | Run all tests |
| `just test-external` | Run tests hitting external services |
| `just gate` | Fast pre-push check (fmt + lint + typecheck + test-unit) |
| `just gate-expensive` | Full check (gate + integration + e2e) |
| `just gate-external` | Everything (gate-expensive + external) |
| `just compose-smoke` | Build the production image and smoke it over HTTP (see `docs/deploy.md`) |
| `just dev` | Start backend and frontend dev servers |
| `just dev-be` | Start backend dev server only |
| `just dev-fe` | Start frontend dev server only |
| `just storybook` | Start the Storybook design sandbox on :6006 (see `docs/storybook.md`) |

All formatting, linting, typecheck, test-unit, and gate targets have `-be` and `-fe` variants (e.g. `just fmt-be`, `just fmt-fe`).

## Project Structure

```
src/aleph/
  app.py                    # FastAPI app factory
  config.py                 # Pydantic-settings based config
  logging.py                # structlog setup
  telemetry.py              # OpenTelemetry setup
  db.py                     # Async engine/session setup
  domains/                  # Pure domain logic, no I/O (grading, progression, cadence, novelty)
  agents/                   # pydantic-ai agents (no bound model, no config/db)
  routers/                  # HTTP endpoint definitions (health.py: /healthz, /readyz)
  services/                 # Business logic / orchestration
  repositories/             # Data access layer
  models/                   # SQLAlchemy models
  dtos/                     # Pydantic request/response models
  web/serve.py              # Static file serving for production
  web/frontend/             # Frontend application (scaffolded separately)
tests/
  unit/                     # Unit tests
  integration/              # Integration tests (real Postgres + Keycloak)
  external/                 # Live-provider contract tests (opt-in, @pytest.mark.external)
scripts/e2e_backend.py      # Stub-model app factory the Playwright harness boots
src/aleph/web/frontend/tests/e2e/  # Playwright browser suite (E2E, phone viewport)
```

## Key Conventions

- Source code lives in `src/aleph/` (src layout).
- **Layering:** `routers -> services -> (agents, repositories)`. Agent definitions in
  `agents/` bind no model and import no application layers (services, routers, config,
  db, repositories, models) nor FastAPI/SQLAlchemy — enforced by a layering test. Pure
  domain logic (grading, progression) lives in `domains/` and does no I/O.
- **DTOs** are Pydantic models for API I/O, always separate from DB models.
- **Frontend** is scaffolded separately into `src/aleph/web/frontend/` using the `frontend-react` template.
- Tests are organized by type: `tests/unit/`, `tests/integration/`, `tests/external/`
  (backend), and the Playwright browser suite in `src/aleph/web/frontend/tests/e2e/`.
  Mark tests that hit external services with `@pytest.mark.external` (opt-in, never in
  CI); tag workflow-proving tests with `@pytest.mark.workflow("W1")`. CI runs four jobs
  — gate / integration / e2e / compose-smoke (see `docs/ci.md`).
- All Python tool config is in `pyproject.toml`.
- Backend: `just dev-be`. Frontend: `just dev-fe`. Both: `just dev`.
- **UI changes** follow [`docs/design.md`](docs/design.md) and are judged in Storybook
  ([`docs/storybook.md`](docs/storybook.md)): add or update a story in
  `src/aleph/web/frontend/src/stories/` for each new screen or state, check it at the
  iPhone and desktop viewports, and attach screenshots (or a recording for interaction)
  to the PR. Stories are for judging design; behavior is still proven by tests.

<!-- Preserve historical native-spec links to the former guidance heading. -->
<a id="two-clients-web-and-ios"></a>

## One Client: Web and Home Screen app

The web client is the only learner client. The
[Home Screen app](docs/prds/home-screen-app.md) shipped to `main` in v1.46.0: the
same web app, installable on the iPhone Home Screen, with Web Push Notifications.
Work on it like any other web change, on a branch off `main`.

The native iOS app was **abandoned** on 2026-11-22. Its
[PRD](docs/prds/ios-app.md), [TDD](docs/tdds/ios-app.md) and
[ADR 0002](docs/adrs/0002-ios-owned-markdown-renderer.md) are kept only as a
record; don't build from them, and don't add Swift code or a generated Swift client.

Keep API schemas and `docs/api.md` consistent with API changes. Render untrusted
Read passages without raw HTML or dangerous URL schemes; verify client behavior
with web tests. Admin tools are web-only.

## Testing Philosophy

- **Red-green TDD:** write a failing test that captures the behavior, then make it pass.
- **Fakes over mocks.** Prefer a small in-memory fake behind a clean interface (a
  `Protocol`) over patching/mocking internals. Design seams so the fake is easy to write;
  a test that needs heavy mocking is telling you the interface is wrong.

## Commit Conventions

**Always use [Conventional Commits](https://www.conventionalcommits.org/) for every
commit.** Releases are automated with `semantic-release` (see `.releaserc.json` and
`.github/workflows/release.yml`): the commit type decides the next version, and a
published release builds the image and deploys to Fly.io ([`docs/deploy.md`](docs/deploy.md)).
Non-conventional commits are ignored by the analyzer — they ship no release and no
deploy.

Format: `type(optional-scope): description`

| Type | Release | Use for |
| ---- | ------- | ------- |
| `fix` | patch (x.y.**z**) | Bug fixes — **and any change that should reach production**, e.g. shipping a UI tweak |
| `feat` | minor (x.**y**.z) | New features |
| `perf` | patch | Performance improvements |
| `docs`, `chore`, `refactor`, `test`, `style`, `ci`, `build` | none | Changes that should not cut a release |

Add `BREAKING CHANGE:` in the commit body (or `!` after the type, e.g. `feat!:`) for a
major (**x**.y.z) bump.

If a change needs to reach production, commit it as `fix` (or `feat`) — a
`chore`/`docs` commit will not deploy.

## More Information

Deeper context lives in [`docs/`](docs/) — browse it on demand rather than
front-loading. Start with:

- [`docs/CONTEXT.md`](docs/CONTEXT.md) — the ubiquitous language (authoritative vocabulary).
- [`docs/architecture.md`](docs/architecture.md) — read before adding modules or changing structure.
- [`docs/development.md`](docs/development.md) — environment setup, debugging, common tasks.
- [`docs/api.md`](docs/api.md) — API endpoint reference.
- [`docs/deploy.md`](docs/deploy.md) — the Fly.io/Neon runbook, the release pipeline, and every production secret.
- [`docs/storybook.md`](docs/storybook.md) — the Storybook design sandbox: every screen and state on realistic mock data.
- [`docs/design.md`](docs/design.md) — the Nocturne design system as applied in the web client: principles, tokens, type, layout.
