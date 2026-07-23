# aleph

A mobile-friendly AI tutoring app for self-directed adult learners studying anything they choose.

## What it does

- **Structured learning path** — a sequential path that guides you skill by skill.
- **Spaced-repetition flashcards** — the AI auto-suggests cards from your lessons; you confirm which ones to keep.
- **Tutor chat loop** — talk to the tutor, and those conversations reshape your future lessons.
- **Light gamification** — streaks and progress tracking, and nothing more.

## Who it's for

Self-directed adult learners who want a focused, mobile-first tutor for any subject — whether that's how medical care is paid for in the United States, the details of clinical trial monitoring, or a technical skill.

## Status

Early brainstorm / prototype.

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [just](https://just.systems/)
- [Docker](https://www.docker.com/) (for dependencies)
- [Node.js](https://nodejs.org/) 20+ and [pnpm](https://pnpm.io/) (for frontend)

### Installation

```sh
# Backend
uv sync --dev
cp .env.example .env

# Frontend
cd src/aleph/web/frontend && pnpm install
```

### Database

```sh
# Start Postgres
docker compose up -d

# Run migrations
uv run alembic upgrade head
```

### Running

```sh
# Start both backend and frontend dev servers
just dev

# Or start separately
just dev-be   # Backend only
just dev-fe   # Frontend only
```

The API will be available at `http://localhost:8000`. The frontend dev server runs on its own port with API proxying.

## Development

```sh
# Check formatting and linting (backend + frontend)
just fmt
just lint

# Fix formatting and linting
just fmt-fix
just lint-fix

# Run type checker
just typecheck

# Backend-only or frontend-only variants
just fmt-be
just lint-fe
```

## Testing

```sh
# Run unit tests (backend + frontend)
just test-unit

# Run all tests
just test-all

# Full pre-push check
just gate
```

## Deployment

```sh
# Build Docker image
docker build -t aleph .

# Run container
docker run -p 8000:8000 aleph
```

## Frontend

The frontend lives in `src/aleph/web/frontend/`, composed from the `frontend-react` Copier template. See [docs/development.md](docs/development.md) for setup and the template-update procedure.

## Architecture

See [docs/architecture.md](docs/architecture.md) for project structure and design decisions.

## API

See [docs/api.md](docs/api.md) for endpoint reference.

## License

[MIT](LICENSE)
