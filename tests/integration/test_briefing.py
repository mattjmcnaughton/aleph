"""Integration tests for `services/briefing.py` against real Postgres (AL-521,
issue #171, epic #163) — "the phase's correctness heart".

Drives `BriefingService`'s claim -> plan -> retrieve -> find -> gate -> write
-> persist pipeline through its injected `session_factory`/`spawn`/
`resolve_model_fn`/`retriever` seams, exactly `test_generation.py`'s
`make_orchestrator` shape: a `CollectingSpawn` the test drains
deterministically, and a `FunctionModel` at the model-resolution seam whose
callback dispatches on `info.output_tools` to serve BOTH the researcher's and
the analyst's calls (the two only ever differ in which output schema they
register). No network, no real provider — `_FakeRetriever` stands in for a
`Retriever` (fakes over mocks, CLAUDE.md).

Covers TDD §5.7's whole failure-mapping table, the two inherited-contract
guarantees (survivor documents, the skip-line template), and every workflow
this ticket names (W29-W33) plus the claim/cap/stale-recovery properties
`_generation`'s own claim protocol already pins at the repository level
(`test_analyst_schema.py`) — repeated here at the *service* level, because
this is the layer that actually decides whether a run happens at all.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select, update

from aleph import db
from aleph.agents.researcher import RetrievedDocument
from aleph.config import Settings
from aleph.models import Beat, BeatResearchState, Brief, BriefKind, BriefSource, Level
from aleph.repositories import BeatRepository, BriefRepository
from aleph.services.briefing import BriefingService
from aleph.services.retrieval import RetrievalUnavailableError

from .conftest import CollectingSpawn, create_user

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.models.function import AgentInfo
    from pydantic_ai.tools import ToolDefinition

    from aleph.models import User

# --------------------------------------------------------------------------- #
# Fakes: a Retriever (CLAUDE.md: fakes over mocks) and a scripted FunctionModel
# whose callback serves BOTH the researcher's and the analyst's calls.
# --------------------------------------------------------------------------- #


@dataclass
class _FakeRetriever:
    """A `Retriever` returning a fixed document list, or raising on demand."""

    documents: list[RetrievedDocument] = field(default_factory=list)
    unavailable: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def search(self, queries: Sequence[str]) -> list[RetrievedDocument]:
        self.calls.append(tuple(queries))
        if self.unavailable:
            raise RetrievalUnavailableError("forced failure")
        return list(self.documents)


def _doc(
    url: str,
    *,
    publisher: str = "Northlake Gazette",
    title: str = "A report",
    published_on: date | None = date(2026, 7, 30),
    text: str = "Substantial retrieved text about the topic. " * 5,
) -> RetrievedDocument:
    return RetrievedDocument(
        url=url, publisher=publisher, title=title, published_on=published_on, text=text
    )


def _finding_payload(
    claim: str,
    source_urls: list[str],
    *,
    detail: str = "More detail a writer could draw on.",
    happened_on: str | None = "2026-07-30",
) -> dict:
    return {
        "claim": claim,
        "detail": detail,
        "source_urls": source_urls,
        "happened_on": happened_on,
    }


def _tool_with(output_tools: Sequence[ToolDefinition], prop: str) -> ToolDefinition:
    for tool in output_tools:
        if prop in tool.parameters_json_schema.get("properties", {}):
            return tool
    raise AssertionError(f"no output tool declares {prop!r}")


def _tool_props(output_tools: Sequence[ToolDefinition]) -> set[str]:
    props: set[str] = set()
    for tool in output_tools:
        props |= set(tool.parameters_json_schema.get("properties", {}))
    return props


@dataclass
class _PipelineResponder:
    """One `FunctionModel` callback serving both agent calls in the pipeline.

    `researcher` selects the researcher's branch (`"findings"` ->
    `Findings`, `"message"` -> `Refusal`); `analyst`, when the pipeline
    reaches it, selects the analyst's (`"cited_urls"` -> `BriefBody`,
    `"detail"` -> `SkippedNote`). Which agent a given call is *for* is read
    off `info.output_tools`' own schema — the researcher's output type always
    registers a `findings`-carrying tool, the analyst's never does — so one
    callback correctly serves both without the test having to track call
    order itself.
    """

    __name__ = "pipeline_responder"

    researcher: tuple[str, dict]
    analyst: tuple[str, dict] | None = None
    researcher_calls: int = 0
    analyst_calls: int = 0

    def __call__(
        self, messages: Sequence[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        props = _tool_props(info.output_tools)
        if "findings" in props:
            self.researcher_calls += 1
            prop, args = self.researcher
        else:
            assert self.analyst is not None, (
                "the analyst was called but this responder has no "
                "analyst response configured"
            )
            self.analyst_calls += 1
            prop, args = self.analyst
        tool = _tool_with(info.output_tools, prop)
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=args)])


def _resolver(model: FunctionModel) -> Callable[[str], Model]:
    return lambda _model_id: model


# --------------------------------------------------------------------------- #
# Arrange helpers
# --------------------------------------------------------------------------- #


def _make_service(
    *,
    retriever: _FakeRetriever,
    resolve_model_fn: Callable[[str], Model],
    spawn: CollectingSpawn | None = None,
    config: Settings | None = None,
    research_timeout_seconds: float = 30.0,
    stale_after_seconds: float | None = None,
) -> tuple[BriefingService, CollectingSpawn]:
    spawn = spawn or CollectingSpawn()
    service = BriefingService(
        session_factory=lambda: db.async_session(),
        spawn=spawn,
        resolve_model_fn=resolve_model_fn,
        config=config if config is not None else Settings(),
        retriever=retriever,
        research_timeout_seconds=research_timeout_seconds,
        stale_after_seconds=stale_after_seconds,
    )
    return service, spawn


async def _make_beat(
    *,
    user: User,
    topic: str = "EU AI regulation",
    anchor_weekday: int = 0,
    guidance: str | None = None,
) -> uuid.UUID:
    async with db.async_session() as session:
        beat = await BeatRepository(session).create(
            user_id=user.id,
            topic=topic,
            level=Level.SOME_EXPERIENCE,
            anchor_weekday=anchor_weekday,
            guidance=guidance,
        )
        await session.commit()
        return beat.id


async def _force_state(
    beat_id: uuid.UUID,
    *,
    research_state: BeatResearchState,
    research_started_at: datetime,
) -> None:
    """Backdoor a Beat's claim state directly (stale-recovery / retry-guard
    fixtures) — mirrors `test_analyst_schema.py`'s own arrange style."""
    async with db.async_session() as session:
        await session.execute(
            update(Beat)
            .where(Beat.id == beat_id)
            .values(
                research_state=research_state,
                research_started_at=research_started_at,
            )
        )
        await session.commit()


