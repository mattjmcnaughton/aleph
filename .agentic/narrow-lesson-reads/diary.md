# Diary: Narrow lesson reads

**Slug**: narrow-lesson-reads
**Started**: 2026-09-20
**Checkpoints**: no
**TDD**: yes
**Current step**: done

## Step 1: Narrow the three reads with SQL regression tests
**Status**: completed
**Files**: src/aleph/repositories/lessons.py, src/aleph/services/lessons_read.py, src/aleph/services/tutor_context.py, tests/integration/test_lesson_reads.py
**What**: Added progression/digest projections and narrowed the outline SELECT with load_only and raiseload. Tutor content uses an explicit path-scoped single-lesson SELECT, including when a partial instance is already in the identity map. Existing full-row readers remain unchanged.
**Why**: Eliminate repeated whole-path content transfer without caching, changing polling, or altering response contracts. Tutor assembly adds one small query rather than transferring all passages. The explicit content SELECT avoids session.get returning a partial identity-map object.
**TDD evidence**: After fixing a duplicate test-fixture username, the baseline ran 3 failing transfer assertions and 1 passing ownership test. Implementation made them green. Added a parameterized warm-identity-map case: 5 regression cases total.
**Verification**: just gate-be passed (format, lint, types, 1575 unit tests). Six affected integration modules passed 114 tests; tutor send/admission and generation passed another 45. Only existing Starlette 422 deprecation warnings appeared.
**Deviations**: none

## Final step: Validation and verification
**Status**: completed
**What**: Diff reviewed; git diff --check passed. SQL assertions cover progress-only fields, no outline text payloads, exactly one ID-constrained tutor content read, and foreign-lesson rejection. Existing tests verify stale generation states, output contracts, and tutor read-only behavior.
**Final verification**: Repeated just gate-be: 1575 passed, formatting/lint/types passed. Combined nine-module integration run: 159 passed. All four acceptance criteria satisfied.
**Limitations**: Local Postgres verifies selected columns, not production Neon billing savings. No frontend changes, no browser testing, and no external model calls.
**Commit**: One local commit, `fix(db): narrow lesson reads to reduce database transfer`; no push or PR.
