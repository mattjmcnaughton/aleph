# Continuous Integration

Four GitHub Actions workflows. [`ci.yml`](../.github/workflows/ci.yml) runs on every
push/PR to `main` (and `claude/ios`); [`ios.yml`](../.github/workflows/ios.yml) builds
and tests the iOS client on PRs and `ios-dev/*` pushes;
[`release.yml`](../.github/workflows/release.yml) runs after a green CI on `main` (and
can be dispatched as a dry run); [`evals.yml`](../.github/workflows/evals.yml) is
dispatch-only because it spends money.

| Workflow | Trigger | Purpose |
| -------- | ------- | ------- |
| **CI** | push/PR to `main` or `claude/ios` | Correctness gates (below) |
| **iOS** | PR to `main` or `claude/ios`, push to `ios-dev/**`, `workflow_dispatch` | The iOS client: Swift lint, Linux package tests, the generated-Swift drift check, macOS build, unit, snapshot and UI tests ([below](#the-ios-workflow-iosyml)) |
| **Release** | after a green CI on `main`, or `workflow_dispatch` | semantic-release → GHCR image → Fly deploy ([deploy.md](deploy.md)) |
| **Evals** | `workflow_dispatch` only | Agent quality measurement ([evals.md](evals.md)) |

## CI (`ci.yml`)

Four independent jobs, all required green. The layout mirrors the sanctioned habagou
shape (gate / integration / e2e / compose-smoke).

| Job | Command | Services | Purpose |
| --- | ------- | -------- | ------- |
| **gate** | `just gate` | none | Formatting (ruff/biome), linting, typecheck (ty/tsc), backend + frontend unit tests, and the contract drift checks: `just openapi-check` (`clients/openapi.json` against the app; fix with `just openapi`) and `just openapi-ts-check` (the web's `schema.d.ts` against `clients/openapi.json`; fix with `just openapi-ts`). The third committed contract file, the generated Swift client, is checked by `clients/ios/scripts/generate-api.sh --check` (fix by running it without `--check`), which needs a Swift toolchain and runs in the iOS workflow's Linux `swift-linux` job, not in `gate`. |
| **integration** | `just test-integration` | Postgres 16 + Keycloak | API against a real per-test Postgres database and the real OIDC code flow. |
| **e2e** | `just test-e2e` | Postgres 16 + Keycloak | Playwright browser suite (phone viewport) against the stub-model backend + dev frontend. |
| **compose-smoke** | `just compose-smoke` | Compose Postgres | Builds the **production image** and proves it serves: `/healthz`, `/readyz` against a migrated database, the SPA shell, and the auth boundary in both directions. |

The first three run the app from a source checkout; `compose-smoke` is the only job
that runs the artifact Fly will run. Full description — what it builds, what it
asserts, and why it cannot touch a developer's database — lives in
[deploy.md § The Compose smoke](deploy.md#the-compose-smoke-just-compose-smoke).

No job calls an LLM provider: the stub model (`services/stub_model.py`) is
deterministic and offline, the live-provider contract tests (`tests/external/`)
are reachable only via `just test-external`, which CI never invokes, and the
eval harness has its own dispatch-only workflow (below) that no required check
depends on.

Runs on the same ref cancel their predecessors (`concurrency`), and each job
uploads its JUnit / Playwright report as an artifact.

## The iOS workflow (`ios.yml`)

Builds and tests the SwiftUI client in `clients/ios/` (iOS TDD §17.1). Its own
`concurrency` group (`ios-<ref>`) cancels superseded runs on the same ref.

| Job | Runner | Runs when | Does |
| --- | ------ | --------- | ---- |
| **changes** | ubuntu | always | The path filter (below). Outputs `ios=true` or `ios=false`. |
| **swift-linux** | ubuntu, container `swift:6.2` | `ios == 'true'` | `swift format lint --strict` on every tracked `.swift` file under `clients/ios/` except the generated client (`Packages/API/Sources/API/Generated/`), using `clients/ios/.swift-format`; `swift test` in `Tools/GenerateAPI` (the contract rewrite); `generate-api.sh --check` (the generated-Swift drift check); `swift test` in the Linux-capable packages `Packages/Core`, `Packages/CoreTestSupport`, `Packages/Markdown` and `Packages/API` (a missing one fails). |
| **ios-gate** | `macos-26` | `ios == 'true'` | Selects `/Applications/Xcode_26.6.app` (`sudo xcode-select -s`), installs XcodeGen 2.46.0, creates the pinned *iPhone 17 / iOS 26.5* simulator, runs `xcodegen generate`, builds with warnings as errors, runs the `Aleph` scheme's unit and snapshot tests, and checks that the Release binary has no `AlephTestTokens` hook. |
| **ios-e2e** | `macos-26` | `ios == 'true'` | Same Xcode and simulator; `brew install postgresql@16`, `uv sync`, `alembic upgrade head`, boots the stub backend (`scripts/e2e_backend.py`) with `E2E_FAKE_ISSUER=1` and `E2E_SIMCTL_UDID`, then runs the `AlephUITests` scheme in the `UITest` configuration. Uploads the `.xcresult` and the backend log. |

**Fake-issuer mode (`ios-e2e`).** The macOS runner has no Docker, so no Keycloak.
With `E2E_FAKE_ISSUER=1` (and only with `ENV=test`), `scripts/e2e_backend.py` serves a
minimal OIDC issuer at `http://127.0.0.1:8000/__e2e__/oidc` (discovery, JWKS and
userinfo, `scripts/e2e_fake_issuer.py`) and points bearer auth at it with
`OIDC_API_AUDIENCE=aleph-api` (iOS TDD D22, §16.3). Its RS256 signing key is committed
and **test-only** (`tests/fixtures/e2e_oidc/signing_key.jwk`); the UI tests launch the
`UITest` build with pre-signed tokens from `clients/ios/AlephUITests/Fixtures/tokens.json`
(learner, admin, expired learner), which `uv run python scripts/mint_e2e_tokens.py`
re-mints and `tests/unit/test_e2e_fake_issuer.py` drift-checks. The real
`BearerAuthenticator` validates every request against that JWKS. Production can never
trust it: the module is never imported by `create_app`, `scripts/` and `tests/` are not
in the image, and `ENV=production` rejects an `/__e2e__/` OIDC issuer or metadata URL.
Real Keycloak bearer coverage stays in `tests/integration/test_bearer_keycloak.py`.

**The path filter.** There is no trigger-level `paths:` filter, because a workflow
that never starts leaves a required check pending forever. Instead `changes` diffs
`git diff --name-only "$base" HEAD`, where `base` is the PR's base SHA, or the push's
`before` SHA. A new branch, a commit it can't find, or a `workflow_dispatch` without
a `base` input runs everything. The other three jobs run only when a changed path
matches one of these (`clients/ios/scripts/ios-relevant.sh`, unit-tested in
`tests/unit/test_ios_ci_scripts.py`):

- `^clients/ios/`
- `^clients/openapi\.json$`
- `^scripts/e2e_backend\.py$`
- `^scripts/e2e_fake_issuer\.py$`
- `^tests/fixtures/e2e_oidc/`
- `^\.github/workflows/ios\.yml$`

Otherwise they're skipped by their `if:`, and a skipped job counts as passing for a
required check. So a backend-only PR never waits on macOS.

**Warnings as errors.** `ios-gate` scans the build log with
`clients/ios/scripts/check-warnings.sh`: any Swift compiler warning in a source file
under `clients/ios/` fails the job. The generated client is exempt, because under
Swift 6.2 every generated file warns that a `package import` is unused, and it is
never edited by hand. Third-party package checkouts and files Xcode generates into
DerivedData are outside `clients/ios/` sources and are ignored too.

**Snapshots.** On a PR or a `workflow_dispatch`, snapshot tests compare against the
committed references in `clients/ios/AlephSnapshotTests/__Snapshots__/` and fail on a
mismatch; the failures and diffs are uploaded as the `ios-gate-compare` artifact. On a
push to `ios-dev/**`, they record instead (below).

**Caching.** `swift-linux` caches SwiftPM's clone cache and `Tools/GenerateAPI/.build`
on the `Package.resolved` files; the macOS jobs cache the cloned package checkouts on
`Package.resolved` and `project.yml`. DerivedData isn't cached. There's no nightly run
on `main`.

### Required checks (a repository setting)

`changes`, `swift-linux`, `ios-gate` and `ios-e2e` are required checks on `main` and
`claude/ios`, next to CI's four jobs. Branch protection is a GitHub setting, not a
file: an owner adds them under **Settings → Branches** (or **Rules → Rulesets**) for
each branch. Keep the job names stable; branch protection refers to them.

### Iterating on `ios-dev/*` (agents without Xcode)

An agent that can't build iOS locally pushes to `ios-dev/<topic>`. There the workflow
runs in **record** mode: `ios-gate` regenerates the snapshot references and `ios-e2e`
the UI-test screenshots, and each commits them back to the same branch as
`chore(ios): record snapshot references and screenshots [skip ci]`
(`clients/ios/scripts/commit-back.sh`). Pull, read the PNGs, repeat, then bring the
commits onto the PR branch, where CI only compares. Read results with the Actions
tab or the job logs. A push to a new `ios-dev/*` branch runs everything; later pushes
diff against the previous head, so a push that changes nothing iOS-relevant skips the
iOS jobs.

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

- **Unit** (`tests/unit/`, `clients/web/src/**/*.test.tsx`) — domains,
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
  specs use the matching `@w1`..`@w23` tags for the same purpose (W10 is
  reserved for the deferred selection-to-quote).

## E2E harness

The Playwright config lives at
[`clients/web/playwright.config.ts`](../clients/web/playwright.config.ts)
with three projects:

| Project | Runs | Why |
| ------- | ---- | --- |
| `setup` | `tests/e2e/auth.setup.ts` | Signs in through the real OIDC code flow twice — once as the `dev` learner, once as `admin-dev` — and saves each session as storage state the journeys replay. |
| `desktop` | `@smoke` only (`testIgnore: journeys/**`) | Keeps the harness honest on a second viewport without paying for the journeys twice. |
| `mobile-390x844` | everything | The §12 phone viewport — the primary target surface, and the only one that runs the journeys. |

Its `webServer` block boots two processes (unless
`BASE_URL` points at a running deployment):

1. **Stub backend** — [`scripts/e2e_backend.py`](../scripts/e2e_backend.py)
   `create_stub_app`: the real API, orchestrator and DB with the deterministic
   stub model wired into every slot (`MODEL_* = stub`) and the per-account rate
   limits lifted. `alembic upgrade head` migrates the target database first;
   `ENV=test` keeps the production stub-guard satisfied.
2. **Dev frontend** — `vite`, proxying `/api` to the backend.

Sentinels force branches deterministically. In a path's **topic**, for the
failure/refusal journeys: `[force-refusal]` (W7), `[force-outline-failure]` /
`[force-lesson-failure:N]` (W8). In a **message**, for the two rails: the
tutor's `[force-tutor-check]` / `[force-tutor-failure]` /
`[force-tutor-refusal]`, and shaping's `[force-proposal-add]` /
`[force-proposal-revise]` / `[force-shaping-decline]` /
`[force-shaping-failure]` (W17–W20). All of them live in
`services/stub_model.py`, are stateless, and are stripped from what the learner
sees.

**Running locally:** `just compose-keycloak-up`, then `just test-e2e`. The suite
signs in for real, so the recipe refuses to start without a reachable realm and
tells you so. It then creates an `aleph_e2e` database, pins the backend to it via
`E2E_DATABASE_URL`, and points Playwright at the machine's preinstalled chromium
through `PW_CHROMIUM_PATH` (the managed download may be a different build). In
CI, `E2E_DATABASE_URL` targets the Postgres service and Playwright installs its
own matching browser, so that local branch is skipped.

## The journeys (AL-090, AL-260, AL-360)

`tests/e2e/journeys/` holds one spec per PRD §8 workflow, tagged `@w1`..`@w34`
(Playwright tag annotations — `pnpm exec playwright test --grep @w3` runs one).
They drive the SPA the way a learner does: real sign-in, real server, real
Postgres, stub model. The conventions below were set by Phase 1's `@w1`..`@w8`
and hold for the tutor's (`@w9`, `@w11`..`@w16`), shaping's (`@w17`..`@w21`),
streaks' (`@w22`, `@w23`) and the analyst's (`@w29`, `@w31` — W30/W32/W33 are
integration cases instead, PRD §7.1) and Flow's (`@w34`)
journeys too.

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
- **The shaping journeys have three timing rules** (AL-360), each of them a
  state to wait for rather than a sleep, and each learned from a flake:
  1. **Wait for the Revision target to be `generated` before Apply or Undo.** A
     lesson mid-write is refused (`409 target_generating`), and completing the
     lesson before it is exactly what advances the prefetch window onto it —
     `waitForLessonGenerated` is the gate (`fixtures/journey.ts`).
  2. **Collapse the shaping rail before working the path rail.** At the phone
     viewport the rail is a sheet *over* the path view, and ghost rows live only
     while a Proposal is pending in the open thread — so a spec that walks into
     a lesson calls `closeShapingRail` first and asserts against real rows.
  3. **Close the Change history sheet before the rail.** The sheet covers the
     conversation and the rail's own collapse control, so the two closes are
     ordered: `closeChangeHistory`, then `closeShapingRail`.
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
