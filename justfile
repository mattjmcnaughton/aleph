fe_dir := "src/aleph/web/frontend"

# Check formatting (backend + frontend)
fmt: fmt-be fmt-fe

# Check backend formatting
fmt-be:
    uv run ruff format --check .

# Check frontend formatting
fmt-fe:
    cd {{fe_dir}} && pnpm run fmt

# Fix formatting (backend + frontend)
fmt-fix: fmt-fix-be fmt-fix-fe

# Fix backend formatting
fmt-fix-be:
    uv run ruff format .

# Fix frontend formatting
fmt-fix-fe:
    cd {{fe_dir}} && pnpm run fmt-fix

# Check linting (backend + frontend)
lint: lint-be lint-fe

# Check backend linting
lint-be:
    uv run ruff check .

# Check frontend linting
lint-fe:
    cd {{fe_dir}} && pnpm run lint

# Fix linting (backend + frontend)
lint-fix: lint-fix-be lint-fix-fe

# Fix backend linting
lint-fix-be:
    uv run ruff check --fix .

# Fix frontend linting
lint-fix-fe:
    cd {{fe_dir}} && pnpm run lint-fix

# Run type checker (backend + frontend)
typecheck: typecheck-be typecheck-fe

# Run backend type checker
typecheck-be:
    uv run ty check

# Run frontend type checker
typecheck-fe:
    cd {{fe_dir}} && pnpm run typecheck

# Run all tests
test-all: test-unit test-integration test-e2e

# Run unit tests (backend + frontend)
test-unit: test-unit-be test-unit-fe

# Run backend unit tests
test-unit-be:
    uv run pytest tests/unit

# Run frontend unit tests
test-unit-fe:
    cd {{fe_dir}} && pnpm run test

# Run integration tests
test-integration:
    uv run pytest -n auto tests/integration

# Run e2e tests (Playwright browser suite, phone viewport — TDD §12)
test-e2e:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p .artifacts/test-results
    # Local runs (no E2E_DATABASE_URL, not CI): create the ephemeral e2e database
    # the Playwright webServer migrates + boots the stub backend against, and
    # point Playwright at the machine's preinstalled chromium (the managed
    # download may be a different build). CI sets E2E_DATABASE_URL to its
    # Postgres service (already created) and installs its own matching browser.
    # The W1-W8 journeys sign in through the real dev realm (TDD §12), so
    # Keycloak has to be up before Playwright starts. Fail here with the fix
    # rather than 30s later inside the browser.
    issuer="${OIDC_ISSUER:-http://127.0.0.1:18080/realms/aleph}"
    if command -v curl >/dev/null 2>&1; then
        if ! curl -fsS -o /dev/null "${issuer}/.well-known/openid-configuration"; then
            echo "e2e needs Keycloak at ${issuer} — run \`just compose-keycloak-up\`" >&2
            exit 1
        fi
    fi
    if [ -z "${E2E_DATABASE_URL:-}" ] && [ -z "${CI:-}" ]; then
        PGPASSWORD="${PGPASSWORD:-postgres}" psql -h localhost -U "${PGUSER:-postgres}" \
            -c 'CREATE DATABASE aleph_e2e' 2>/dev/null || true
        # Pin the e2e backend to its own database so a DATABASE_URL exported for
        # the integration suite (e.g. during `just gate-expensive`) never leaks
        # into the browser suite's backend.
        export E2E_DATABASE_URL="postgresql+asyncpg://${PGUSER:-postgres}:${PGPASSWORD:-postgres}@localhost:5432/aleph_e2e"
        if [ -z "${PW_CHROMIUM_PATH:-}" ] && [ -x /opt/pw-browsers/chromium ]; then
            export PW_CHROMIUM_PATH=/opt/pw-browsers/chromium
        fi
    fi
    cd {{fe_dir}} && pnpm exec playwright test

# Run tests that hit external services
test-external:
    uv run pytest -m external

# Run the agent eval harness (needs OPENROUTER_API_KEY; `--smoke` offline, `--agreement` calibration). See docs/evals.md
evals *ARGS:
    uv run python -m evals {{ARGS}}

# Fast pre-push check (backend + frontend)
gate: gate-be gate-fe

# Backend gate
gate-be: fmt-be lint-be typecheck-be test-unit-be

# Frontend gate
gate-fe: fmt-fe lint-fe typecheck-fe test-unit-fe

# Full check
gate-expensive: gate test-integration test-e2e

# Everything including external
gate-external: gate-expensive test-external

# Start backend dev server
dev-be:
    uv run uvicorn aleph.app:app --reload

# Start frontend dev server
dev-fe:
    cd {{fe_dir}} && pnpm run dev

# Start both dev servers
dev:
    #!/usr/bin/env bash
    just dev-be &
    just dev-fe &
    wait

# Start Compose Postgres for native app development (backing service on localhost:5432)
compose-db-up:
    docker compose up -d --wait db

# Start Compose Keycloak with the checked-in aleph dev realm (issuer on localhost:18080)
compose-keycloak-up:
    docker compose up -d --wait keycloak

# Build the production image and smoke the Compose stack over HTTP (docs/deploy.md)
compose-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    # Only the app port is published, and it is overridable because a `just
    # dev-be` may be holding 8000. The smoke's database (`smoke-db`) publishes no
    # host port at all — it is reached over the Compose network — so the
    # machine's own Postgres on 5432 is never involved.
    export ALEPH_APP_PORT="${ALEPH_APP_PORT:-8000}"
    base="http://127.0.0.1:${ALEPH_APP_PORT}"
    smoke_services=(smoke-db migrate app)
    teardown() {
        # Scoped to the smoke's own containers and their *anonymous* volumes. A
        # project-wide `docker compose down -v` would also delete the `pgdata`
        # named volume the dev `db` service keeps a developer's local database
        # in — running the smoke must never cost someone their dev data.
        docker compose rm --stop --force --volumes "${smoke_services[@]}" \
            >/dev/null 2>&1 || true
    }
    cleanup() {
        # Both services, because a failed migration is otherwise invisible: the
        # app never starts, so app-only logs would show nothing at all.
        docker compose logs --no-color --tail 50 migrate app || true
        teardown
    }
    # Registered *before* `up`, so a failure during build, migrate, or startup
    # still prints logs and still tears the stack down.
    trap cleanup EXIT
    teardown
    # `app` pulls in `smoke-db` and the one-shot `migrate` via depends_on;
    # keycloak stays down (the smoke asserts unauthenticated surfaces only).
    # `--wait` blocks until `migrate` has exited 0 and the app's healthcheck
    # passes, so readiness is the compose file's business, not a polling loop's.
    docker compose up --build -d --wait --wait-timeout 300 app
    # Liveness, readiness (a real query against the migrated database), the SPA
    # shell served by the same process, and the auth boundary in both directions.
    curl -fsS "$base/healthz" | grep -q '"status":"ok"'
    curl -fsS "$base/readyz" | grep -q '"status":"ready"'
    curl -fsS "$base/" | grep -q '<div id="root">'
    curl -fsS "$base/api/v1/auth/session" | grep -q '"authenticated":false'
    curl -sS "$base/api/v1/paths" | grep -q '"code":"unauthenticated"'
    echo "compose smoke OK"

# Stop Compose services
compose-down:
    docker compose down
