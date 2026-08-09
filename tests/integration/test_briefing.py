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
from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select, update

from aleph import db
from aleph.agents.researcher import ResearcherDeps, RetrievedDocument
from aleph.config import Settings
from aleph.models import Beat, BeatResearchState, Brief, BriefKind, BriefSource, Level
from aleph.repositories import BeatRepository, BriefRepository
from aleph.services import briefing as briefing_module
from aleph.services.briefing import (
    _INVARIANT_VIOLATION_MESSAGE,
    _RESEARCH_FAILED_MESSAGE,
    BriefingService,
)
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
# W31 — findings EXIST but the novelty gate drops every one of them -> a
# skipped row (number IS NULL, no body), and the next arrival does NOT
# immediately re-research.
#
# FIX 7 (code review, AL-521): the ORIGINAL W31 test exercised the researcher
# returning ZERO findings, which is a DIFFERENT (also legitimate) §5.7 row.
# §5.7's actual Skipped row is "findings exist and the GATE drops all of
# them" — the only path where `documents` is non-empty but
# `analyst_documents` is empty, and the one D9 and the padding test govern.
# The zero-findings path keeps its own, separate test below.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W31")
async def test_gate_dropping_all_findings_produces_a_skipped_row() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user, anchor_weekday=0)

    # Run 1: publishes a real Brief with claim "X happened" citing url A.
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
                "body_markdown": "X happened.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    service_1, _ = _make_service(
        retriever=retriever_1, resolve_model_fn=_resolver(FunctionModel(responder_1))
    )
    await service_1.run_research(beat_id, date(2026, 7, 20))

    # Run 2: the researcher reports a REAL finding that restates run 1's
    # claim, citing ONLY the already-cited URL — the gate drops it on BOTH
    # mechanisms (`domains/novelty.py::filter_new`), leaving `documents`
    # non-empty but `analyst_documents` empty. Not zero findings from the
    # researcher — a genuine gate rejection.
    retriever_2 = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder_2 = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        analyst=("detail", {"detail": ""}),
    )
    service_2, spawn = _make_service(
        retriever=retriever_2, resolve_model_fn=_resolver(FunctionModel(responder_2))
    )

    local_today = date(2026, 8, 3)
    await service_2.run_research(beat_id, local_today)

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE
    # The researcher WAS called and DID report a finding — the gate, not the
    # model, is what produced the Skipped outcome.
    assert responder_2.researcher_calls == 1

    briefs = await _briefs_for_beat(beat_id)
    skipped = [b for b in briefs if b.kind is BriefKind.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].number is None
    assert skipped[0].body_markdown is None
    assert skipped[0].published_on == local_today

    # The next arrival, the SAME day, must not immediately re-research: the
    # Skipped entry resets the cadence floor exactly as a published one does.
    async with db.async_session() as session:
        await service_2.drain_claimable(
            session,
            user_id=user.id,
            tz_offset_minutes=0,
            now=datetime(2026, 8, 3, 12, tzinfo=UTC),
        )
    await spawn.drain()

    briefs_after = await _briefs_for_beat(beat_id)
    assert len(briefs_after) == 2  # the published one + the skipped one, no third