async def _reload_beat(beat_id: uuid.UUID) -> Beat:
    async with db.async_session() as session:
        beat = await BeatRepository(session).get(beat_id)
        assert beat is not None
        return beat


async def _briefs_for_beat(beat_id: uuid.UUID) -> list[Brief]:
    async with db.async_session() as session:
        return await BriefRepository(session).list_for_beat(beat_id)


async def _sources_for_brief(brief_id: uuid.UUID) -> list[BriefSource]:
    async with db.async_session() as session:
        result = await session.execute(
            select(BriefSource)
            .where(BriefSource.brief_id == brief_id)
            .order_by(BriefSource.position)
        )
        return list(result.scalars())


async def _create_user() -> User:
    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
        return user


# --------------------------------------------------------------------------- #
# W29 — first run claimed -> a published Brief with rows in brief_sources.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W29")
async def test_first_run_produces_a_published_brief_with_sources() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)
    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {
                "findings": [
                    _finding_payload(
                        "Something material happened", ["https://example.com/a"]
                    )
                ]
            },
        ),
        analyst=(
            "cited_urls",
            {
                "title": "The backlash arrived",
                "body_markdown": "Northlake published a review.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE

    briefs = await _briefs_for_beat(beat_id)
    assert len(briefs) == 1
    brief = briefs[0]
    assert brief.kind is BriefKind.PUBLISHED
    assert brief.number == 1
    assert brief.published_on == date(2026, 8, 3)
    assert brief.title == "The backlash arrived"

    sources = await _sources_for_brief(brief.id)
    assert len(sources) == 1
    assert sources[0].url == "https://example.com/a"
    assert sources[0].publisher == "Northlake Gazette"


# --------------------------------------------------------------------------- #
# W30 — a second Brief's builds_on resolves to the first, and re-reports none
# of its claims.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W30")
async def test_second_brief_builds_on_the_first_and_reports_no_repeated_claims() -> (
    None
):
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    # Run 1: publishes claim "X happened" citing url A.
    retriever_1 = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder_1 = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        analyst=(
            "cited_urls",
            {
                "title": "First edition",
                "body_markdown": "X happened, Northlake reports.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    service_1, _ = _make_service(
        retriever=retriever_1, resolve_model_fn=_resolver(FunctionModel(responder_1))
    )
    await service_1.run_research(beat_id, date(2026, 7, 20))

    # Run 2: the researcher restates claim "X happened" (citing ONLY the
    # already-cited url A — dropped by the gate) alongside a genuinely new
    # finding (a new claim, a new url B) — the gate must keep only the new one.
    retriever_2 = _FakeRetriever(
        documents=[_doc("https://example.com/a"), _doc("https://example.com/b")]
    )
    responder_2 = _PipelineResponder(
        researcher=(
            "findings",
            {
                "findings": [
                    _finding_payload("X happened", ["https://example.com/a"]),
                    _finding_payload("Y happened", ["https://example.com/b"]),
                ]
            },
        ),
        analyst=(
            "cited_urls",
            {
                "title": "Second edition",
                "body_markdown": "Y happened, per the Gazette.",
                "cited_urls": ["https://example.com/b"],
            },
        ),
    )
    service_2, _ = _make_service(
        retriever=retriever_2, resolve_model_fn=_resolver(FunctionModel(responder_2))
    )
    await service_2.run_research(beat_id, date(2026, 8, 3))

    briefs = await _briefs_for_beat(beat_id)
    published = [b for b in briefs if b.kind is BriefKind.PUBLISHED]
    assert len(published) == 2
    first = next(b for b in published if b.number == 1)
    second = next(b for b in published if b.number == 2)

    # The restated claim never reaches the second Brief's own claims array.
    assert second.claims == ["Y happened"]
    assert "X happened" not in second.claims

    # "Builds on Brief #N" resolution (AL-522's eventual query, exercised here
    # against the persisted data): the highest-numbered published Brief BELOW
    # this one.
    async with db.async_session() as session:
        builds_on_id = await session.scalar(
            select(Brief.id)
            .where(
                Brief.beat_id == beat_id,
                Brief.number < second.number,
                Brief.kind == BriefKind.PUBLISHED,
            )
            .order_by(Brief.number.desc())
            .limit(1)
        )
    assert builds_on_id == first.id
    assert first.number == 1


# --------------------------------------------------------------------------- #
# W31 — no novel findings -> a skipped row (number IS NULL, no body), and the
# next arrival does NOT immediately re-research.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W31")
async def test_no_novel_findings_produces_skipped_row_and_no_immediate_rerun() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user, anchor_weekday=0)

    # Non-empty documents, but the researcher legitimately finds nothing
    # worth flagging (TDD §5.3: an empty ``findings`` list is a legitimate
    # researcher result, not itself the Skipped signal).
    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=("findings", {"findings": []}),
        analyst=("detail", {"detail": ""}),
    )
    service, spawn = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    local_today = date(2026, 8, 3)
    await service.run_research(beat_id, local_today)

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE

    briefs = await _briefs_for_beat(beat_id)
    assert len(briefs) == 1
    skipped = briefs[0]
    assert skipped.kind is BriefKind.SKIPPED
    assert skipped.number is None
    assert skipped.body_markdown is None
    assert skipped.title is None
    assert skipped.published_on == local_today

    # The next arrival, the SAME day, must not immediately re-research: the
    # Skipped entry resets the cadence floor exactly as a published one does.
    async with db.async_session() as session:
        await service.drain_claimable(
            session,
            user_id=user.id,
            tz_offset_minutes=0,
            now=datetime(2026, 8, 3, 12, tzinfo=UTC),
        )
    await spawn.drain()

    beat_after = await _reload_beat(beat_id)
    assert beat_after.research_state is BeatResearchState.IDLE
    briefs_after = await _briefs_for_beat(beat_id)
    assert len(briefs_after) == 1  # no second entry


# --------------------------------------------------------------------------- #
# W32 — a Beat left claimable across several anchor days produces EXACTLY ONE
# Brief.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W32")
async def test_a_long_absence_produces_one_brief_not_a_backlog() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user, anchor_weekday=0)

    async with db.async_session() as session:
        await BriefRepository(session).create_published(
            beat_id=beat_id,
            number=1,
            published_at=datetime(2026, 6, 15, 9, tzinfo=UTC),
            published_on=date(2026, 6, 15),
            title="First edition",
            body_markdown="Body.",
            claims=["an old claim"],
            sources=[],
        )
        await session.commit()

    # Six weeks later: many Anchor days have passed since the last entry.
    retriever = _FakeRetriever(documents=[_doc("https://example.com/z")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("Z happened", ["https://example.com/z"])]},
        ),
        analyst=(
            "cited_urls",
            {
                "title": "Second edition",
                "body_markdown": "Z happened.",
                "cited_urls": ["https://example.com/z"],
            },
        ),
    )
    service, spawn = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )
    async with db.async_session() as session:
        await service.drain_claimable(
            session,
            user_id=user.id,
            tz_offset_minutes=0,
            now=datetime(2026, 7, 27, 12, tzinfo=UTC),
        )
    await spawn.drain()

    briefs = await _briefs_for_beat(beat_id)
    published = [b for b in briefs if b.kind is BriefKind.PUBLISHED]
    assert len(published) == 2  # the old one + exactly one new one, not six
    newest = max(published, key=lambda b: b.number)
    assert newest.number == 2


