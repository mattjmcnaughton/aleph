"""Flashcard drafting: claim → run the agent → persist; poll; keep (TDD §5.2).

Mirrors ``services/generation.py``'s claim/run/persist shape, deliberately
smaller (D7's "no prefetch, no continuity, no chain"): one claim on
``flashcard_draft_runs`` (``FlashcardRepository.claim_draft_run``, complete and
untouched by this ticket), one model call under
``generation_timeout_seconds`` — reused, not duplicated (D13/§13: "no second
timeout pair") — and one persist.

**Structurally impossible to draft a lesson twice (D7).** The claim is an
``INSERT ... ON CONFLICT (lesson_id) DO UPDATE ... WHERE <claimable>``; an
already-``generating`` or already-``generated`` run matches no update arm and
the claim returns ``None``, so :meth:`FlashcardDraftingService.trigger_draft_run`
is a safe no-op to fire from a mutation's ``onSuccess`` that React may run
twice. Only a ``failed`` run, or a ``generating`` one whose ``started_at`` has
crossed the stale window, is re-claimable.

**The claim/run split is unit-testable where it matters.** The claim + spawn
(:class:`FlashcardDraftingService`) drives real sessions via an injected
``session_factory`` exactly like ``GenerationOrchestrator`` — integration-tested
only, the same posture that class takes. The logic *inside* one claimed run —
load context, call the agent under a timeout, map failure, persist — is pulled
out as :func:`_run_claimed`, taking two narrow ``Protocol`` seams
(:class:`DraftContextLoader`, :class:`DraftRunResolver`) so
``tests/unit/test_flashcard_drafting.py`` drives it against fakes and a
``FunctionModel`` with no database (CLAUDE.md: fakes over mocks).

**Poll and keep are ordinary request-scoped reads/writes**, following
``services/reviews.py``'s shape exactly: a private function behind a
``Protocol`` (unit-tested with a fake store) plus a production entry point that
builds the real :class:`~aleph.repositories.flashcards.FlashcardRepository`.

**"Today" for keep (D4).** Like grading (``services/reviews.py::grade_card``),
this service is the sole owner of "today": ``POST .../keep`` carries
``tz_offset_minutes`` on its body (the same field shape as ``POST /reviews``'s
``GradeCardRequest`` — TDD §6 does not spell this field into the keep payload
example, but the arithmetic has to come from *somewhere* on this request, and a
body field is the one call site `dtos/progress.py`'s ``getTimezoneOffset()``
convention already uses for a write). :func:`keep_flashcard_drafts` resolves
``today = (now - tz_offset_minutes).date()`` exactly as ``_grade`` does, then
``due_on = today + ladder[0]`` — the D1 "enters at rung 0" arithmetic, computed
here (the service), never in the repository (which only stores an already-computed
``due_on``, per its own docstring).

**Instrumentation (TDD §9).** ``_run_claimed`` emits ``flashcards_drafted`` on
every resolution it actually has an ``account_id``/``path_id`` for — a fenced
win of ``persist_drafts`` (``generated``) or ``resolve_failed`` (``failed``);
a vanished/never-generated lesson's context-missing branch emits nothing, the
same posture ``services/generation.py::_run_claimed_lesson`` takes for its own
referential-breakage case. ``keep_flashcard_drafts`` emits ``flashcards_kept``
after ``_keep_drafts`` returns without raising, reading the lesson's drafts
once *before* the keep transaction runs so ``drafted_count`` reflects what
existed going in, not what a discard already removed.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

import structlog
from fastapi import HTTPException, status

from aleph import events
from aleph.agents.flashcard import (
    FlashcardCaps,
    FlashcardDeps,
    build_flashcard_agent,
    build_flashcard_prompt,
)
from aleph.config import settings as global_settings
from aleph.db import new_session
from aleph.models import FlashcardDraftRunState
from aleph.repositories import (
    FlashcardRepository,
    LessonRepository,
    PathRepository,
    QuickCheckRepository,
    UnitRepository,
)
from aleph.services.generation import AGENT_LEVEL, usage_tokens
from aleph.services.openrouter import resolve_model

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Coroutine, Sequence
    from contextlib import AbstractAsyncContextManager
    from datetime import date
    from typing import Any

    from pydantic_ai import Agent
    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.agents.flashcard import FlashcardDrafts
    from aleph.config import Settings
    from aleph.domains.scheduling import LadderDays
    from aleph.models import Flashcard, Level

    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
    Spawn = Callable[[Coroutine[Any, Any, Any]], Any]
    ResolveModel = Callable[[str], Model]

logger = structlog.get_logger(__name__)

# Learner-facing failure text (advisory 9, ``services/generation.py`` precedent):
# never the raw provider/exception text — that is logged with full context
# instead. Stored on ``flashcard_draft_runs.error`` and never rendered on the
# wire (§6's GET payload is ``{state, cards}`` only, no error field) — kept
# generic anyway, on the same "the DB column must never carry a leak" discipline
# the lesson pipeline follows, in case a future surface reads it.
_DRAFT_FAILED_MESSAGE = "Flashcard drafting failed. Please retry."
_DRAFT_TIMEOUT_MESSAGE = "Flashcard drafting timed out. Please retry."
_DRAFT_CONTEXT_MISSING_MESSAGE = "This lesson's content is not available for drafting."


# --------------------------------------------------------------------------- #
# Views — what `routers/v1/flashcards.py` translates to wire DTOs.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DraftCardView:
    """One drafted card, as the poll/keep-screen shows it (§6)."""

    id: uuid.UUID
    front: str
    back: str


@dataclass(frozen=True)
class DraftsView:
    """`GET .../flashcard-drafts`'s payload: `{state, cards}` (§6).

    `state` is `"not_started"` when drafting was never triggered for this
    lesson — `flashcard_draft_runs` is sparse (D7: one row per *drafted*
    lesson), so "no row" is a real, distinct case from `"generating"` a real
    run row would report. `cards` is populated only once `state ==
    "generated"`.
    """

    state: Literal["not_started", "generating", "generated", "failed"]
    cards: tuple[DraftCardView, ...]


@dataclass(frozen=True)
class KeepResultView:
    """`POST .../keep`'s payload: the ids actually kept (§6)."""

    kept_ids: tuple[uuid.UUID, ...]