# --------------------------------------------------------------------------- #
# The ZERO-findings path — kept as its own, separate test (FIX 7): the
# researcher legitimately finds nothing worth flagging in this batch of
# documents, distinct from the gate-rejection path above.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_zero_findings_from_researcher_produces_skipped_row() -> None:
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
# W32 — a Beat left claimable across several simulated anchor days produces
# EXACTLY ONE new Brief, and the cadence floor moves.
#
# FIX 7 (code review, AL-521): the ORIGINAL W32 test called `drain_claimable`
# ONCE, which one drain structurally guarantees can produce at most one new
# Brief — it could not distinguish "the cadence caps it at one" from "we
# only asked once." This drains REPEATEDLY across several simulated days
# (some before the floor opens, one right after it does, one again the SAME
# day, one more after that) and asserts exactly one new Brief resulted
# across the WHOLE sequence, and that the floor moved to the new Brief's own
# date.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W32")
async def test_repeated_draining_across_several_anchor_days_produces_one_brief() -> (
    None
):
    user = await _create_user()
    beat_id = await _make_beat(user=user, anchor_weekday=0)  # Monday

    async with db.async_session() as session:
        await BriefRepository(session).create_published(
            beat_id=beat_id,
            number=1,
            published_at=datetime(2026, 6, 15, 9, tzinfo=UTC),
            published_on=date(2026, 6, 15),  # a Monday
            title="First edition",
            body_markdown="Body.",
            claims=["an old claim"],
            sources=[],
        )
        await session.commit()

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

    async def _drain_on(day: date) -> None:
        async with db.async_session() as session:
            await service.drain_claimable(
                session,
                user_id=user.id,
                tz_offset_minutes=0,
                now=datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
            )
        await spawn.drain()

    # Day 1: BEFORE the floor opens (`next_claimable_on(6/15, Monday) ==
    # 6/22`) — must be a no-op.
    await _drain_on(date(2026, 6, 18))
    assert len(await _briefs_for_beat(beat_id)) == 1

    # Day 2: six weeks after the last entry, well past the floor — the
    # single run that actually publishes. Note it publishes dated THIS day,
    # not the earlier floor date — "since the last Brief", not a catch-up.
    await _drain_on(date(2026, 7, 27))
    briefs = await _briefs_for_beat(beat_id)
    published = [b for b in briefs if b.kind is BriefKind.PUBLISHED]
    assert len(published) == 2
    newest = max(published, key=lambda b: b.number)
    assert newest.number == 2
    assert newest.published_on == date(2026, 7, 27)

    # Day 3: the SAME day again — the floor just moved to 8/3 (7/27 is
    # itself a Monday, so the next Monday strictly after it is a full week
    # out); a same-day redrain must not double-publish.
    await _drain_on(date(2026, 7, 27))
    # Day 4: still before the new floor (8/3).
    await _drain_on(date(2026, 8, 1))

    briefs_final = await _briefs_for_beat(beat_id)
    published_final = [b for b in briefs_final if b.kind is BriefKind.PUBLISHED]
    assert len(published_final) == 2  # still exactly one NEW Brief, not more

    # FIX G (second-pass code review on AL-521): every assertion above this
    # point is NEGATIVE (the floor did NOT open early) — a regression that
    # left the Beat permanently unclaimable (FIX A's own failure mode) would
    # pass every one of them. Day 5: on/after the NEW floor (8/3) — the Beat
    # must become claimable AGAIN, proving the floor MOVED rather than
    # closing for good. A fresh finding/URL, since #2's own claim/URL are
    # now already-cited and would be gated to a Skip, not a publish.
    retriever.documents = [_doc("https://example.com/w")]
    responder.researcher = (
        "findings",
        {"findings": [_finding_payload("W happened", ["https://example.com/w"])]},
    )
    responder.analyst = (
        "cited_urls",
        {
            "title": "Third edition",
            "body_markdown": "W happened.",
            "cited_urls": ["https://example.com/w"],
        },
    )
    await _drain_on(date(2026, 8, 3))

    briefs_third = await _briefs_for_beat(beat_id)
    published_third = [b for b in briefs_third if b.kind is BriefKind.PUBLISHED]
    assert len(published_third) == 3
    newest_third = max(published_third, key=lambda b: b.number)
    assert newest_third.number == 3
    assert newest_third.published_on == date(2026, 8, 3)


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


@pytest.mark.anyio
async def test_zero_documents_after_retrieval_filters_remove_them_all_is_failed() -> (
    None
):
    """FIX 7 (code review, AL-521): the RAW-empty-list test above never
    exercises §5.2's filters (undated / empty-text) at all — the retriever
    itself returning `[]` proves nothing about `retrieve()`'s own filtering.
    This gives the retriever real documents that the filters remove
    ENTIRELY: one undated, one with no text — so "zero documents AFTER the
    filters" is proven at THIS layer, not merely asserted."""
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    retriever = _FakeRetriever(
        documents=[
            _doc("https://example.com/undated", published_on=None),
            _doc("https://example.com/empty", text=""),
        ]
    )
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