# --------------------------------------------------------------------------- #
# Zero documents after the §5.2 filters -> failed, NOT skipped. The
# load-bearing row.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_zero_documents_after_filters_is_failed_not_skipped() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    retriever = _FakeRetriever(documents=[])  # nothing to read at all
    responder = _PipelineResponder(
        researcher=("findings", {"findings": []}),
    )
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.FAILED
    assert beat.research_error is not None

    # The researcher must never even be called on this branch.
    assert responder.researcher_calls == 0

    briefs = await _briefs_for_beat(beat_id)
    assert briefs == []  # never a Skipped row either


# --------------------------------------------------------------------------- #
# W33 — RetrievalUnavailableError -> failed, retryable, and no briefs row
# exists at all. "Never an uncited essay."
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W33")
async def test_retrieval_unavailable_is_failed_with_no_brief_row() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    retriever = _FakeRetriever(unavailable=True)
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.FAILED
    assert beat.research_error is not None
    assert responder.researcher_calls == 0

    briefs = await _briefs_for_beat(beat_id)
    assert briefs == []

    # Retryable: the explicit retry claim re-claims it; an ordinary arrival
    # claim does not.
    async with db.async_session() as session:
        repo = BeatRepository(session)
        assert await repo.claim_research(beat_id) is None
        assert await repo.claim_research_for_retry(beat_id) is not None
        await session.rollback()