# --------------------------------------------------------------------------- #
# Poll (`GET .../flashcard-drafts`) — a plain read behind a narrow `Protocol`.
# --------------------------------------------------------------------------- #


class DraftPollStore(Protocol):
    """The repository capability the poll read needs.

    ``get_effective_draft_run_state`` — not the raw row — is the point of the
    seam: the real implementation
    (:meth:`~aleph.repositories.flashcards.FlashcardRepository.get_effective_draft_run_state`)
    reports a stale ``generating`` run as ``failed`` (built through
    ``effective_state_case``, on the database clock), which is what turns a
    crashed drafting worker's permanently-``generating`` row into a surfaced
    retry rather than a dead spinner (§5.6). A fake standing in for that
    contract in tests may compute the same collapse in plain Python — the SQL
    itself is pinned separately, at the repository, by
    ``tests/integration/test_flashcard_drafting_api.py``.
    """

    async def get_effective_draft_run_state(
        self, lesson_id: uuid.UUID
    ) -> FlashcardDraftRunState | None: ...

    async def list_drafts_for_lesson(self, lesson_id: uuid.UUID) -> list[Flashcard]: ...


async def _load_drafts(store: DraftPollStore, lesson_id: uuid.UUID) -> DraftsView:
    """`GET .../flashcard-drafts`'s four states, off the run's **effective** state.

    Reads a stale-collapsed state from the store (never the raw row) — the
    BLOCKER fix: a crashed worker leaves ``flashcard_draft_runs.state`` at
    ``generating`` forever, and without the collapse this would report
    ``"generating"`` forever too, with no retry affordance ever reachable
    (§5.6's retry only renders on the ``failed`` branch).
    """
    state = await store.get_effective_draft_run_state(lesson_id)
    if state is None:
        return DraftsView(state="not_started", cards=())
    if state is FlashcardDraftRunState.GENERATED:
        drafts = await store.list_drafts_for_lesson(lesson_id)
        return DraftsView(
            state="generated",
            cards=tuple(
                DraftCardView(id=draft.id, front=draft.front, back=draft.back)
                for draft in drafts
            ),
        )
    if state is FlashcardDraftRunState.FAILED:
        return DraftsView(state="failed", cards=())
    return DraftsView(state="generating", cards=())


