# Continuous Integration

GitHub Actions workflow in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml),
run on every push/PR to `main`. Three independent jobs, all required green. The
layout mirrors the sanctioned habagou shape (gate / integration / e2e).

| Job | Command | Services | Purpose |
| --- | ------- | -------- | ------- |
| **gate** | `just gate` | none | Formatting (ruff/biome), linting, typecheck (ty/tsc), backend + frontend unit tests. |
| **integration** | `just test-integration` | Postgres 16 + Keycloak | API against a real per-test Postgres database and the real OIDC code flow. |
| **e2e** | `just test-e2e` | Postgres 16 + Keycloak | Playwright browser suite (phone viewport) against the stub-model backend + dev frontend. |

No job calls an LLM provider: the stub model (`services/stub_model.py`) is
deterministic and offline, and the live-provider contract tests
(`tests/external/`) are reachable only via `just test-external`, which CI never
invokes.

Runs on the same ref cancel their predecessors (`concurrency`), and each job
uploads its JUnit / Playwright report as an artifact.

## Test layers and markers (TDD §12)

- **Unit** (`tests/unit/`, `src/aleph/web/frontend/src/**/*.test.tsx`) — domains,
  validators, DTO mapping, the stub model, frontend components. No I/O.
- **Integration** (`tests/integration/`) — API against a real Postgres (the
  per-test template-database clone pattern in `conftest.py`) plus real Keycloak
  for the OIDC flow. Migrations are owned by the conftest.
- **E2E** — the Playwright suite (below).
- **External** (`tests/external/`) — one live outline + one live lesson round
  trip against real OpenRouter models: a drift canary, not a quality measure
  (quality is the §11 eval harness's job).

Two pytest markers are registered in `pyproject.toml`:

- `@pytest.mark.external` — tags a live-provider test. **Convention for
  `tests/external/`:** every test there carries this marker and is
  **keyless-safe** — it skips cleanly when `OPENROUTER_API_KEY` is unset, so
  `just test-external` can run without credentials and never spends money or
  hangs by accident.
- `@pytest.mark.workflow("W1")` — tags a test that proves a named PRD workflow.
  Shared vocabulary from PRD → test → trace only; there is **no** enforcement
  machinery (no packaged catalog, no coverage-verification job). Code review, not
  CI tooling, checks that a workflow change updates a meaningful test. Playwright
  specs use `@w1`..`@w8` tags for the same purpose.

## E2E harness

The Playwright config lives at
[`src/aleph/web/frontend/playwright.config.ts`](../src/aleph/web/frontend/playwright.config.ts)
with two projects — `desktop` and `mobile-390x844` (the §12 phone viewport, the
primary target surface). Its `webServer` block boots two processes (unless
`BASE_URL` points at a running deployment):

1. **Stub backend** — [`scripts/e2e_backend.py`](../scripts/e2e_backend.py)
   `create_stub_app`: the real API, orchestrator and DB with the deterministic
   stub model wired into every slot (`MODEL_* = stub`) and the per-account rate
   limits lifted. `alembic upgrade head` migrates the target database first;
   `ENV=test` keeps the production stub-guard satisfied.
2. **Dev frontend** — `vite`, proxying `/api` to the backend.

Sentinel topics force branches deterministically for the failure/refusal
journeys: `[force-refusal]` (W7), `[force-outline-failure]` /
`[force-lesson-failure:N]` (W8) — see `services/stub_model.py`.

**Running locally:** `just test-e2e`. It creates an `aleph_e2e` database, pins
the backend to it via `E2E_DATABASE_URL`, and points Playwright at the machine's
preinstalled chromium through `PW_CHROMIUM_PATH` (the managed download may be a
different build). In CI, `E2E_DATABASE_URL` targets the Postgres service and
Playwright installs its own matching browser, so that local branch is skipped.

**Adding the W1–W8 journeys (AL-090):** drop new specs beside
`tests/e2e/smoke.spec.ts`, tagged `@w1`..`@w8`, driving the SPA against this same
stub-model harness. The current `smoke.spec.ts` is the skeleton — it proves the
harness boots end to end (session gate → sign-in surface renders) at 390x844.
