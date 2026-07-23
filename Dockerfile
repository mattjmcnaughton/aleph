## Stage 1: Dependencies
FROM python:3.12-slim AS deps

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen

## Stage 2: Build
FROM deps AS build

COPY . .

## Stage 3: Production
FROM python:3.12-slim AS production

LABEL org.opencontainers.image.title="aleph"
LABEL org.opencontainers.image.description="Mobile-friendly AI tutor: name a topic, get a generated learning path"

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid 1000 --create-home app

WORKDIR /app
COPY --from=build --chown=app:app /app /app

USER app

EXPOSE 8000

# --proxy-headers / --forwarded-allow-ips: behind Fly's TLS-terminating proxy
# the app sees plain HTTP; without these uvicorn ignores X-Forwarded-Proto, so
# the OIDC callback ``redirect_uri`` is built as http:// (breaking prod login)
# and ``Secure`` session cookies misbehave. Mirrors habagou's production CMD.
CMD ["uv", "run", "uvicorn", "aleph.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