# --------------------------------------------------------------------------- #
# "Writer exhausts validator retries -> failed, no partial Brief." FIX 7
# (code review, AL-521): §5.7 names this row explicitly and no test drove it.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_exhausted_writer_retries_is_failed_with_no_partial_brief() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        # ALWAYS violates `validate_brief_result`'s provenance check — cites
        # a URL outside the documents behind this run's survivors, on EVERY
        # call — so the analyst's retry budget (`_ANALYST_RETRIES`)
        # exhausts and pydantic-ai raises `UnexpectedModelBehavior`.
        analyst=(
            "cited_urls",
            {
                "title": "T",
                "body_markdown": "Body.",
                "cited_urls": ["https://never-retrieved.example"],
            },
        ),
    )
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.FAILED
    assert beat.research_error is not None

    briefs = await _briefs_for_beat(beat_id)
    assert briefs == []  # no partial Brief is ever persisted


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
# FIX 1 (code review, AL-521): the claim must happen INSIDE drain_claimable,
# before the spawn — so `research_state` reads `researching` immediately
# after `drain_claimable` returns, WITHOUT ever awaiting the spawned task.
# This is the exact window the defect lived in: a Beat's pre-claim state
# (`idle`) is ALSO its post-success state, so `lib/polling.ts` sees a
# terminal state on the client's first fetch unless the claim already
# committed synchronously inside the request.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_drain_claimable_commits_the_claim_before_the_spawned_task_runs() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user, anchor_weekday=0)

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    # hold=True: the spawned task is parked at a gate and never given the
    # event loop until the test explicitly opens it — the exact window the
    # old (broken) ordering left empty.
    spawn = CollectingSpawn(hold=True)
    service, _ = _make_service(
        retriever=retriever,
        resolve_model_fn=_resolver(FunctionModel(responder)),
        spawn=spawn,
    )

    async with db.async_session() as session:
        await service.drain_claimable(
            session,
            user_id=user.id,
            tz_offset_minutes=0,
            now=datetime(2026, 8, 3, 12, tzinfo=UTC),
        )

    # A FRESH session (a second connection, simulating the request's own
    # subsequent read) must already see `researching` — the spawned task has
    # not been given the event loop at all yet (spawn.tasks holds it parked).
    assert len(spawn.tasks) == 1
    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.RESEARCHING
    assert beat.research_started_at is not None

    # Clean up the parked task so nothing dangles past the test.
    await spawn.cancel_pending()


@pytest.mark.anyio
async def test_run_research_called_with_a_fence_never_re_claims() -> None:
    """The other half of FIX 1: `run_research(..., fence=...)` must run the
    pipeline against the ALREADY-claimed Beat, never re-claim it — a second
    claim attempt on an already-`researching` row would simply fail and the
    run would silently no-op."""
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    async with db.async_session() as session:
        fence = await BeatRepository(session).claim_research(beat_id)
        await session.commit()
    assert fence is not None

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

    await service.run_research(beat_id, date(2026, 8, 3), fence=fence)

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE
    briefs = await _briefs_for_beat(beat_id)
    assert len(briefs) == 1
    assert briefs[0].kind is BriefKind.PUBLISHED


# --------------------------------------------------------------------------- #
# FIX 3 (code review, AL-521): the drain must not spawn (or even attempt to
# claim) for a Beat that cannot be claimed — a run in flight, or a
# permanently `failed`/`refused` Beat.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_draining_a_researching_beat_repeatedly_spawns_at_most_one_task() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user, anchor_weekday=0)

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    spawn = CollectingSpawn(hold=True)
    service, _ = _make_service(
        retriever=retriever,
        resolve_model_fn=_resolver(FunctionModel(responder)),
        spawn=spawn,
    )

    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    async with db.async_session() as session:
        await service.drain_claimable(
            session, user_id=user.id, tz_offset_minutes=0, now=now
        )
    assert len(spawn.tasks) == 1
    assert (await _reload_beat(beat_id)).research_state is BeatResearchState.RESEARCHING

    # The Beat is now `researching` — a run genuinely in flight. Draining
    # again (the poll-every-2-5s shape the review names) must not spawn a
    # SECOND task while the first is still parked.
    async with db.async_session() as session:
        await service.drain_claimable(
            session, user_id=user.id, tz_offset_minutes=0, now=now
        )
    async with db.async_session() as session:
        await service.drain_claimable(
            session, user_id=user.id, tz_offset_minutes=0, now=now
        )
    assert len(spawn.tasks) == 1  # still exactly one, not three

    await spawn.cancel_pending()


