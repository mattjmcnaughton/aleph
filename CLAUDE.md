# CLAUDE.md

Mobile-friendly AI tutor: name a topic, get a generated learning path.

Python web application using FastAPI, uvicorn, OpenTelemetry, uv, ruff, ty, and pytest. Web frontend lives in `src/aleph/web/frontend/`; the native SwiftUI iPhone app lives in `ios/`. The two are kept at feature parity (see "Two Clients" below).

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
| `just evals` | Run the agent eval harness (needs `OPENROUTER_API_KEY`; `--smoke` = offline, `--agreement` = judge calibration) |
| `just gate` | Fast pre-push check (fmt + lint + typecheck + test-unit) |
| `just gate-expensive` | Full check (gate + integration + e2e) |
| `just gate-external` | Everything (gate-expensive + external) |
| `just compose-smoke` | Build the production image and smoke it over HTTP (see `docs/deploy.md`) |
| `just dev` | Start backend and frontend dev servers |
| `just dev-be` | Start backend dev server only |
| `just dev-fe` | Start frontend dev server only |

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
evals/                      # Agent eval harness (dev-only, never packaged;
                            # opt-in runs — see docs/evals.md)
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
- **Agent evals** (`evals/`) are opt-in and cost money: `just evals` locally (or
  `just evals --smoke` offline), or dispatch the `Evals` GitHub Actions workflow. Never
  part of `just gate` or the CI gate. See `docs/evals.md`.
- All Python tool config is in `pyproject.toml`.
- Backend: `just dev-be`. Frontend: `just dev-fe`. Both: `just dev`.

## Two Clients: Web and iOS

aleph has two frontends over one API: the React web app (`src/aleph/web/frontend/`)
and the native SwiftUI iPhone app (`ios/`, see
[`docs/prds/ios-app.md`](docs/prds/ios-app.md)). **They are kept at feature parity.**

- **A user-facing change lands in both clients in the same PR.** That covers a new
  screen, a changed flow, new copy, a new state, or a new API field the UI shows. If
  one side genuinely can't follow in that PR, open a tracked issue for it and link it
  in the PR description. Never let the two drift silently.
- **The API is the contract, and both clients use it unchanged.** Any change under
  `/api/v1` regenerates the committed OpenAPI document and the Swift client in the
  same PR. Update `docs/api.md` as you already do. The web and iOS builds must both
  pass.
- **Rules that live in a client are written twice and tested twice.** For example:
  the resume target, the polling cadence and give-up time, Day arithmetic and
  `tz_offset_minutes`, streak copy, Markdown and Mermaid handling. When you change one
  on the web, change and test it in Swift too, and the reverse. Prefer moving a rule to
  the server when that's reasonable, so it exists once.
- **Same vocabulary.** [`docs/CONTEXT.md`](docs/CONTEXT.md) applies to Swift type names
  and UI copy exactly as it does to Python and TypeScript.
- **Same workflow IDs.** An XCUITest that proves a workflow carries the same `W`-number
  as its Playwright journey.
- **Auth differs, on purpose.** The web uses the first-party session cookie. iOS uses
  OIDC (PKCE) bearer tokens. Both resolve to the same user. Don't merge the two flows.
- **Untrusted model output.** Both clients render the Read passage with no raw HTML and
  with dangerous URL schemes stripped.
- **Screenshots.** iOS snapshot PNGs are committed under `ios/`, so review visual
  changes from the PR diff. Use the Playwright suite the same way on the web side.

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

**iOS-only changes** must not deploy the backend. Until `.releaserc.json` excludes an
`ios` scope, commit them as non-releasing types (`chore(ios):`, `ci(ios):`,
`test(ios):`). The iOS app ships through its own workflow: a TestFlight upload on
every merge that touches `ios/`, a monthly re-upload before the 90-day build expiry,
and a manual dispatch.

## More Information

Deeper context lives in [`docs/`](docs/) — browse it on demand rather than
front-loading. Start with:

- [`docs/CONTEXT.md`](docs/CONTEXT.md) — the ubiquitous language (authoritative vocabulary).
- [`docs/architecture.md`](docs/architecture.md) — read before adding modules or changing structure.
- [`docs/development.md`](docs/development.md) — environment setup, debugging, common tasks.
- [`docs/api.md`](docs/api.md) — API endpoint reference.
- [`docs/evals.md`](docs/evals.md) — the agent eval harness (`evals/`) and evaluation strategy.
- [`docs/deploy.md`](docs/deploy.md) — the Fly.io/Neon runbook, the release pipeline, and every production secret.
