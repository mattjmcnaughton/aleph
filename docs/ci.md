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
deterministic and offline, the live-provider contract tests (`tests/external/`)
are reachable only via `just test-external`, which CI never invokes, and the
eval harness has its own dispatch-only workflow (below) that no required check
depends on.

Runs on the same ref cancel their predecessors (`concurrency`), and each job
uploads its JUnit / Playwright report as an artifact.

## The Evals workflow (opt-in, never required)

[`.github/workflows/evals.yml`](../.github/workflows/evals.yml) runs the agent
eval harness (`evals/`, [docs/evals.md](evals.md)) against the live OpenRouter
provider.

- **`workflow_dispatch` only.** No `push`, no `pull_request`, no `schedule`
  trigger — an eval run costs real money, so it happens only when a human
  dispatches it (Actions tab → Evals → Run workflow). It is never a required
  check and nothing in the CI gate depends on it. Three inputs:
  - `mode` — `seed-set` (generate the seed set and gate on it) or `agreement`
    (calibration only: judge `evals/human_labels.yaml` and report judge↔human
    agreement — no generation, much cheaper, and the run to dispatch after any
    change to the judge prompt or its calibration examples).
  - `models` — comma-separated OpenRouter ids to sweep in `seed-set` mode; empty
    uses the configured `MODEL_OUTLINE` / `MODEL_LESSON`. The judge always stays
    on `MODEL_JUDGE`.
  - `judge` — run Layer 2 (default on). Uncheck for a cheap Layer-1-only
    structural pass; one judge call per artifact is the bulk of a run's cost.
- **Secret:** `OPENROUTER_API_KEY`, a repository secret (**AL-080 — not
  uploaded yet**). Until it lands, a dispatched run fails immediately with the
  harness's exit-2 message.
- **Environment:** `uv sync --frozen --no-dev --group evals` — project deps plus
  the `evals` dependency group only. No Node, no Postgres, no Keycloak: the
  harness is database-free and never boots the app.
- **Results:** the per-case report table and the gate summary land in the job log
  and the job summary (`$GITHUB_STEP_SUMMARY`), and the JSON is uploaded as the
  `eval-report` artifact. The job fails on a harness error, a hard floor
  (Layer 1's refusal-branch correctness / outline caps / lesson bands, or
  Layer 2's `safe` rubric item), or a seed-set pass rate below the ≥ 90% gate.

The harness's *own* plumbing is covered for free in the gate:
`tests/unit/test_evals_harness.py` runs the offline smoke path (real agents,
stub model), `tests/unit/test_evals_judge.py` runs the whole Layer 2 path
against the deterministic stub judge (rubric, prompts, gate arithmetic,
agreement), and `tests/unit/test_packaging.py` proves `evals/` never ships in
the wheel.

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
- **Evals** (`evals/`) — not a pytest layer at all: a separate harness with its
  own runner (`just evals`) and its own dispatch-only workflow. See
  [docs/evals.md](evals.md).

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
with three projects:

| Project | Runs | Why |
| ------- | ---- | --- |
| `setup` | `tests/e2e/auth.setup.ts` | Signs in through the real OIDC code flow twice — once as the `dev` learner, once as `admin-dev` — and saves each session as storage state the journeys replay. |
| `desktop` | `@smoke` only (`testIgnore: journeys/**`) | Keeps the harness honest on a second viewport without paying for the journeys twice. |
| `mobile-390x844` | everything | The §12 phone viewport — the primary target surface, and the only one that runs W1–W8. |

Its `webServer` block boots two processes (unless
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

**Running locally:** `just compose-keycloak-up`, then `just test-e2e`. The suite
signs in for real, so the recipe refuses to start without a reachable realm and
tells you so. It then creates an `aleph_e2e` database, pins the backend to it via
`E2E_DATABASE_URL`, and points Playwright at the machine's preinstalled chromium
through `PW_CHROMIUM_PATH` (the managed download may be a different build). In
CI, `E2E_DATABASE_URL` targets the Postgres service and Playwright installs its
own matching browser, so that local branch is skipped.

## The W1–W8 journeys (AL-090)

`tests/e2e/journeys/` holds one spec per PRD §8 workflow, tagged `@w1`..`@w8`
(Playwright tag annotations — `pnpm exec playwright test --grep @w3` runs one).
They drive the SPA the way a learner does: real sign-in, real server, real
Postgres, stub model.

- **Auth is real, not injected.** `auth.setup.ts` drives `/auth/login` → the
  realm's login form → `/auth/callback` and saves the resulting cookie as
  storage state (`tests/e2e/.auth/`, gitignored); the journeys replay it with
  `test.use({ storageState })`. The `@smoke` specs deliberately do not — they
  assert the signed-out gate. W2 signs in from scratch again, form and all,
  because "resume across sessions" is what it is about. The realm users are the
  checked-in `dev` (learner) and `admin-dev` (admin — the model picker, §5.3).
- **Isolation is by topic, not by database.** Every worker shares one `aleph_e2e`
  database and one learner account, and the journeys exercise the real "Your
  paths" list, so no spec may assume an empty account. Each test coins a unique
  topic (`uniqueTopic`) and asserts through the resulting path's id
  (`[data-path-id=…]`), which makes residue from other specs — and from previous
  runs of the suite — invisible to it. The unique topic is also the stub model's
  seed, so content cannot collide either.
- **Assertions are structural.** "Real content renders" means a non-empty Read
  passage, a 3–4 option Quick check, an Outcome with an explanation, progress
  readouts that add up — never a literal string from the stub.
- **Waits reload rather than stare.** A surface that has legitimately stopped
  polling cannot be waited out at any timeout, so the generation waits reload
  between attempts instead. Which waits do that, which deliberately do not (the
  post-completion beats assert *in place*, so a stale surface fails rather than
  being refreshed out from under the assertion), and why, is narrated once — in
  the `waitWithReload` / `expectRailStateInPlace` docstrings in
  `tests/e2e/fixtures/journey.ts`. Assertions never read a value once and
  compare: every one of them retries.
- **W6 uses two paths on one topic.** Proving the answer is hidden needs
  something to look for, and the correct answer is exactly what the learner may
  not see. Since the stub is deterministic in (topic, position), a second path on
  the same topic is the same lesson: the first path's Attempt discloses the key
  legitimately, and the second path is then a lesson whose answer the test knows
  but whose DOM must not contain it — and knowing it is what lets the journey aim
  at each Outcome branch deliberately.
- **W8 does not assert "retry then succeeds."** The sentinel lives in the path's
  topic and retry re-runs that same topic, so the stub fails it again by design.
  The journey proves the retry is offered, that taking it is a real round trip
  landing on a live surface rather than a dead spinner, and that the documented
  recovery works. The half it cannot reach — a *transient* failure whose retry
  succeeds and whose learner carries on down the same path — is proven where a
  fail-once model can be injected at the model-resolution seam:
  `test_retry_after_a_transient_failure_recovers_the_same_path` in
  `tests/integration/test_paths_api.py` (alongside
  `test_retry_reclaims_failed_outline`, which pins the re-claim itself through
  the claim stamp). Between them the workflow is witnessed end to end.