@pytest.mark.anyio
async def test_draining_a_refused_or_failed_beat_spawns_nothing() -> None:
    user = await _create_user()
    refused_id = await _make_beat(user=user, anchor_weekday=0, topic="refused topic")
    failed_id = await _make_beat(user=user, anchor_weekday=0, topic="failed topic")
    await _force_state(
        refused_id,
        research_state=BeatResearchState.REFUSED,
        research_started_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    await _force_state(
        failed_id,
        research_state=BeatResearchState.FAILED,
        research_started_at=datetime(2026, 7, 1, tzinfo=UTC),
    )

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    spawn = CollectingSpawn(hold=True)
    service, _ = _make_service(
        retriever=retriever,
        resolve_model_fn=_resolver(FunctionModel(responder)),
        spawn=spawn,
    )

    async with db.async_session() as session:
        await service.drain_claimable(
            session,
            user_id=user.id,
            tz_offset_minutes=0,
            now=datetime(2026, 8, 3, 12, tzinfo=UTC),
        )

    assert spawn.tasks == []
    assert (await _reload_beat(refused_id)).research_state is BeatResearchState.REFUSED
    assert (await _reload_beat(failed_id)).research_state is BeatResearchState.FAILED

    await spawn.cancel_pending()


# --------------------------------------------------------------------------- #
# Second-pass code-review FIX A on AL-521: no DRAIN-level test covered the
# stale-`researching` arm at all — every existing stale test (above, and
# `test_stale_researching_beat_is_reclaimable_after_the_stale_window`) drives
# `run_research` directly, the SELF-claim path, never `drain_claimable`. In
# production `drain_claimable` is the ONLY recovery path for a crashed run
# (D5 — no Beats scan): if this regressed to a plain state filter, this is
# the test that would catch a permanently-wedged Beat that every other test
# in this file would still call green.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_draining_a_stale_researching_beat_reclaims_it() -> None:
    user = await _create_user()
    beat_id = await _make_beat(user=user, anchor_weekday=0)
    stale_fence = datetime(2026, 7, 1, tzinfo=UTC)
    await _force_state(
        beat_id,
        research_state=BeatResearchState.RESEARCHING,
        research_started_at=stale_fence,
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
    spawn = CollectingSpawn(hold=True)
    service, _ = _make_service(
        retriever=retriever,
        resolve_model_fn=_resolver(FunctionModel(responder)),
        spawn=spawn,
        stale_after_seconds=1.0,  # far shorter than the fence's age above
    )

    async with db.async_session() as session:
        await service.drain_claimable(
            session,
            user_id=user.id,
            tz_offset_minutes=0,
            now=datetime(2026, 8, 3, 12, tzinfo=UTC),
        )

    # Drained AND re-claimed: one task spawned, and the fence moved off the
    # stale timestamp -- proof this is a FRESH claim, not merely a read that
    # happened to still see `researching`.
    assert len(spawn.tasks) == 1
    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.RESEARCHING
    assert beat.research_started_at != stale_fence

    await spawn.drain()

    beat_after = await _reload_beat(beat_id)
    assert beat_after.research_state is BeatResearchState.IDLE
    briefs = await _briefs_for_beat(beat_id)
    assert len(briefs) == 1
    assert briefs[0].kind is BriefKind.PUBLISHED


# --------------------------------------------------------------------------- #
# FIX 4 (code review, AL-521): the cap is checked PER CLAIM, not once per
# drain — with `used = cap - 1` and several claimable Beats, only ONE of
# them may claim, never all of them.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_cap_is_checked_before_each_claim_not_once_for_the_whole_drain() -> (
    None
):
    user = await _create_user()
    beat_1 = await _make_beat(user=user, topic="topic one")
    beat_2 = await _make_beat(user=user, topic="topic two")
    beat_3 = await _make_beat(user=user, topic="topic three")

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    # Cap of 1: with all THREE Beats claimable in the same drain, the old
    # (once-per-drain) check would pass a single capacity check and then
    # spawn for every claimable id — three billed runs. The fixed check
    # must claim exactly ONE.
    config = Settings(rate_limit_brief_research_per_day=1)
    spawn = CollectingSpawn(hold=True)
    service, _ = _make_service(
        retriever=retriever,
        resolve_model_fn=_resolver(FunctionModel(responder)),
        config=config,
        spawn=spawn,
    )

    async with db.async_session() as session:
        await service.drain_claimable(
            session,
            user_id=user.id,
            tz_offset_minutes=0,
            now=datetime(2026, 8, 3, 12, tzinfo=UTC),
        )

    assert len(spawn.tasks) == 1  # not three

    researching = [
        beat_id
        for beat_id in (beat_1, beat_2, beat_3)
        if (await _reload_beat(beat_id)).research_state is BeatResearchState.RESEARCHING
    ]
    assert len(researching) == 1

    await spawn.cancel_pending()


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
# FIX 5 (code review, AL-521): "a Brief with no Sources is not publishable"
# enforced at the PERSIST boundary, and cited_urls deduplicated.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_zero_sources_after_materialization_is_failed_not_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structurally unreachable through the ordinary pipeline today —
    `AnalystDeps`'s construction-time invariant plus both agents' output
    validators already guarantee a non-empty, resolvable `cited_urls` — so
    forced here by monkeypatching `_materialize_sources` to its degenerate
    output, exactly the scenario the review's write-up names."""
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    monkeypatch.setattr(briefing_module, "_materialize_sources", lambda *a, **k: [])

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

    await service.run_research(beat_id, date(2026, 8, 3))

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.FAILED
    assert beat.research_error is not None

    briefs = await _briefs_for_beat(beat_id)
    assert briefs == []  # never a zero-Source Brief


@pytest.mark.anyio
async def test_a_url_cited_twice_produces_one_source_not_two() -> None:
    """FIX 5: `cited_urls` carries no dedupe anywhere upstream — a model
    listing the same URL twice must not become two `brief_sources` rows at
    two different positions."""
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
                "body_markdown": "Body. Body again.",
                "cited_urls": ["https://example.com/a", "https://example.com/a"],
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
    assert sources[0].url == "https://example.com/a"


# --------------------------------------------------------------------------- #
# `open_threads` comes from the latest PUBLISHED Brief, never the latest
# ENTRY (second-pass code-review FIX B on AL-521, rewriting FIX 6's test to
# pin the corrected behavior). §5.4's own motivating example — "Nothing
# material since Brief #4 -- the Commission's consultation is still open" --
# is unreachable past the first quiet week under the old "latest entry"
# reading, because a Skipped row's `claims` is always `[]`. A quiet Beat is
# the NORMAL case, so this is the common path, not an edge case.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_open_thread_claims_come_from_the_latest_published_brief() -> None:
    """`_ResearchContext`'s two claim-shaped fields must diverge:
    `prior_claims` (the gate's own unbounded input, D9) keeps the Beat's
    WHOLE history; `open_thread_claims` (what reaches `AnalystDeps.
    open_threads`) is bounded to the latest PUBLISHED Brief's claims only --
    proven here by a Skipped row that is the more RECENT entry, whose own
    claims (`[]`) must NOT win."""
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    async with db.async_session() as session:
        briefs_repo = BriefRepository(session)
        await briefs_repo.create_published(
            beat_id=beat_id,
            number=1,
            published_at=datetime(2026, 7, 6, 9, tzinfo=UTC),
            published_on=date(2026, 7, 6),
            title="First",
            body_markdown="Body.",
            claims=["an ancient claim from the first edition"],
            sources=[],
        )
        await briefs_repo.create_published(
            beat_id=beat_id,
            number=2,
            published_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
            published_on=date(2026, 7, 20),
            title="Second",
            body_markdown="Body.",
            claims=["the newest claim"],
            sources=[],
        )
        # The latest ENTRY, more recent than Brief #2 -- a Skipped row, whose
        # claims are always `[]`. The old ("latest entry") reading would make
        # `open_thread_claims` `()` here; FIX B must not.
        await briefs_repo.create_skipped(
            beat_id=beat_id,
            published_at=datetime(2026, 7, 27, 9, tzinfo=UTC),
            published_on=date(2026, 7, 27),
            skip_line="Nothing material since Brief #2",
        )
        await session.commit()

    retriever = _FakeRetriever()
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    context = await service._load_context(beat_id)  # white-box: FIX B's own field

    assert context is not None
    assert set(context.prior_claims) == {
        "an ancient claim from the first edition",
        "the newest claim",
    }
    # The latest PUBLISHED Brief's claims (#2), NOT the empty latest entry's.
    assert context.open_thread_claims == ("the newest claim",)
    assert context.latest_published_number == 2


@pytest.mark.anyio
async def test_two_consecutive_skips_still_carry_the_latest_published_claims() -> None:
    """§5.4's own worked example, played out for a second quiet week. Week 1
    publishes Brief #1 with a claim that stays "open" for weeks to come; week
    2 is quiet (a legitimate zero-findings Skip); week 3 is STILL quiet --
    under the OLD "latest entry" reading, `open_thread_claims` would already
    be `()` by week 3 (week 2's own Skip row), permanently losing "the
    consultation is still open" the moment the FIRST quiet week passed. This
    is the common path (consecutive quiet weeks), not an edge case."""
    user = await _create_user()
    beat_id = await _make_beat(user=user, anchor_weekday=0)

    # Week 1: publishes.
    retriever_1 = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder_1 = _PipelineResponder(
        researcher=(
            "findings",
            {
                "findings": [
                    _finding_payload(
                        "the consultation opened", ["https://example.com/a"]
                    )
                ]
            },
        ),
        analyst=(
            "cited_urls",
            {
                "title": "First edition",
                "body_markdown": "The consultation opened.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    service_1, _ = _make_service(
        retriever=retriever_1, resolve_model_fn=_resolver(FunctionModel(responder_1))
    )
    await service_1.run_research(beat_id, date(2026, 7, 6))

    # Week 2: nothing novel -- a legitimate zero-findings quiet week.
    retriever_2 = _FakeRetriever(documents=[_doc("https://example.com/b")])
    responder_2 = _PipelineResponder(
        researcher=("findings", {"findings": []}), analyst=("detail", {"detail": ""})
    )
    service_2, _ = _make_service(
        retriever=retriever_2, resolve_model_fn=_resolver(FunctionModel(responder_2))
    )
    await service_2.run_research(beat_id, date(2026, 7, 13))

    briefs_after_week_2 = await _briefs_for_beat(beat_id)
    assert len(briefs_after_week_2) == 2
    assert briefs_after_week_2[0].kind is BriefKind.SKIPPED  # newest first

    # Week 3: STILL nothing novel. The latest ENTRY is now week 2's Skip
    # (`claims == []`, `create_skipped`'s own shape) -- the exact case FIX B
    # exists for. `open_thread_claims` must still carry week 1's claim here.
    retriever_3 = _FakeRetriever(documents=[_doc("https://example.com/c")])
    responder_3 = _PipelineResponder(
        researcher=("findings", {"findings": []}), analyst=("detail", {"detail": ""})
    )
    service_3, _ = _make_service(
        retriever=retriever_3, resolve_model_fn=_resolver(FunctionModel(responder_3))
    )

    context = await service_3._load_context(beat_id)  # white-box: FIX B's own field
    assert context is not None
    assert context.open_thread_claims == ("the consultation opened",)
    assert context.latest_published_number == 1

    await service_3.run_research(beat_id, date(2026, 7, 20))

    briefs_final = await _briefs_for_beat(beat_id)
    assert len(briefs_final) == 3
    newest = briefs_final[0]
    assert newest.kind is BriefKind.SKIPPED
    # The skip line's number-naming clause was already keyed on
    # `latest_published` before FIX B -- proven here alongside
    # `open_thread_claims` so both halves of one skip line are shown to share
    # ONE data source, which is the "internal inconsistency" tell FIX B's own
    # bug report names.
    assert newest.skip_line is not None
    assert newest.skip_line.startswith("Nothing material since Brief #1")


# --------------------------------------------------------------------------- #
# FIX 8 (code review, AL-521): a behavioral guard on the retrieval invariant,
# replacing the old source-grep guard (`tests/unit/test_briefing_service.py`).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_documents_reaching_researcher_deps_are_capped_and_budget_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly one `Retriever.search()` call per run, AND the documents
    reaching `ResearcherDeps` are bounded by BOTH
    `BRIEF_RETRIEVAL_MAX_DOCUMENTS` and `BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS`
    — i.e. that they demonstrably came through
    `services/retrieval.py::retrieve()`, the only path that enforces either
    bound, and nowhere else."""
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    long_text = "x" * 1_000
    retriever = _FakeRetriever(
        documents=[_doc(f"https://example.com/{i}", text=long_text) for i in range(5)]
    )
    # findings=[] -> survivors=[] -> the analyst IS still called (on the
    # Skipped branch, TDD §5.4) — a response must be configured for it too,
    # or the responder's own assertion trips.
    responder = _PipelineResponder(
        researcher=("findings", {"findings": []}),
        analyst=("detail", {"detail": ""}),
    )
    config = Settings(
        brief_retrieval_max_documents=2,
        brief_retrieval_text_budget_chars=120,
    )
    service, _ = _make_service(
        retriever=retriever,
        resolve_model_fn=_resolver(FunctionModel(responder)),
        config=config,
    )

    captured: list[ResearcherDeps] = []
    real_build_prompt = briefing_module.build_researcher_prompt

    def _spying_build_prompt(deps: ResearcherDeps) -> str:
        captured.append(deps)
        return real_build_prompt(deps)

    monkeypatch.setattr(
        briefing_module, "build_researcher_prompt", _spying_build_prompt
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    assert len(retriever.calls) == 1  # exactly one search per run
    assert len(captured) == 1
    documents = captured[0].documents
    assert len(documents) <= config.brief_retrieval_max_documents
    assert (
        sum(len(d.text) for d in documents) <= config.brief_retrieval_text_budget_chars
    )
    # Genuinely truncated, not merely "small enough by luck" — five
    # 1000-char documents could never fit an untruncated budget of 120.
    assert any(len(d.text) < 1_000 for d in documents)


# --------------------------------------------------------------------------- #
# FIX 9 (code review, AL-521): a programming-error invariant violation is
# distinguishable from an ordinary pipeline failure, not disguised as one.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_analyst_deps_invariant_violation_is_a_distinguishable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`AnalystDeps.__post_init__`'s `ValueError` — an invariant violation,
    not a provider blip — must still resolve to `failed` (the blanket
    handler's whole point: a bug must not become an infinitely-retried
    billed run) but be stored distinguishably from an ordinary pipeline
    failure. Structurally unreachable through the ordinary pipeline (the
    researcher's own validator plus `_documents_for_survivors` keep the two
    sets in agreement by construction) — forced here by monkeypatching
    `_documents_for_survivors` to violate that agreement, exactly the
    review's named scenario."""
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    monkeypatch.setattr(briefing_module, "_documents_for_survivors", lambda *a, **k: [])

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
    )
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.FAILED
    assert beat.research_error == _INVARIANT_VIOLATION_MESSAGE
    assert beat.research_error != _RESEARCH_FAILED_MESSAGE  # distinguishable
    assert responder.analyst_calls == 0  # never reached a model call

    briefs = await _briefs_for_beat(beat_id)
    assert briefs == []


# --------------------------------------------------------------------------- #
# Second-pass code-review FIX E on AL-521: FIX 9's `except (ValueError,
# KeyError)` was too broad -- `pydantic_core.ValidationError` (a real
# `Retriever` parses a provider payload) and `json.JSONDecodeError` both
# subclass `ValueError` and are reachable INSIDE the guarded block, since
# `retrieve()` runs there too. A malformed/truncated search-API response —
# the textbook provider blip that MIGHT succeed on retry — must fall through
# to the ordinary blanket handler, never be misclassified as an invariant
# violation.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_validation_error_from_retrieve_is_not_misclassified_as_an_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `pydantic_core.ValidationError` raised from inside `retrieve()` (here
    forced directly, standing in for a real `Retriever` failing to parse a
    malformed provider payload) must resolve to the ORDINARY `failed` message
    (`_RESEARCH_FAILED_MESSAGE`), not `_INVARIANT_VIOLATION_MESSAGE` — proving
    FIX E's narrower `_InvariantViolationError` catch no longer swallows this."""
    user = await _create_user()
    beat_id = await _make_beat(user=user)

    class _Probe(BaseModel):
        value: int

    async def _raising_retrieve(*_args: object, **_kwargs: object) -> list[object]:
        _Probe.model_validate({"value": "not-a-number"})  # raises ValidationError
        return []  # pragma: no cover - unreachable, _Probe always raises above

    monkeypatch.setattr(briefing_module, "retrieve", _raising_retrieve)

    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    service, _ = _make_service(
        retriever=retriever, resolve_model_fn=_resolver(FunctionModel(responder))
    )

    await service.run_research(beat_id, date(2026, 8, 3))

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.FAILED
    # The ORDINARY blanket failure text -- NOT the invariant-violation one.
    assert beat.research_error == _RESEARCH_FAILED_MESSAGE
    assert beat.research_error != _INVARIANT_VIOLATION_MESSAGE
    assert responder.researcher_calls == 0  # never reached: retrieve() raised first

    briefs = await _briefs_for_beat(beat_id)
    assert briefs == []


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
