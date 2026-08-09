"""Analyst schema integration tests against a real per-test Postgres database
(AL-511, issue #168, part of epic #163).

Covers the load-bearing schema properties migration ``0012`` / TDD §4
promises, and the repository behaviour ``repositories/beats.py`` /
``repositories/briefs.py`` own:

* **D2 is structural, not conventional** — the two ``CHECK`` constraints
  reject a bodied Skipped row, a Published row missing its number/title/body,
  and two Published rows sharing a number in one Beat; the **partial** unique
  index is what lets two Skipped rows (both ``number IS NULL``) share a Beat.
* **The claim protocol (D3), reused from ``_generation.py`` unchanged** —
  atomic under two concurrent callers, ``failed`` excluded from the auto
  predicate and reachable only via the retry claim, a stale ``researching``
  row self-heals after the stale window, ``refused`` is terminal.
* **The read ping is first-write-wins (D11)** — a second ping never moves
  ``read_at``/``sources_seen_at``.
* **Cascades** — deleting a user tears down Beats -> Briefs -> Sources.
* **The rail read and the two continuity reads** (§4/§5.4/§5.6).

Written in the ``test_schema.py``/``test_flashcards_schema.py`` style: real
Postgres (fakes over mocks is for pure logic; constraints, partial indexes and
row-lock claims are decided by the database).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import Table, delete, func, select, text
from sqlalchemy.exc import IntegrityError

from aleph import db
from aleph.models import (
    Beat,
    BeatResearchState,
    Brief,
    BriefKind,
    BriefSource,
    Level,
    User,
)
from aleph.repositories.beats import BeatRepository
from aleph.repositories.briefs import BriefRepository, NewSource

from .conftest import create_user

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

# ``brief_research_stale_after_seconds`` defaults to 420s (AL-501); tests that
# need a stale window use an explicit smaller value on the repository
# (matching ``FlashcardRepository.claim_draft_run``'s per-call
# ``stale_after_seconds``, only here it is a constructor argument, the
# ``PathRepository`` shape) so the fixture ages don't have to be minutes long.
TEST_STALE_AFTER_SECONDS = 180.0
STALE_AGE = timedelta(minutes=4)  # > TEST_STALE_AFTER_SECONDS
FRESH_AGE = timedelta(minutes=1)  # < TEST_STALE_AFTER_SECONDS


async def _make_beat(
    session: AsyncSession,
    *,
    user: User,
    topic: str = "EU AI regulation",
    anchor_weekday: int = 0,
    research_state: BeatResearchState = BeatResearchState.IDLE,
    research_started_at: datetime | None = None,
    research_error: str | None = None,
) -> Beat:
    beat = Beat(
        user_id=user.id,
        topic=topic,
        level=Level.SOME_EXPERIENCE,
        anchor_weekday=anchor_weekday,
        research_state=research_state,
        research_started_at=research_started_at,
        research_error=research_error,
    )
    session.add(beat)
    await session.flush()
    return beat


def _new_source(
    url: str = "https://example.com/a",
    *,
    publisher: str = "Northlake Health System",
    title: str = "A report",
    published_on: date = date(2026, 8, 1),
) -> NewSource:
    return NewSource(
        url=url, publisher=publisher, title=title, published_on=published_on
    )


# --------------------------------------------------------------------------- #
# D2's two CHECK constraints — structural, not conventional (TDD §4)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_bodied_skipped_brief_is_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        session.add(
            Brief(
                beat_id=beat.id,
                kind=BriefKind.SKIPPED,
                number=None,
                published_at=datetime.now(UTC),
                published_on=date(2026, 8, 3),
                title=None,
                body_markdown="A Skipped row must never carry a body.",
                skip_line="Nothing material since Brief #4.",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_a_skipped_brief_with_a_number_is_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        session.add(
            Brief(
                beat_id=beat.id,
                kind=BriefKind.SKIPPED,
                number=5,
                published_at=datetime.now(UTC),
                published_on=date(2026, 8, 3),
                title=None,
                body_markdown=None,
                skip_line="Nothing material.",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_a_published_brief_with_no_number_is_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        session.add(
            Brief(
                beat_id=beat.id,
                kind=BriefKind.PUBLISHED,
                number=None,
                published_at=datetime.now(UTC),
                published_on=date(2026, 8, 3),
                title="The backlash arrived",
                body_markdown="Body.",
                skip_line=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_a_published_brief_missing_title_or_body_is_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        session.add(
            Brief(
                beat_id=beat.id,
                kind=BriefKind.PUBLISHED,
                number=1,
                published_at=datetime.now(UTC),
                published_on=date(2026, 8, 3),
                title=None,  # missing
                body_markdown="Body.",
                skip_line=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_a_published_brief_with_a_skip_line_is_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        session.add(
            Brief(
                beat_id=beat.id,
                kind=BriefKind.PUBLISHED,
                number=1,
                published_at=datetime.now(UTC),
                published_on=date(2026, 8, 3),
                title="T",
                body_markdown="Body.",
                skip_line="A published Brief must not carry a skip line.",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


# --------------------------------------------------------------------------- #
# The partial unique index — the property a test must pin explicitly
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_two_published_briefs_sharing_a_number_in_one_beat_rejected() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        session.add_all(
            [
                Brief(
                    beat_id=beat.id,
                    kind=BriefKind.PUBLISHED,
                    number=1,
                    published_at=datetime.now(UTC),
                    published_on=date(2026, 7, 20),
                    title="First",
                    body_markdown="Body one.",
                    skip_line=None,
                ),
                Brief(
                    beat_id=beat.id,
                    kind=BriefKind.PUBLISHED,
                    number=1,
                    published_at=datetime.now(UTC),
                    published_on=date(2026, 7, 27),
                    title="Second",
                    body_markdown="Body two.",
                    skip_line=None,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.anyio
async def test_two_skipped_briefs_in_one_beat_succeed() -> None:
    """The property the partial index buys (TDD §4): NULL ``number`` is never
    indexed, so any number of Skipped rows may share a Beat.
    """
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        session.add_all(
            [
                Brief(
                    beat_id=beat.id,
                    kind=BriefKind.SKIPPED,
                    number=None,
                    published_at=datetime.now(UTC),
                    published_on=date(2026, 7, 27),
                    skip_line="Nothing material since Brief #4.",
                ),
                Brief(
                    beat_id=beat.id,
                    kind=BriefKind.SKIPPED,
                    number=None,
                    published_at=datetime.now(UTC),
                    published_on=date(2026, 8, 3),
                    skip_line="Still nothing material.",
                ),
            ]
        )
        await session.commit()  # must not raise

    async with db.async_session() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Brief)
            .where(Brief.beat_id == beat.id, Brief.kind == BriefKind.SKIPPED)
        )
    assert count == 2


@pytest.mark.anyio
async def test_the_brief_number_index_is_declared_on_both_model_and_migration() -> None:
    """Mirrors ``test_the_due_on_index_is_declared_on_both_model_and_migration``
    (``test_flashcards_schema.py``): the model's ``__table_args__`` and the
    migration that actually creates the index in a real database must agree,
    and the index must genuinely be partial.
    """
    briefs_table = cast("Table", Brief.__table__)
    model_index = next(
        index
        for index in briefs_table.indexes
        if index.name == "uq_briefs_beat_id_number"
    )
    assert [column.name for column in model_index.columns] == ["beat_id", "number"]
    assert model_index.unique is True
    assert model_index.dialect_options["postgresql"]["where"] is not None

    async with db.async_session() as session:
        indexdef = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'briefs' AND indexname = :name"
            ),
            {"name": "uq_briefs_beat_id_number"},
        )

    assert indexdef is not None
    assert "UNIQUE" in indexdef
    assert "beat_id" in indexdef
    assert "number IS NOT NULL" in indexdef


# --------------------------------------------------------------------------- #
# The claim protocol (D3) — reused unchanged from _generation.py
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_two_concurrent_beat_research_claims_exactly_one_winner() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        await session.commit()
        beat_id = beat.id

    async def claim() -> bool:
        async with db.async_session() as session:
            won = await BeatRepository(session).claim_research(beat_id)
            await session.commit()
            return won is not None

    results = await asyncio.gather(claim(), claim())
    assert results.count(True) == 1, results
    assert results.count(False) == 1, results

    async with db.async_session() as session:
        beat = await BeatRepository(session).get(beat_id)
        assert beat is not None
        assert beat.research_state is BeatResearchState.RESEARCHING
        assert beat.research_started_at is not None


@pytest.mark.anyio
async def test_failed_beat_reclaimable_only_via_retry() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(
            session,
            user=user,
            research_state=BeatResearchState.FAILED,
            research_error="retrieval unavailable",
        )
        await session.commit()
        beat_id = beat.id

    async with db.async_session() as session:
        # Auto claim (arrival drain) must NOT re-claim a real failure — D3's
        # whole point: an outage must not bill a fresh run on every page load.
        assert await BeatRepository(session).claim_research(beat_id) is None
        await session.rollback()

    async with db.async_session() as session:
        # The explicit learner retry re-claims it.
        fence = await BeatRepository(session).claim_research_for_retry(beat_id)
        assert fence is not None
        await session.commit()

    async with db.async_session() as session:
        beat = await BeatRepository(session).get(beat_id)
        assert beat is not None
        assert beat.research_state is BeatResearchState.RESEARCHING
        assert beat.research_error is None  # cleared on re-claim


@pytest.mark.anyio
async def test_refused_beat_is_terminal_never_reclaimed() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(
            session, user=user, research_state=BeatResearchState.REFUSED
        )
        await session.commit()
        beat_id = beat.id

    async with db.async_session() as session:
        repo = BeatRepository(session)
        assert await repo.claim_research(beat_id) is None
        assert await repo.claim_research_for_retry(beat_id) is None
        await session.rollback()


@pytest.mark.anyio
async def test_stale_researching_beat_is_reclaimable_fresh_not() -> None:
    now = datetime.now(UTC)
    async with db.async_session() as session:
        user = await create_user(session)
        stale = await _make_beat(
            session,
            user=user,
            research_state=BeatResearchState.RESEARCHING,
            research_started_at=now - STALE_AGE,
        )
        fresh = await _make_beat(
            session,
            user=user,
            topic="another topic",
            research_state=BeatResearchState.RESEARCHING,
            research_started_at=now - FRESH_AGE,
        )
        await session.commit()
        stale_id, fresh_id = stale.id, fresh.id

    async with db.async_session() as session:
        repo = BeatRepository(session, stale_after_seconds=TEST_STALE_AFTER_SECONDS)
        assert await repo.claim_research(stale_id) is not None
        assert await repo.claim_research(fresh_id) is None
        await session.commit()


# --------------------------------------------------------------------------- #
# The read ping — first-write-wins (D11)
# --------------------------------------------------------------------------- #


async def _published_brief(
    session: AsyncSession, *, beat_id: uuid.UUID, number: int = 1
) -> Brief:
    repo = BriefRepository(session)
    return await repo.create_published(
        beat_id=beat_id,
        number=number,
        published_at=datetime(2026, 8, 3, 9, tzinfo=UTC),
        published_on=date(2026, 8, 3),
        title="The backlash arrived",
        body_markdown="Body.",
        claims=["a claim"],
        sources=[_new_source()],
    )


@pytest.mark.anyio
async def test_a_second_read_ping_does_not_move_read_at() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        await session.commit()
        brief = await _published_brief(session, beat_id=beat.id)
        await session.commit()
        brief_id = brief.id

    async with db.async_session() as session:
        first = await BriefRepository(session).mark_read(brief_id)
        await session.commit()
    assert first is True

    async with db.async_session() as session:
        first_brief = await BriefRepository(session).get(brief_id)
        assert first_brief is not None
        first_read_at = first_brief.read_at

    async with db.async_session() as session:
        second = await BriefRepository(session).mark_read(brief_id)
        await session.commit()
    assert second is False

    async with db.async_session() as session:
        second_brief = await BriefRepository(session).get(brief_id)
        assert second_brief is not None
        second_read_at = second_brief.read_at
    assert second_read_at == first_read_at


@pytest.mark.anyio
async def test_a_second_sources_seen_ping_does_not_move_sources_seen_at() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        await session.commit()
        brief = await _published_brief(session, beat_id=beat.id)
        await session.commit()
        brief_id = brief.id

    async with db.async_session() as session:
        first = await BriefRepository(session).mark_sources_seen(brief_id)
        await session.commit()
    assert first is True

    async with db.async_session() as session:
        first_brief = await BriefRepository(session).get(brief_id)
        assert first_brief is not None
        first_seen_at = first_brief.sources_seen_at

    async with db.async_session() as session:
        second = await BriefRepository(session).mark_sources_seen(brief_id)
        await session.commit()
    assert second is False

    async with db.async_session() as session:
        second_brief = await BriefRepository(session).get(brief_id)
        assert second_brief is not None
        second_seen_at = second_brief.sources_seen_at
    assert second_seen_at == first_seen_at


# --------------------------------------------------------------------------- #
# Cascades (TDD §4)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_deleting_user_cascades_beats_briefs_and_sources() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        await session.commit()
        await _published_brief(session, beat_id=beat.id)
        await session.commit()
        user_id = user.id

    async with db.async_session() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Beat)) == 0
        assert await session.scalar(select(func.count()).select_from(Brief)) == 0
        assert await session.scalar(select(func.count()).select_from(BriefSource)) == 0


@pytest.mark.anyio
async def test_deleting_a_beat_cascades_its_briefs_and_sources() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        await session.commit()
        await _published_brief(session, beat_id=beat.id)
        await session.commit()
        beat_id = beat.id
        user_id = user.id

    async with db.async_session() as session:
        deleted = await BeatRepository(session).delete(beat_id)
        await session.commit()
    assert deleted is True

    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Brief)) == 0
        assert await session.scalar(select(func.count()).select_from(BriefSource)) == 0
        # The learner account survives deleting one of their Beats.
        assert await session.get(User, user_id) is not None


# --------------------------------------------------------------------------- #
# The rail read and the two continuity reads (§4/§5.4/§5.6)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_list_for_beat_interleaves_both_kinds_newest_first() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        await session.commit()
        repo = BriefRepository(session)
        await repo.create_published(
            beat_id=beat.id,
            number=1,
            published_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
            published_on=date(2026, 7, 20),
            title="First",
            body_markdown="Body one.",
            claims=["claim one"],
            sources=[_new_source()],
        )
        await repo.create_skipped(
            beat_id=beat.id,
            published_at=datetime(2026, 7, 27, 9, tzinfo=UTC),
            published_on=date(2026, 7, 27),
            skip_line="Nothing material since Brief #1.",
        )
        await repo.create_published(
            beat_id=beat.id,
            number=2,
            published_at=datetime(2026, 8, 3, 9, tzinfo=UTC),
            published_on=date(2026, 8, 3),
            title="Second",
            body_markdown="Body two.",
            claims=["claim two"],
            sources=[_new_source(url="https://example.com/b")],
        )
        await session.commit()
        beat_id = beat.id

    async with db.async_session() as session:
        entries = await BriefRepository(session).list_for_beat(beat_id)

    assert [(entry.kind, entry.number, entry.published_on) for entry in entries] == [
        (BriefKind.PUBLISHED, 2, date(2026, 8, 3)),
        (BriefKind.SKIPPED, None, date(2026, 7, 27)),
        (BriefKind.PUBLISHED, 1, date(2026, 7, 20)),
    ]


@pytest.mark.anyio
async def test_prior_claims_and_source_urls_span_the_whole_beat_history() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        beat = await _make_beat(session, user=user)
        await session.commit()
        repo = BriefRepository(session)
        await repo.create_published(
            beat_id=beat.id,
            number=1,
            published_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
            published_on=date(2026, 7, 20),
            title="First",
            body_markdown="Body one.",
            claims=["the commission opened a consultation"],
            sources=[_new_source(url="https://example.com/first")],
        )
        await repo.create_published(
            beat_id=beat.id,
            number=2,
            published_at=datetime(2026, 8, 3, 9, tzinfo=UTC),
            published_on=date(2026, 8, 3),
            title="Second",
            body_markdown="Body two.",
            claims=["the consultation closed"],
            sources=[_new_source(url="https://example.com/second")],
        )
        await session.commit()
        beat_id = beat.id

    async with db.async_session() as session:
        repo = BriefRepository(session)
        claims = await repo.prior_claims_for_beat(beat_id)
        urls = await repo.prior_source_urls_for_beat(beat_id)

    assert set(claims) == {
        "the commission opened a consultation",
        "the consultation closed",
    }
    assert urls == {"https://example.com/first", "https://example.com/second"}


@pytest.mark.anyio
async def test_another_beats_claims_and_sources_never_leak_in() -> None:
    async with db.async_session() as session:
        user = await create_user(session)
        owner_beat = await _make_beat(session, user=user, topic="owner topic")
        other_beat = await _make_beat(session, user=user, topic="other topic")
        await session.commit()
        repo = BriefRepository(session)
        await repo.create_published(
            beat_id=owner_beat.id,
            number=1,
            published_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
            published_on=date(2026, 7, 20),
            title="Owner's",
            body_markdown="Body.",
            claims=["owner claim"],
            sources=[_new_source(url="https://example.com/owner")],
        )
        await repo.create_published(
            beat_id=other_beat.id,
            number=1,
            published_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
            published_on=date(2026, 7, 20),
            title="Other's",
            body_markdown="Body.",
            claims=["other claim"],
            sources=[_new_source(url="https://example.com/other")],
        )
        await session.commit()
        owner_beat_id = owner_beat.id

    async with db.async_session() as session:
        repo = BriefRepository(session)
        claims = await repo.prior_claims_for_beat(owner_beat_id)
        urls = await repo.prior_source_urls_for_beat(owner_beat_id)

    assert claims == ["owner claim"]
    assert urls == {"https://example.com/owner"}
