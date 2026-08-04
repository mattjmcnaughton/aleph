"""Unit tests for `aleph.services.flashcard_drafting` (Phase 3 TDD §11).

Against fakes behind `Protocol`s (CLAUDE.md: fakes over mocks, never patching
internals) — no session, no Postgres, no network:

* `_run_claimed` — the claimed run's logic (context load, agent call under a
  timeout, failure mapping, persist) against a fake `DraftContextLoader` +
  `DraftRunResolver` and a real assembled agent driven by a `FunctionModel`
  (mirrors `test_flashcard_agent.py`'s own harness).
* `_load_drafts` — the poll read's four states against a fake `DraftPollStore`.
* `_keep_drafts` — the keep transaction's due-date arithmetic and its
  mutates-nothing-on-mismatch guard against a fake `DraftKeepStore`.
* `DailyRateLimiter.check_flashcard_draft_generation` — the new cap this
  ticket adds to `services/rate_limit.py`, against a small fake `UsageCounter`
  (the `tests/unit/test_rate_limit.py` shape, respelled locally here since
  that file is not part of this ticket's edit scope).

The claim + spawn (`FlashcardDraftingService`) drives real sessions via an
injected `session_factory` exactly like `GenerationOrchestrator` — untestable
without a database by the same logic that leaves `GenerationOrchestrator`
itself covered only by integration tests (`tests/unit/test_generation_service.py`
tests only its pure helpers). `tests/integration/test_flashcard_drafting_api.py`
is where the claim/spawn/HTTP surface is exercised end to end.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import HTTPException
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from structlog.testing import capture_logs

from aleph.agents.flashcard import FlashcardCaps, build_flashcard_agent
from aleph.models import Flashcard, FlashcardDraftRunState, Level
from aleph.services.flashcard_drafting import (
    DraftContext,
    _draft_not_found,
    _keep_drafts,
    _load_drafts,
    _run_claimed,
)
from aleph.services.rate_limit import DailyRateLimiter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo
    from pydantic_ai.tools import ToolDefinition


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_LESSON_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()
_PATH_ID = uuid.uuid4()
_FENCE = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
_GENERATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


_BASE_CONTEXT = DraftContext(
    user_id=_USER_ID,
    topic="Rust ownership",
    level=Level.SOME_EXPERIENCE,
    unit_title="Foundations",
    lesson_title="Ownership basics",
    read_passage="Rust tracks ownership of every value. " * 10,
    quick_check_stem="",
    source_path_id=_PATH_ID,
    source_path_title="Learn Rust",
    source_lesson_title="Ownership basics",
    source_generated_at=_GENERATED_AT,
    position_in_path=0,
)


def _context(**overrides: Any) -> DraftContext:
    """One base context, varied by field.

    `replace` rather than a `dict[str, object]` splat: the dataclass is frozen
    and fully typed, so this keeps a typo'd or wrongly-typed override a static
    error instead of one that only surfaces when the field is finally read.
    """
    return replace(_BASE_CONTEXT, **overrides)


# --------------------------------------------------------------------------- #
# Fakes (CLAUDE.md: fakes over mocks)
# --------------------------------------------------------------------------- #


@dataclass
class _FakeContextLoader:
    context: DraftContext | None

    async def load(self, lesson_id: uuid.UUID) -> DraftContext | None:
        return self.context


@dataclass
class _RecordingResolver:
    persist_result: bool = True
    failed_result: bool = True
    persisted: list[tuple[uuid.UUID, datetime, DraftContext, list[tuple[str, str]]]] = (
        field(default_factory=list)
    )
    failed: list[tuple[uuid.UUID, datetime, str]] = field(default_factory=list)

    async def persist_drafts(
        self,
        *,
        lesson_id: uuid.UUID,
        fence: datetime,
        context: DraftContext,
        cards: Sequence[tuple[str, str]],
    ) -> bool:
        self.persisted.append((lesson_id, fence, context, list(cards)))
        return self.persist_result

    async def resolve_failed(
        self, *, lesson_id: uuid.UUID, fence: datetime, error: str
    ) -> bool:
        self.failed.append((lesson_id, fence, error))
        return self.failed_result


def _flashcard_tool(output_tools: Sequence[ToolDefinition]) -> ToolDefinition:
    for tool in output_tools:
        if "cards" in tool.parameters_json_schema.get("properties", {}):
            return tool
    raise AssertionError("no output tool declares 'cards'")


def _valid_cards(count: int = 4) -> list[dict[str, str]]:
    return [
        {
            "front": f"Name one distinct fact from this lesson ({i}).",
            "back": f"A short, self-contained answer to fact {i}, standing alone.",
        }
        for i in range(count)
    ]


class _ValidDraftsResponder:
    """A `FunctionModel` callback returning a valid `FlashcardDrafts` payload."""

    __name__ = "valid_drafts_responder"

    def __call__(
        self, messages: Sequence[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        tool = _flashcard_tool(info.output_tools)
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"cards": _valid_cards()})]
        )


class _RaisingResponder:
    """A `FunctionModel` callback that always errors — the generic-failure path."""

    __name__ = "raising_responder"

    def __call__(
        self, messages: Sequence[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        raise RuntimeError("the provider is on fire")


async def _slow_responder(
    messages: Sequence[ModelMessage], info: AgentInfo
) -> ModelResponse:
    """An async `FunctionModel` callback that outlives a short timeout.

    Must be a plain coroutine **function** (not a callable class instance):
    `FunctionModel.request` dispatches on `inspect.iscoroutinefunction(self.function)`,
    which is only true for a real function/bound-method object — a class
    instance whose `__call__` is `async def` fails that check and gets routed
    through the *sync* executor path instead, where it would return an
    unawaited coroutine rather than actually racing `asyncio.timeout`.
    """
    await asyncio.sleep(1.0)
    tool = _flashcard_tool(info.output_tools)
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool.name, args={"cards": _valid_cards()})]
    )


def _resolve_model_fn(model: FunctionModel):  # noqa: ANN201 - test seam, inferred fine
    return lambda _model_id: model


async def _run(
    *,
    context_loader: _FakeContextLoader,
    resolver: _RecordingResolver,
    model: FunctionModel,
    timeout_seconds: float = 5.0,
) -> None:
    await _run_claimed(
        context_loader,
        resolver,
        lesson_id=_LESSON_ID,
        fence=_FENCE,
        timeout_seconds=timeout_seconds,
        resolve_model_fn=_resolve_model_fn(model),
        model_flashcard="stub",
        caps=FlashcardCaps(),
        agent=build_flashcard_agent(),
    )


# --------------------------------------------------------------------------- #
# `_run_claimed`: happy path.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_happy_path_persists_the_agents_cards() -> None:
    context = _context()
    resolver = _RecordingResolver()
    await _run(
        context_loader=_FakeContextLoader(context),
        resolver=resolver,
        model=FunctionModel(_ValidDraftsResponder()),
    )

    assert resolver.failed == []
    assert len(resolver.persisted) == 1
    lesson_id, fence, persisted_context, cards = resolver.persisted[0]
    assert lesson_id == _LESSON_ID
    assert fence == _FENCE
    assert persisted_context is context
    assert cards == [(c["front"], c["back"]) for c in _valid_cards()]


# --------------------------------------------------------------------------- #
# `_run_claimed`: the three failure branches.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_missing_context_resolves_failed_without_calling_the_agent() -> None:
    resolver = _RecordingResolver()
    await _run(
        context_loader=_FakeContextLoader(None),
        resolver=resolver,
        model=FunctionModel(_ValidDraftsResponder()),
    )

    assert resolver.persisted == []
    assert len(resolver.failed) == 1
    lesson_id, fence, error = resolver.failed[0]
    assert lesson_id == _LESSON_ID
    assert fence == _FENCE
    assert "not available" in error


@pytest.mark.anyio
async def test_missing_context_logs_a_lost_fence_instead_of_discarding_it() -> None:
    """The smaller fix: `resolve_failed`'s `-> bool` is read even in the
    context-missing branch, which has no `flashcards_drafted` event to gate
    it on. A lost fence (a stale re-claim already owns the row) is logged
    rather than the return value being silently discarded.
    """
    resolver = _RecordingResolver(failed_result=False)
    with capture_logs() as logs:
        await _run(
            context_loader=_FakeContextLoader(None),
            resolver=resolver,
            model=FunctionModel(_ValidDraftsResponder()),
        )

    assert any(
        entry["event"] == "flashcard_draft_context_missing_lost_fence" for entry in logs
    )


@pytest.mark.anyio
async def test_an_agent_error_resolves_failed_with_a_generic_message() -> None:
    resolver = _RecordingResolver()
    await _run(
        context_loader=_FakeContextLoader(_context()),
        resolver=resolver,
        model=FunctionModel(_RaisingResponder()),
    )

    assert resolver.persisted == []
    assert len(resolver.failed) == 1
    _, _, error = resolver.failed[0]
    # Generic, never the raw provider exception text (advisory 9).
    assert "on fire" not in error
    assert "retry" in error.lower()


@pytest.mark.anyio
async def test_a_timeout_resolves_failed_with_a_timeout_message() -> None:
    resolver = _RecordingResolver()
    await _run(
        context_loader=_FakeContextLoader(_context()),
        resolver=resolver,
        model=FunctionModel(_slow_responder),
        timeout_seconds=0.01,
    )

    assert resolver.persisted == []
    assert len(resolver.failed) == 1
    _, _, error = resolver.failed[0]
    assert "timed out" in error.lower()


# --------------------------------------------------------------------------- #
# `_load_drafts`: the four poll states (§6), off the store's **effective**
# state — the production store is `FlashcardRepository.get_effective_draft_run_state`,
# which collapses a stale `generating` row to `failed` via `effective_state_case`
# (on the database clock). This fake stands in for that already-collapsed
# contract; the SQL collapse itself is pinned at the repository, at
# integration level (`tests/integration/test_flashcard_drafting_api.py`).
# --------------------------------------------------------------------------- #


@dataclass
class _FakePollStore:
    state: FlashcardDraftRunState | None
    drafts: list[Flashcard] = field(default_factory=list)

    async def get_effective_draft_run_state(
        self, lesson_id: uuid.UUID
    ) -> FlashcardDraftRunState | None:
        return self.state

    async def list_drafts_for_lesson(self, lesson_id: uuid.UUID) -> list[Flashcard]:
        return self.drafts


@pytest.mark.anyio
async def test_poll_with_no_run_row_is_not_started() -> None:
    view = await _load_drafts(_FakePollStore(state=None), _LESSON_ID)
    assert view.state == "not_started"
    assert view.cards == ()


@pytest.mark.anyio
async def test_poll_while_generating_carries_no_cards() -> None:
    view = await _load_drafts(
        _FakePollStore(state=FlashcardDraftRunState.GENERATING), _LESSON_ID
    )
    assert view.state == "generating"
    assert view.cards == ()


@pytest.mark.anyio
async def test_poll_failed_is_retryable_and_carries_no_cards() -> None:
    view = await _load_drafts(
        _FakePollStore(state=FlashcardDraftRunState.FAILED), _LESSON_ID
    )
    assert view.state == "failed"
    assert view.cards == ()


@pytest.mark.anyio
async def test_poll_generated_lists_every_pending_draft() -> None:
    drafts = [
        Flashcard(id=uuid.uuid4(), front="F1", back="B1"),
        Flashcard(id=uuid.uuid4(), front="F2", back="B2"),
    ]
    view = await _load_drafts(
        _FakePollStore(state=FlashcardDraftRunState.GENERATED, drafts=drafts),
        _LESSON_ID,
    )
    assert view.state == "generated"
    assert [(c.front, c.back) for c in view.cards] == [("F1", "B1"), ("F2", "B2")]


# --------------------------------------------------------------------------- #
# `_load_drafts`: the BLOCKER regression — a stale `generating` run must
# report `failed`, never a permanent `"generating"` dead spinner (D7/§5.6).
#
# `FlashcardRepository.get_effective_draft_run_state` performs this collapse
# in SQL, on the **database clock** (`effective_state_case`, mirroring
# `claim_draft_run`'s own stale-aware predicate). This fake mirrors that same
# rule in plain Python — given a raw `state`/`started_at`, a stale window, and
# a fixed `now` — so `_load_drafts`'s consumption of the contract is pinned
# without a database; the SQL collapse itself is pinned at integration level.
# --------------------------------------------------------------------------- #


@dataclass
class _StaleAwareFakePollStore:
    run_state: FlashcardDraftRunState | None
    started_at: datetime | None
    stale_after_seconds: float
    now: datetime
    drafts: list[Flashcard] = field(default_factory=list)

    async def get_effective_draft_run_state(
        self, lesson_id: uuid.UUID
    ) -> FlashcardDraftRunState | None:
        if self.run_state is None:
            return None
        is_stale = (
            self.run_state is FlashcardDraftRunState.GENERATING
            and self.started_at is not None
            and self.now - self.started_at
            >= timedelta(seconds=self.stale_after_seconds)
        )
        return FlashcardDraftRunState.FAILED if is_stale else self.run_state

    async def list_drafts_for_lesson(self, lesson_id: uuid.UUID) -> list[Flashcard]:
        return self.drafts


@pytest.mark.anyio
async def test_poll_reports_a_stale_generating_run_as_failed() -> None:
    """The BLOCKER regression: a crashed drafting worker (a Fly machine
    restart, a task cancelled at shutdown — neither caught by the service's
    own top-level `except Exception`) leaves the row `generating` forever
    with no further `POST` ever coming. Left to the raw stored state, the
    poll would report `"generating"` forever and the retry affordance — only
    rendered on the `failed` branch (§5.6) — would never be reachable. A run
    `generating` since well before the stale window must report `failed`.
    """
    now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    started_at = now - timedelta(seconds=600)  # well past a 180s stale window
    store = _StaleAwareFakePollStore(
        run_state=FlashcardDraftRunState.GENERATING,
        started_at=started_at,
        stale_after_seconds=180,
        now=now,
    )

    view = await _load_drafts(store, _LESSON_ID)

    assert view.state == "failed"
    assert view.cards == ()


@pytest.mark.anyio
async def test_poll_reports_a_fresh_generating_run_as_generating_still() -> None:
    """The converse of the BLOCKER fix: a `generating` run **within** the
    stale window must still report `generating` — the fix must not turn every
    in-flight run into a false `failed`.
    """
    now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    started_at = now - timedelta(seconds=5)
    store = _StaleAwareFakePollStore(
        run_state=FlashcardDraftRunState.GENERATING,
        started_at=started_at,
        stale_after_seconds=180,
        now=now,
    )

    view = await _load_drafts(store, _LESSON_ID)

    assert view.state == "generating"
    assert view.cards == ()


# --------------------------------------------------------------------------- #
# `_keep_drafts`: the due-date arithmetic and the mutates-nothing-on-mismatch guard.
# --------------------------------------------------------------------------- #


@dataclass
class _FakeKeepStore:
    updated_count: int
    calls: list[tuple[uuid.UUID, tuple[uuid.UUID, ...], date]] = field(
        default_factory=list
    )

    async def keep_drafts(
        self,
        *,
        lesson_id: uuid.UUID,
        kept_ids: Sequence[uuid.UUID],
        due_on: date,
    ) -> int:
        self.calls.append((lesson_id, tuple(kept_ids), due_on))
        return self.updated_count


_LADDER = (1, 3, 7, 14, 30)
_TODAY = date(2026, 8, 4)


@pytest.mark.anyio
async def test_keep_computes_due_on_as_today_plus_the_ladders_first_rung() -> None:
    kept_id = uuid.uuid4()
    store = _FakeKeepStore(updated_count=1)

    result = await _keep_drafts(
        store, lesson_id=_LESSON_ID, kept_ids=[kept_id], today=_TODAY, ladder=_LADDER
    )

    assert result.kept_ids == (kept_id,)
    [(lesson_id, kept_ids, due_on)] = store.calls
    assert lesson_id == _LESSON_ID
    assert kept_ids == (kept_id,)
    assert due_on == _TODAY + timedelta(days=1)  # ladder[0] — never today (D1)


@pytest.mark.anyio
async def test_keep_none_is_skip_keep_none() -> None:
    store = _FakeKeepStore(updated_count=0)

    result = await _keep_drafts(
        store, lesson_id=_LESSON_ID, kept_ids=[], today=_TODAY, ladder=_LADDER
    )

    assert result.kept_ids == ()
    [(_, kept_ids, _due)] = store.calls
    assert kept_ids == ()


@pytest.mark.anyio
async def test_keep_dedupes_repeated_ids_before_matching_the_store_count() -> None:
    kept_id = uuid.uuid4()
    store = _FakeKeepStore(updated_count=1)  # the store reports one distinct match

    result = await _keep_drafts(
        store,
        lesson_id=_LESSON_ID,
        kept_ids=[kept_id, kept_id],
        today=_TODAY,
        ladder=_LADDER,
    )

    assert result.kept_ids == (kept_id,)


@pytest.mark.anyio
async def test_keep_preserves_the_requests_order_when_deduping() -> None:
    """The smaller fix: dedup via `dict.fromkeys` preserves the request's
    order — a `set(kept_ids)` round-trip (what this replaces) makes the `200`
    body's `kept_ids` arbitrary rather than the caller's own order, which the
    integration test's `sorted(...)` comparison had been concealing.
    """
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    store = _FakeKeepStore(updated_count=3)

    result = await _keep_drafts(
        store,
        lesson_id=_LESSON_ID,
        # A duplicate (`first` again) and an intentionally non-sorted order —
        # the point is that the *output* order matches the request's first
        # appearances, not `sorted()` and not insertion-into-a-set order.
        kept_ids=[third, first, second, first],
        today=_TODAY,
        ladder=_LADDER,
    )

    assert result.kept_ids == (third, first, second)
    [(_, kept_ids, _due)] = store.calls
    assert kept_ids == (third, first, second)


@pytest.mark.anyio
async def test_a_foreign_kept_id_is_404() -> None:
    # The store reports fewer updated rows than distinct ids requested — the
    # `keep_drafts` repository contract for "one of these ids was not a draft
    # of this lesson" (§5.2/§11).
    store = _FakeKeepStore(updated_count=1)

    with pytest.raises(HTTPException) as excinfo:
        await _keep_drafts(
            store,
            lesson_id=_LESSON_ID,
            kept_ids=[uuid.uuid4(), uuid.uuid4()],
            today=_TODAY,
            ladder=_LADDER,
        )

    assert excinfo.value.status_code == 404
    # The exception raised is exactly `_draft_not_found()`'s shape — asserted
    # directly so a future refactor cannot silently swap in a different 404.
    assert excinfo.value.detail == _draft_not_found().detail


# --------------------------------------------------------------------------- #
# `DailyRateLimiter.check_flashcard_draft_generation` (D13) — the new cap.
# --------------------------------------------------------------------------- #


@dataclass
class _FakeDraftUsage:
    """The one counter `check_flashcard_draft_generation` calls — every other
    `UsageCounter` method is unused by these tests and left unimplemented."""

    counts: dict[uuid.UUID, int] = field(default_factory=dict)

    async def count_flashcard_draft_runs_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int:
        return self.counts.get(user_id, 0)


def _draft_limiter(usage: _FakeDraftUsage, *, cap: int) -> DailyRateLimiter:
    return DailyRateLimiter(
        usage,  # ty: ignore[invalid-argument-type]
        paths_per_day=0,
        lesson_generations_per_day=0,
        tutor_messages_per_day=0,
        flashcard_drafts_per_day=cap,
    )


@pytest.mark.anyio
async def test_under_the_cap_passes() -> None:
    usage = _FakeDraftUsage(counts={_USER_ID: 1})
    limiter = _draft_limiter(usage, cap=2)
    await limiter.check_flashcard_draft_generation(user_id=_USER_ID, is_admin=False)


@pytest.mark.anyio
async def test_at_the_cap_is_429() -> None:
    usage = _FakeDraftUsage(counts={_USER_ID: 2})
    limiter = _draft_limiter(usage, cap=2)

    with pytest.raises(HTTPException) as excinfo:
        await limiter.check_flashcard_draft_generation(user_id=_USER_ID, is_admin=False)
    assert excinfo.value.status_code == 429


@pytest.mark.anyio
async def test_an_admin_is_exempt_even_over_the_cap() -> None:
    usage = _FakeDraftUsage(counts={_USER_ID: 99})
    limiter = _draft_limiter(usage, cap=2)
    await limiter.check_flashcard_draft_generation(user_id=_USER_ID, is_admin=True)


@pytest.mark.anyio
async def test_a_non_positive_cap_disables_the_check() -> None:
    usage = _FakeDraftUsage(counts={_USER_ID: 999})
    limiter = _draft_limiter(usage, cap=0)
    await limiter.check_flashcard_draft_generation(user_id=_USER_ID, is_admin=False)
