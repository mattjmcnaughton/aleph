# Production image (TDD §13/D3, ticket AL-100). Adapted from habagou's.
#
# Three stages:
#   frontend-build — pnpm/Vite build of src/aleph/web/frontend -> dist/
#   backend-build  — uv-installed virtualenv (project deps only) + application code
#   production     — slim runtime: unprivileged user, venv on PATH, uvicorn
#
# One process serves both halves (D15): the built SPA is copied *into the package
# tree* at src/aleph/web/frontend/dist, which is exactly where
# `aleph.web.serve.mount_frontend` looks, so the API and the shell come off the
# same port with no second service and no CDN.

FROM node:22-bookworm-slim AS frontend-deps

WORKDIR /app/src/aleph/web/frontend
# pnpm pinned to the version .github/workflows/ci.yml uses, so a lockfile that
# satisfies CI satisfies the image.
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
# Manifest + lockfile only: this layer is the expensive one and must not be
# invalidated by an edit to a component.
COPY src/aleph/web/frontend/package.json \
  src/aleph/web/frontend/pnpm-lock.yaml \
  src/aleph/web/frontend/.npmrc ./
RUN pnpm install --frozen-lockfile

FROM frontend-deps AS frontend-build

COPY src/aleph/web/frontend ./
RUN pnpm run build

FROM python:3.12-slim AS backend-build

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

# UV_PYTHON_DOWNLOADS=never: the base image's 3.12 already satisfies
# requires-python, so a managed-interpreter download would make the build depend
# on the network for something it already has. UV_LINK_MODE=copy keeps the venv
# self-contained across the stage boundary (no hardlinks into a cache that the
# production stage will not have).
ENV UV_PYTHON_DOWNLOADS=never \
  UV_LINK_MODE=copy \
  UV_COMPILE_BYTECODE=1

WORKDIR /app
# Dependencies as their own layer: they churn far less often than source.
# `--no-dev` installs the project's runtime dependencies only — the `dev` group
# (and the `evals` group it includes) is a non-default group, so the eval harness
# never reaches the image, mirroring the `evals/` never-ships guarantee that
# tests/unit/test_packaging.py pins for the wheel.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project

# Only what production runs: migrations, the package, and the built SPA. `evals/`,
# `tests/`, `docs/`, `queries/` and `scripts/` are peers of `src/` and are simply
# never copied.
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY --from=frontend-build /app/src/aleph/web/frontend/dist ./src/aleph/web/frontend/dist
RUN uv sync --no-dev --frozen

FROM python:3.12-slim AS production

LABEL org.opencontainers.image.title="aleph"
LABEL org.opencontainers.image.description="Mobile-friendly AI tutor: name a topic, get a generated learning path"

RUN groupadd --gid 1000 app \
  && useradd --uid 1000 --gid 1000 --create-home app

WORKDIR /app
COPY --from=backend-build --chown=app:app /app /app
# --chmod so the release command is executable no matter what mode the checkout
# (or a Windows/`flyctl deploy` build context) hands us.
COPY --chown=app:app --chmod=0755 docker/release.sh /app/docker/release.sh

# The venv on PATH rather than `uv run`: `uvicorn` and `alembic` are the two
# entry points production needs, and uv itself is a build-time tool with no job
# in the runtime image.
#
# No HOST/PORT here: the CMD below hardcodes both, and nothing in the app reads
# `Settings.host`/`Settings.port`. Setting them would advertise a knob that does
# not turn.
ENV PATH="/app/.venv/bin:${PATH}"

USER app

EXPOSE 8000

# --proxy-headers / --forwarded-allow-ips: behind Fly's TLS-terminating proxy the
# app sees plain HTTP; without these uvicorn ignores X-Forwarded-Proto, so the
# OIDC callback ``redirect_uri`` is built as http:// (breaking prod login) and
# ``Secure`` session cookies misbehave.
CMD ["uvicorn", "aleph.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
