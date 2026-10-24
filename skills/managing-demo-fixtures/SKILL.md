---
name: managing-demo-fixtures
description: Loads, inspects, resets, and edits Aleph's fictional demo learner data. Use when preparing a demo or testing Tutor with rich learning history.
---

# Managing demo fixtures

Use the checked-in `fixtures/demo/healthtech-explorer.json` pack and
`scripts/demo_fixtures.py` from the repository root. The pack is authored fiction,
not a database export or evidence of model quality. Never copy personal learner
data into it. Read `docs/CONTEXT.md` before changing its vocabulary.

## Persona and content

The learner is a healthtech software engineer interested in AI, programming,
healthtech, music, and economics, with about 20 minutes a day and openness to
unrelated discoveries. Eight paths contain 32 authored Read passages and Quick
checks, with 14 completed lessons; four Beats contain 12 authored demo Briefs.
Tutor starts with a Learning focus but no invented conversation or recommendations.

## Safe workflow

1. Inspect the runtime and database destination without printing credentials.
   Use only a disposable local PostgreSQL database named `aleph_demo_*`, or the
   dedicated `aleph_tutor_interactive` database. Never use production, a tunnel to
   shared infrastructure, or another learner's account. Loopback alone does not
   prove that a database is disposable.
2. Validate offline: `uv run python -m scripts.demo_fixtures validate`.
3. Explicitly supply `ALEPH_DEMO_DATABASE_URL` privately. There is deliberately no
   fallback to the application's `DATABASE_URL`. A fresh local database must
   already have the application's Alembic migrations applied; migrate only after
   confirming its disposable identity.
4. Inspect: `uv run python -m scripts.demo_fixtures status`.
5. Load: `uv run python -m scripts.demo_fixtures load`. An existing fixture account
   is preserved, including interactive edits. IDs are stable. Dates are relative
   to UTC today; use `--anchor-date YYYY-MM-DD` for repeatable historical captures.
6. Reset only when the user authorizes replacing this fictional account's state.
   Stop its runtime/workers first, preserving their service configuration. Confirm
   no operations are running. Run
   `uv run python -m scripts.demo_fixtures reset --confirm-reset healthtech-explorer`.
   This deletes this account's history, recommendations, and learning activity,
   reseeds it transactionally, and advances the Tutor epoch. It must not delete
   other accounts. Restart the same runtime and inspect `status` again.
7. Open a fresh Tutor tab after reset so old in-tab recovery requests are discarded.
   Verify Home, a path, a Read passage/Quick check, a Beat/Brief, and the Learning
   focus through the app. Do not trigger a paid Tutor operation as a fixture check.

The fixed account ID is `00dadd00-0000-4000-8000-000000000001`; its identity is
`https://demo.aleph.invalid` / `healthtech-explorer`. The loader also recognizes the
original isolated Tutor demo identity for an explicitly authorized reset.
Authentication remains the runtime's responsibility; do not add an unauthenticated
impersonation endpoint. Keep demo access restricted to its intended audience.

## Editing the saved pack

Edit the JSON, not a dump of the interactive database. Keep useful substantive
passages, unambiguous Quick checks, coherent chronological completion prefixes,
unique slugs, and distinct Brief dates. Briefs must identify themselves as authored
demo material, without fabricated citations or current-news claims. Keep medical
content educational, not patient advice. Validate after editing and run:

```sh
uv run pytest tests/unit/test_demo_fixtures.py
uv run pytest tests/integration/test_demo_fixtures.py
```

The integration suite creates disposable PostgreSQL databases using the repo's
existing test harness. Confirm its local database configuration before running.

The loader makes no model/retrieval calls and stores stub overrides for seeded
paths and Beats. This does not disable a separately running app's Tutor, scheduler,
or retrieval provider. Use the existing isolated demo runtime with those disabled
or stubbed; a live Tutor needs separate spend authorization. Fixtures demonstrate
application state, not natural-language intent or recommendation quality.

Canonical skill source is `skills/managing-demo-fixtures/`; `.agents/skills` and
`.claude/skills` are relative symlinks to `../skills`. Edit only the canonical file.