async def load_flashcard_drafts(
    session: AsyncSession, *, lesson_id: uuid.UUID
) -> DraftsView:
    """`GET .../flashcard-drafts`'s whole payload (§6)."""
    return await _load_drafts(FlashcardRepository(session), lesson_id)


# --------------------------------------------------------------------------- #
# Keep (`POST .../keep`) — one transaction, behind a narrow `Protocol`.
# --------------------------------------------------------------------------- #


class DraftKeepStore(Protocol):
    """The repository capability the keep write needs."""

    async def keep_drafts(
        self,
        *,
        lesson_id: uuid.UUID,
        kept_ids: Sequence[uuid.UUID],
        due_on: date,
    ) -> int: ...


def _draft_not_found() -> HTTPException:
    """A `404` for a kept id that is not a draft of this lesson (§5.2/§11).

    Plain `not_found` (no `details.reason`, unlike the `409` envelope
    `services/reviews.py::_conflict` uses) — this is an ownership-shaped
    "does not exist for you", the same posture as an unowned lesson/card
    everywhere else in this codebase (404-never-403).
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="one or more kept ids are not drafts of this lesson",
    )


async def _keep_drafts(
    store: DraftKeepStore,
    *,
    lesson_id: uuid.UUID,
    kept_ids: Sequence[uuid.UUID],
    today: date,
    ladder: LadderDays,
) -> KeepResultView:
    """The §5.2 keep transaction's logic: compute `due_on`, delegate, guard.

    `due_on = today + ladder[0]` is D1's "a freshly kept card enters at rung 0,
    due tomorrow — never today" arithmetic (§5.1/§14), computed **here** (the
    service owns "today" and the ladder), never in the repository (whose own
    docstring says `due_on` arrives "already computed by the caller").

    Raises `404` — and, critically, **never calls `session.commit()`** — when
    `store.keep_drafts` reports fewer rows updated than distinct ids requested:
    the repository's DELETE has already run in the same (uncommitted)
    transaction by the time it returns (its own docstring), so the caller must
    let this exception propagate uncommitted. `routers/v1/flashcards.py` never
    commits before this returns, and the `get_session` dependency rolls back
    the session on an exception that escapes the route (SQLAlchemy's session
    context-manager contract) — so a foreign kept id mutates nothing (§11).
    """
    # `dict.fromkeys` dedupes while preserving the request's order — a plain
    # `set(kept_ids)` round-trip (the smaller fix this replaces) makes the
    # `200` body's `kept_ids` an arbitrary order, which the integration test's
    # own `sorted(...)` comparison was concealing. Deduped exactly once, here:
    # `FlashcardRepository.keep_drafts` receives already-distinct ids and
    # documents that as its input contract rather than deduping a second time.
    unique_kept_ids = tuple(dict.fromkeys(kept_ids))
    due_on = today + timedelta(days=ladder[0])
    kept_count = await store.keep_drafts(
        lesson_id=lesson_id, kept_ids=unique_kept_ids, due_on=due_on
    )
    if kept_count != len(unique_kept_ids):
        raise _draft_not_found()
    return KeepResultView(kept_ids=unique_kept_ids)


async def keep_flashcard_drafts(
    session: AsyncSession,
    *,
    lesson_id: uuid.UUID,
    kept_ids: Sequence[uuid.UUID],
    tz_offset_minutes: int,
    now: datetime | None = None,
) -> KeepResultView:
    """`POST .../keep`'s whole transaction (§5.2/§6).

    Does not commit; the caller (the router) does, once, after this returns —
    exactly `services/reviews.py::grade_card`'s posture, and for the same
    reason (§5.2's "one transaction"). The event this emits therefore lands
    slightly ahead of the actual commit (TDD §9) — the router this ticket does
    not touch is what commits — the same structural trade every emitter in
    this module and `services/reviews.py::grade_card` makes.

    Reads the lesson's pending drafts **before** delegating to `_keep_drafts`
    (whose own repository call deletes every draft this request does not
    keep, in the same uncommitted transaction) so `flashcards_kept`'s
    `drafted_count` is what actually existed going into this request, not what
    survived it. A lesson with no pending drafts at all emits nothing — there
    is no `account_id`/`path_id` to read off an empty list, and the drafts
    screen has nothing to have shown a learner in that case.

    The event's fields are copied into **plain locals up front**, not read off
    the ORM rows afterwards. `_keep_drafts`' delete flushes, which expires
    every loaded `Flashcard` instance; a later `draft.user_id` would then be a
    lazy refresh of a row this transaction has already deleted, issued from
    sync attribute access outside SQLAlchemy's greenlet context — a
    `MissingGreenlet` that 500s an otherwise valid keep. Scalars captured
    before the write cannot expire.
    """
    resolved_now = now if now is not None else datetime.now(UTC)
    today = (resolved_now - timedelta(minutes=tz_offset_minutes)).date()
    repo = FlashcardRepository(session)
    pending_drafts = await repo.list_drafts_for_lesson(lesson_id)
    drafted_count = len(pending_drafts)
    account_id = pending_drafts[0].user_id if pending_drafts else None
    source_path_id = pending_drafts[0].source_path_id if pending_drafts else None
    result = await _keep_drafts(
        repo,
        lesson_id=lesson_id,
        kept_ids=kept_ids,
        today=today,
        ladder=global_settings.flashcard_ladder,
    )
    if account_id is not None:
        events.emit_flashcards_kept(
            account_id=account_id,
            path_id=source_path_id,
            lesson_id=lesson_id,
            drafted_count=drafted_count,
            kept_count=len(result.kept_ids),
        )
    return result


# --------------------------------------------------------------------------- #
# The claimed run's logic (unit-testable: two narrow `Protocol` seams).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DraftContext:
    """Everything one claimed drafting run needs, loaded from the DB once.

    `level` is the DB enum (`aleph.models.Level`); `_run_claimed` maps it
    through `services.generation.AGENT_LEVEL` — the one dict every generation
    pipeline shares (that module's docstring: "a fourth `Level` member must
    land in exactly one dict"), never a second copy here.

    `position_in_path` is the `flashcards_drafted` event's locator field
    (TDD §9) — carried here rather than re-derived, the same "the service
    reads it off the row, once" posture `services/generation.py`'s own
    `_LessonContext.position_in_path` takes.
    """

    user_id: uuid.UUID
    topic: str
    level: Level
    unit_title: str
    lesson_title: str
    read_passage: str
    quick_check_stem: str
    source_path_id: uuid.UUID | None
    source_path_title: str
    source_lesson_title: str
    source_generated_at: datetime
    position_in_path: int


class DraftContextLoader(Protocol):
    """Loads a claimed run's context, or `None` if the lesson cannot be drafted."""

    async def load(self, lesson_id: uuid.UUID) -> DraftContext | None: ...


class DraftRunResolver(Protocol):
    """Resolves a claimed run to `generated` (with its cards) or `failed`."""

    async def persist_drafts(
        self,
        *,
        lesson_id: uuid.UUID,
        fence: datetime,
        context: DraftContext,
        cards: Sequence[tuple[str, str]],
    ) -> bool: ...

    async def resolve_failed(
        self, *, lesson_id: uuid.UUID, fence: datetime, error: str
    ) -> bool: ...


async def _run_claimed(
    context_loader: DraftContextLoader,
    resolver: DraftRunResolver,
    *,
    lesson_id: uuid.UUID,
    fence: datetime,
    timeout_seconds: float,
    resolve_model_fn: ResolveModel,
    model_flashcard: str,
    caps: FlashcardCaps,
    agent: Agent[FlashcardDeps, FlashcardDrafts] | None = None,
) -> None:
    """One claimed run: load context, run the agent under a timeout, persist.

    Maps every failure mode to `resolver.resolve_failed` (§5.4 of the lesson
    pipeline's own table, mirrored exactly): a lesson that vanished or was
    never generated (context missing), a timeout, or any agent error
    (refusal is not a branch here — TDD §5.2: the flashcard agent "has no
    refusal branch", the lesson it drafts from was already generated). Only a
    clean run reaches `persist_drafts`. `agent` is a test seam (defaults to
    the real assembled agent) — `tests/unit/test_flashcard_drafting.py`
    injects nothing here (the module-level `build_flashcard_agent()` already
    binds no model), but a real `Agent[...]` can be passed directly to avoid
    reconstructing one per call in a tight test loop.

    Emits `flashcards_drafted` (TDD §9) on every resolution **from this point
    on** — context missing is the one branch before it that emits nothing (no
    `account_id`/`path_id` to stamp, `services/generation.py`'s own
    referential-breakage posture) — gated in every case on the resolver
    reporting a **fenced win**, so a lost fence (a stale re-claim already owns
    the row) records nothing, exactly `lesson_generated`'s own rule.

    The context-missing branch has no event to gate, but `resolve_failed`
    still reports a fenced win/loss here exactly as it does everywhere else
    (`DraftRunResolver`'s contract, `-> bool` in both implementations) — a
    lost fence is logged (there being no `flashcards_drafted` to skip)
    rather than the return value being silently discarded: a `-> bool` method
    whose result nobody reads is exactly the kind of drift between a
    contract and its use this module's own discipline warns about.
    """
    context = await context_loader.load(lesson_id)
    if context is None:
        resolved = await resolver.resolve_failed(
            lesson_id=lesson_id, fence=fence, error=_DRAFT_CONTEXT_MISSING_MESSAGE
        )
        if not resolved:
            logger.warning(
                "flashcard_draft_context_missing_lost_fence",
                lesson_id=str(lesson_id),
            )
        return

    deps = FlashcardDeps(
        topic=context.topic,
        level=AGENT_LEVEL[context.level],
        unit_title=context.unit_title,
        lesson_title=context.lesson_title,
        read_passage=context.read_passage,
        quick_check_stem=context.quick_check_stem,
        caps=caps,
    )
    model = resolve_model_fn(model_flashcard)
    run_agent = agent if agent is not None else build_flashcard_agent()
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            run = await run_agent.run(
                build_flashcard_prompt(deps), deps=deps, model=model
            )
    except TimeoutError:
        await _resolve_and_emit_drafted_failed(
            resolver,
            context=context,
            lesson_id=lesson_id,
            fence=fence,
            started=started,
            error=_DRAFT_TIMEOUT_MESSAGE,
        )
        return
    except Exception:  # noqa: BLE001 - any agent error maps to failed (§5.2)
        logger.exception("flashcard_draft_generation_failed", lesson_id=str(lesson_id))
        await _resolve_and_emit_drafted_failed(
            resolver,
            context=context,
            lesson_id=lesson_id,
            fence=fence,
            started=started,
            error=_DRAFT_FAILED_MESSAGE,
        )
        return

    duration_ms = round((time.perf_counter() - started) * 1000)
    prompt_tokens, completion_tokens, total_tokens = usage_tokens(run)
    cards = [(card.front, card.back) for card in run.output.cards]
    persisted = await resolver.persist_drafts(
        lesson_id=lesson_id, fence=fence, context=context, cards=cards
    )
    if persisted:
        events.emit_flashcards_drafted(
            account_id=context.user_id,
            path_id=context.source_path_id,
            lesson_id=lesson_id,
            position_in_path=context.position_in_path,
            drafted_count=len(cards),
            outcome="generated",
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )


async def _resolve_and_emit_drafted_failed(
    resolver: DraftRunResolver,
    *,
    context: DraftContext,
    lesson_id: uuid.UUID,
    fence: datetime,
    started: float,
    error: str,
) -> None:
    """Mark the run failed and emit `flashcards_drafted` on a fenced win.

    `duration_ms` is measured from the same `started` mark the success path
    uses (before the model call), so a timeout's duration reads as the timeout
    bound and an agent error's as however long it ran before raising — the
    same clock, whichever branch it took. `drafted_count=0`: nothing was
    persisted.
    """
    ok = await resolver.resolve_failed(lesson_id=lesson_id, fence=fence, error=error)
    if ok:
        events.emit_flashcards_drafted(
            account_id=context.user_id,
            path_id=context.source_path_id,
            lesson_id=lesson_id,
            position_in_path=context.position_in_path,
            drafted_count=0,
            outcome="failed",
            duration_ms=round((time.perf_counter() - started) * 1000),
        )


# --------------------------------------------------------------------------- #
# Production `DraftContextLoader` / `DraftRunResolver`: real repos, own sessions.
# --------------------------------------------------------------------------- #


class _RepoDraftContextLoader:
    """Loads :class:`DraftContext` from the real repositories, own session.

    A `None` return covers every "cannot draft this" case in one place: the
    lesson (or its unit/path) vanished since the claim won, or the lesson's
    content was never actually generated (`read_passage`/`generated_at` unset
    — reachable in principle because lesson completion is gated on *unlock*
    state, not generation state, CONTEXT.md) — every one of these maps to
    `_run_claimed`'s `failed` branch rather than a crash.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def load(self, lesson_id: uuid.UUID) -> DraftContext | None:
        async with self._session_factory() as session:
            lesson = await LessonRepository(session).get(lesson_id)
            if lesson is None or lesson.read_passage is None:
                return None
            if lesson.generated_at is None:
                return None
            path = await PathRepository(session).get(lesson.path_id)
            if path is None:
                return None
            unit = await UnitRepository(session).get(lesson.unit_id)
            if unit is None:
                return None
            quick_check = await QuickCheckRepository(session).get_for_lesson(lesson_id)
            if quick_check is None:
                return None
            return DraftContext(
                user_id=path.user_id,
                topic=path.topic,
                level=path.level,
                unit_title=unit.title,
                lesson_title=lesson.title,
                read_passage=lesson.read_passage,
                quick_check_stem=quick_check.stem,
                source_path_id=path.id,
                # The **display** title (D12's citation is display text), not
                # `path.topic` — `display_title` is the `title or topic`
                # fallback (CONTEXT.md: "Path title... defaults to the Topic
                # until renamed"). `path.title` alone can be `NULL`, which
                # would violate `source_path_title`'s `NOT NULL` column.
                source_path_title=path.display_title,
                source_lesson_title=lesson.title,
                source_generated_at=lesson.generated_at,
                position_in_path=lesson.position_in_path,
            )


class _RepoDraftRunResolver:
    """Resolves a claimed run via the real repository, own session per call.

    Mirrors `GenerationOrchestrator._persist_outline`'s discipline: the mark
    runs first, so a lost fence (a stale re-claim already owns the row) rolls
    back before any card is inserted, rather than inserting rows this call
    then has to unwind.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def persist_drafts(
        self,
        *,
        lesson_id: uuid.UUID,
        fence: datetime,
        context: DraftContext,
        cards: Sequence[tuple[str, str]],
    ) -> bool:
        async with self._session_factory() as session:
            repo = FlashcardRepository(session)
            marked = await repo.mark_draft_run_generated(
                lesson_id=lesson_id, fence=fence
            )
            if not marked:
                await session.rollback()
                return False
            await repo.create_drafts(
                user_id=context.user_id,
                source_lesson_id=lesson_id,
                source_path_id=context.source_path_id,
                source_lesson_title=context.source_lesson_title,
                source_path_title=context.source_path_title,
                source_generated_at=context.source_generated_at,
                cards=cards,
            )
            await session.commit()
            return True

    async def resolve_failed(
        self, *, lesson_id: uuid.UUID, fence: datetime, error: str
    ) -> bool:
        async with self._session_factory() as session:
            ok = await FlashcardRepository(session).mark_draft_run_failed(
                lesson_id=lesson_id, error=error, fence=fence
            )
            if ok:
                await session.commit()
            else:
                await session.rollback()
            return ok


# --------------------------------------------------------------------------- #
# The claim + spawn (integration-tested, the `GenerationOrchestrator` shape).
# --------------------------------------------------------------------------- #


class FlashcardDraftingService:
    """Drives one lesson's drafting run through the claim/run/persist machine.

    Constructed with injectable seams — `session_factory`, `spawn`,
    `resolve_model_fn` — exactly `GenerationOrchestrator`'s shape (§5.2: "reuse
    the pattern, not the code"), so integration tests swap `_spawn` for a
    drainable collector and `_resolve_model` for the deterministic stub the
    same way `tests/integration/conftest.py::CollectingSpawn`/`stub_resolver`
    already do for lesson generation.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory = new_session,
        spawn: Spawn = asyncio.create_task,
        resolve_model_fn: ResolveModel = resolve_model,
        config: Settings = global_settings,
        generation_timeout_seconds: float | None = None,
        stale_after_seconds: float | None = None,
        caps: FlashcardCaps | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._spawn = spawn
        self._resolve_model = resolve_model_fn
        self._config = config
        self._timeout = (
            generation_timeout_seconds
            if generation_timeout_seconds is not None
            else config.generation_timeout_seconds
        )
        self._stale = (
            stale_after_seconds
            if stale_after_seconds is not None
            else config.generation_stale_after_seconds
        )
        # The PRD §6 count band comes from `Settings` (D13's provisional
        # numbers); the word caps stay at `FlashcardCaps`' own defaults — TDD
        # §13's config table lists no per-word-cap setting.
        self._caps = caps or FlashcardCaps(
            count_min=config.flashcard_drafts_min,
            count_max=config.flashcard_drafts_max,
        )

    def trigger_draft_run(self, lesson_id: uuid.UUID) -> None:
        """Fire-and-forget claim + run (§5.2 #2-3) — the router's `202` trigger.

        Mirrors `GenerationOrchestrator.trigger_lesson_generation`: the request
        returns immediately; the client polls `GET .../flashcard-drafts`. The
        claim itself runs inside the spawned task (not synchronously in the
        request), so a lost race (already `generating`/`generated`) is a no-op
        with no extra round trip charged to the request.
        """
        self._spawn(self._claim_and_run(lesson_id))

    async def _claim_and_run(self, lesson_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            fence = await FlashcardRepository(session).claim_draft_run(
                lesson_id=lesson_id, stale_after_seconds=self._stale
            )
            await session.commit()
        if fence is None:
            # Already `generating` (won by a concurrent trigger) or already
            # `generated` (terminal, D7) — a structurally guaranteed no-op.
            return

        # Top-level handler (the `run_outline_task` invariant): any escape
        # from the claimed body — an infra blip in the context load or the
        # persist — records `failed` best-effort under the fence rather than
        # leaving the row wedged in `generating` until the stale window, and
        # never leaks an unretrieved exception out of the spawned task.
        try:
            await _run_claimed(
                _RepoDraftContextLoader(self._session_factory),
                _RepoDraftRunResolver(self._session_factory),
                lesson_id=lesson_id,
                fence=fence,
                timeout_seconds=self._timeout,
                resolve_model_fn=self._resolve_model,
                model_flashcard=self._config.model_flashcard,
                caps=self._caps,
            )
        except Exception:
            logger.exception("flashcard_draft_task_failed", lesson_id=str(lesson_id))
            with contextlib.suppress(Exception):
                await _RepoDraftRunResolver(self._session_factory).resolve_failed(
                    lesson_id=lesson_id, fence=fence, error=_DRAFT_FAILED_MESSAGE
                )


# The module-level singleton `routers/v1/flashcards.py` imports directly — the
# `generation_orchestrator` precedent (`services/generation.py`): production
# wiring is constructed once, cheaply (no I/O), and tests patch its `_spawn`/
# `_resolve_model` seams in place.
flashcard_drafting_service = FlashcardDraftingService()