# --------------------------------------------------------------------------- #
# A Refusal -> research_state = refused, refusal_message set, no retry path
# re-claims it.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_researcher_refusal_is_terminal_and_never_reclaimed() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user, topic="how to synthesize a bioweapon")

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "message",
            {"message": "This subject is outside what the analyst can research."},
        ),
    )
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.REFUSED
    assert beat.refusal_message is not None
    assert responder.analyst_calls == 0  # the analyst is never reached

    briefs = await _briefs_for_beat(beat_id)
    assert briefs == []

    async with db.async_session() as session:
        repo = BeatRepository(session)
        assert await repo.claim_research(beat_id) is None
        assert await repo.claim_research_for_retry(beat_id) is None
        await session.rollback()


# --------------------------------------------------------------------------- #
# Two concurrent arrivals claim once.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_two_concurrent_runs_claim_the_beat_exactly_once() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        analyst=(
            "cited_urls",
            {
                "title": "T",
                "body_markdown": "Body.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    await asyncio.gather(
        service.run_research(beat_id, date(2026, 8, 3)),
        service.run_research(beat_id, date(2026, 8, 3)),
    )

    briefs = await _briefs_for_beat(beat_id)
    published = [b for b in briefs if b.kind is BriefKind.PUBLISHED]
    assert len(published) == 1  # only the winner published


