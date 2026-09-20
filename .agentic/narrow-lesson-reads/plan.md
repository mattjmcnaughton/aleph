# Plan: Narrow lesson reads

**Slug**: narrow-lesson-reads
**Gate command**: just gate-be
**Optimization target**: Reduce Postgres result bytes without changing behavior; no caching, schema changes, polling changes, or unrelated query optimization.

## Goal
Stop loading whole-path Read passages for lesson unlock checks, path outlines,
and the lesson tutor's path digest. Preserve API responses, ordering, effective
generation states, ownership checks, tutor grounding, and progression rules.

## Acceptance criteria
1. Unlock reads select only lesson ID, position, and completion timestamp, preserving complete/available/locked and missing-lesson results — SQL capture and integration tests.
2. Outline reads omit passage, revision, and error text while preserving ordering and stale generation handling — SQL capture and repository/API tests.
3. Tutor assembly loads content only for the requested lesson and lightweight names/progress for its digest, rejecting missing or foreign lessons — SQL capture and tutor integration tests.
4. Existing backend behavior remains green — backend gate and affected integration suites.

## Verification plan
- Red first: new real-Postgres SQL-capture regressions must fail on full lesson selections.
- Run `DATABASE_URL=postgresql+asyncpg:///aleph uv run pytest tests/integration/test_lesson_reads.py -q`.
- Run `DATABASE_URL=postgresql+asyncpg:///aleph uv run pytest tests/integration/test_repositories.py tests/integration/test_lessons_api.py tests/integration/test_paths_api.py tests/integration/test_tutor_context.py tests/integration/test_tutor_api.py -q` (confirm filenames before execution).
- Run `just gate-be`; frontend is unchanged, so no visual verification is needed.
- Improvement target: zero passage columns in whole-path queries on these three seams, versus full Lesson selections today. This proves eliminated transfer, not a measured production billing percentage.

## Research
**Patterns to mirror**: `repositories/lessons.py` owns ordered lesson reads and SQL effective-state expressions. `services/lessons_read.py:lesson_unlock_state`, `services/paths_read.py:load_path_detail`, and `services/tutor_context.py:assemble_lesson_context` are the affected callers. Existing repository and tutor integration tests cover stale claims, digest states, grounding, and read-only behavior.
**External references**: https://neon.com/docs/introduction/network-transfer
**Domain notes**: `docs/CONTEXT.md` defines Path digest as names and unlock states only; another lesson's Read passage is not tutor scope.

## Environment readiness
- Gate dry-run: PASS (`just --dry-run gate-be`; pytest help works).
- Installed missing local tools: Postgres 15 and `rust-just`; `uv sync --frozen` completed.
- Local disposable database: `postgresql+asyncpg:///aleph`; never use production credentials for tests.
- Dev runtime: YES — FastAPI TestClient booted the actual lifespan; `/healthz` and `/readyz` returned 200.
- Baseline tutor integration suite: 14 passed.
- No worktree init recipe exists. No blockers remain.

## Implementation approach
### Step 1: Narrow the three reads with SQL regression tests
- Add real-Postgres tests that capture executed SELECTs and verify both selected columns and representative results. Run red before implementation.
- Add focused progression and digest reads in the existing lesson repository using SQLAlchemy `load_only(..., raiseload=True)`; preserve the existing full-content method for other consumers.
- Narrow the existing effective-state outline query to its caller's fields, retaining its SQL state computation.
- Switch unlock checks to the progression read. For tutor assembly, explicitly fetch/validate the current lesson before the digest read so SQLAlchemy's identity map cannot leave its content deferred. Remove the obsolete full-list lookup helper.
- Run the backend gate and affected integration tests. Acceptance: 1–4.

### Final step: Validation and verification
- Review the diff and captured query assertions; confirm no unrelated read paths changed.
- Re-run the gate and affected integration tests, recording results in diary.md.
- Make one Conventional Commit; do not push or open a PR.