# --------------------------------------------------------------------------- #
# A failed Beat is not re-claimed by an ordinary arrival.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_failed_beat_not_reclaimed_by_an_ordinary_arrival() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user, anchor_weekday=0)
    await _force_state(
        beat_id,
        research_state=BeatResearchState.FAILED,
        research_started_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    service, spawn = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    async with db.async_session() as session:
        await service.drain_claimable(
            session,
            user_id=user.id,
            tz_offset_minutes=0,
            now=datetime(2026, 8, 3, 12, tzinfo=UTC),
        )
    await spawn.drain()

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.FAILED  # untouched
    briefs = await _briefs_for_beat(beat_id)
    assert briefs == []


# --------------------------------------------------------------------------- #
# A dead-mid-run Beat is re-claimable after the stale window.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_stale_researching_beat_is_reclaimable_after_the_stale_window() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)
    await _force_state(
        beat_id,
        research_state=BeatResearchState.RESEARCHING,
        research_started_at=datetime.now(UTC) - timedelta(minutes=10),
    )

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        analyst=(
            "cited_urls",
            {
                "title": "T",
                "body_markdown": "Body.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    service, _ = _make_service(
        retriever=retriever,
        resolve_model_fn=_resolver(FunctionModel(responder)),
        stale_after_seconds=1.0,  # far shorter than the 10-minute age above
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE
    briefs = await _briefs_for_beat(beat_id)
    assert len(briefs) == 1
    assert briefs[0].kind is BriefKind.PUBLISHED


# --------------------------------------------------------------------------- #
# Hitting RATE_LIMIT_BRIEF_RESEARCH_PER_DAY inside the drain degrades to no
# research, never an exception.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_hitting_the_research_cap_degrades_to_no_research_not_an_exception() -> (
    None
):
    user = await _create_user()
    # Beat 1 already researched today (stamps research_started_at, spending
    # the cap); Beat 2 is fresh and cadence-claimable immediately.
    beat_1 = await _make_beat(user=user, topic="topic one")
    beat_2 = await _make_beat(user=user, topic="topic two")

    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        analyst=(
            "cited_urls",
            {
                "title": "T",
                "body_markdown": "Body.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    config = Settings(rate_limit_brief_research_per_day=1)
    service, spawn = _make_service(
        retriever=retriever,
        resolve_model_fn=_resolver(FunctionModel(responder)),
        config=config,
    )
    # Spend the cap on beat_1 through the real pipeline (stamps
    # research_started_at today, exactly what the counter reads).
    await service.run_research(beat_1, date(2026, 8, 3))
    assert (await _reload_beat(beat_1)).research_state is BeatResearchState.IDLE

    # Now drain for beat_2: cadence says claimable (no priors), but the cap
    # is already spent — must degrade silently, never raise.
    async with db.async_session() as session:
        await service.drain_claimable(
            session, user_id=user.id, tz_offset_minutes=0, now=now
        )
    await spawn.drain()  # must not raise

    beat_2_after = await _reload_beat(beat_2)
    assert beat_2_after.research_state is BeatResearchState.IDLE  # never claimed
    assert (await _briefs_for_beat(beat_2)) == []


# --------------------------------------------------------------------------- #
# Source publisher/title/date match the retrieved document even when the
# model emits contradictory values — the adversarial provenance test.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_source_metadata_matches_the_document_not_the_models_prose() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    real_date = date(2024, 3, 1)
    retriever = _FakeRetriever(
        documents=[
            _doc(
                "https://example.com/a",
                publisher="Real Publisher",
                title="Real Title",
                published_on=real_date,
            )
        ]
    )
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {
                "findings": [
                    _finding_payload(
                        "Something happened",
                        ["https://example.com/a"],
                        # Adversarial: the researcher's own `happened_on`
                        # names a plausible but WRONG date — never consulted
                        # by source materialization, which reads only the
                        # retrieved document's own metadata.
                        happened_on="2020-01-01",
                    )
                ]
            },
        ),
        analyst=(
            "cited_urls",
            {
                "title": "T",
                # Adversarial: the writer's own prose asserts a different,
                # wrong publication date. Never parsed.
                "body_markdown": "This was reported on 1 January 2020.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    briefs = await _briefs_for_beat(beat_id)
    assert len(briefs) == 1
    sources = await _sources_for_brief(briefs[0].id)
    assert len(sources) == 1
    assert sources[0].published_on == real_date  # the DOCUMENT's date, not "2020-01-01"
    assert sources[0].publisher == "Real Publisher"
    assert sources[0].title == "Real Title"


# --------------------------------------------------------------------------- #
# services/lifecycle.py's only change: GenerationLifecycle.start() binds
# briefing_service to the SAME TaskRegistry generation uses, but its OWN
# semaphore (D14) — exercised against the real module-level singleton, since
# that is exactly what the FastAPI lifespan binds in production.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_lifecycle_binds_briefing_service_to_the_shared_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aleph.services.briefing import briefing_service
    from aleph.services.generation import generation_orchestrator
    from aleph.services.lifecycle import GenerationLifecycle

    user = await _create_user()
    beat_id = await _make_beat(user=user)

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        analyst=(
            "cited_urls",
            {
                "title": "T",
                "body_markdown": "Body.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    # The module-level singleton's retriever/resolver are swapped for the
    # test's duration (monkeypatch auto-restores) — this IS the object
    # ``GenerationLifecycle.start()`` binds in production, so exercising the
    # wiring means using it, not a fresh instance the lifecycle never touches.
    monkeypatch.setattr(briefing_service, "_retriever", retriever)
    monkeypatch.setattr(
        briefing_service, "_resolve_model", _resolver(FunctionModel(responder))
    )

    lifecycle = GenerationLifecycle(generation_orchestrator)
    await lifecycle.start()
    try:
        assert len(lifecycle.registry) == 0
        # Drives the real public entry point (D15's arrival trigger), which
        # spawns through the service's OWN bound seam — never the test's —
        # exactly a real ``GET /beats`` would.
        async with db.async_session() as session:
            await briefing_service.drain_claimable(
                session,
                user_id=user.id,
                tz_offset_minutes=0,
                now=datetime(2026, 8, 3, 12, tzinfo=UTC),
            )
        # The task landed in the SAME registry generation uses (TDD §2: "the
        # registry ... reused as-is") — a second, private registry would
        # leave this at 0.
        assert len(lifecycle.registry) == 1
        await lifecycle.registry.join()
    finally:
        await lifecycle.stop()

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE
    briefs = await _briefs_for_beat(beat_id)
    assert len(briefs) == 1
