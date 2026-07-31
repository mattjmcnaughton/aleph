"""``assemble_shaping_context`` against a real Postgres database (2B §5.2, D9).

The seam is defined by what it reads and by what it must **not** do, and both
only mean something against real rows:

* **Deps** — topic and level, the shaping digest (names, position, unlock state,
  the D2 ``engaged`` flag, each attempted lesson's **Outcome**), the **Change
  history** as plain lines with status, and the caps the agent is handed as
  data (``first_shapeable_position``, ``lessons_remaining``).
* **History** — the most recent ``TUTOR_CONTEXT_TURNS`` turns of the *stored*
  **shaping** thread, oldest first, with a prior **Proposal** serialized as
  compact text. The in-lesson thread on the same path is invisible from here
  (D3), which is the kind-scoping W21 depends on.
* **No lesson bodies, ever** — the whole shaping-scope bound (PRD §5.2). The
  fixture path's lessons all carry a Read passage; none of it may reach the deps
  or the history.
* **Purity** — no writes and no generation triggers, for the same reason the
  in-lesson seam has none: the Phase 1 read seams poll-as-trigger, and shaping a
  path must not start generating lessons. Asserted by fingerprinting the
  database either side of an assembly that commits its session.

The digest mapping, the caps arithmetic, resolution derivation and the window
arithmetic are pure and covered in ``tests/unit/test_shaping_context.py``; this
module proves they compose over rows the repositories actually return.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select

from aleph import db
from aleph.agents.shaper import (
    FIRST_SHAPEABLE_LESSON_ID_MARKER,
    FIRST_SHAPEABLE_POSITION_MARKER,
    render_shaping_context,
)
from aleph.config import settings
from aleph.domains.grading import Outcome
from aleph.domains.progression import UnlockState
from aleph.models import (
    Attempt,
    Conversation,
    ConversationKind,
    Lesson,
    LessonGenerationState,
    Level,
    Message,
    MessageSource,
    Path,
    PathChange,
    PathChangeKind,
    PathChangeStatus,
    QuickCheck,
    Unit,
    User,
)
from aleph.repositories import ChangeRepository, ConversationRepository
from aleph.services.tutor_context import assemble_shaping_context

from .conftest import create_user

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.agents.shaper import ShaperDeps
    from aleph.services.tutor_context import AssembledContext

PASSAGE = "Ownership is Rust's memory model: one owner at a time."
STEM = "Which binding owns the String after a move?"
OPTIONS = ["The first", "The second", "Neither"]
CORRECT_INDEX = 1
EXPLANATION = "A move transfers ownership to the new binding."


# --------------------------------------------------------------------------- #
# Arrange helpers
#
# One path, two units, four lessons in ``position_in_path`` order:
#   1 complete (never attempted), 2 attempted-incorrectly, 3 and 4 untouched.
# That shape gives the digest every state the mapping has to distinguish, puts
# the engagement boundary at position 3, and leaves two *ungenerated* lessons a
# poll-as-trigger read would have claimed for generation.
# --------------------------------------------------------------------------- #


async def _arrange(*, username: str = "test-user") -> tuple[uuid.UUID, uuid.UUID]:
    """Commit the fixture path; return ``(user_id, path_id)``."""
    async with db.async_session() as session:
        user = await create_user(session, username=username)
        path = Path(user_id=user.id, topic="Rust ownership", level=Level.NEW_TO_IT)
        session.add(path)
        await session.flush()

        foundations = Unit(
            path=path, position=1, title="Foundations", summary="The basics"
        )
        borrowing = Unit(path=path, position=2, title="Borrowing", summary="References")
        session.add_all([foundations, borrowing])
        await session.flush()

        first = _lesson(
            path,
            foundations,
            position=1,
            title="What ownership is",
            state=LessonGenerationState.GENERATED,
            completed=True,
        )
        second = _lesson(
            path,
            foundations,
            position=2,
            title="Moves and copies",
            state=LessonGenerationState.GENERATED,
        )
        third = _lesson(
            path,
            borrowing,
            position=3,
            title="Shared references",
            state=LessonGenerationState.UNGENERATED,
        )
        fourth = _lesson(
            path,
            borrowing,
            position=4,
            title="Mutable references",
            state=LessonGenerationState.UNGENERATED,
        )
        session.add_all([first, second, third, fourth])
        await session.flush()

        check = QuickCheck(
            lesson_id=second.id,
            stem=STEM,
            options=OPTIONS,
            correct_index=CORRECT_INDEX,
            explanation=EXPLANATION,
        )
        session.add(check)
        await session.flush()
        session.add(
            Attempt(
                quick_check_id=check.id,
                user_id=user.id,
                selected_index=0,
                # Deliberately disagrees with the keyed answer: the seam must
                # re-grade from ``selected_index`` (AL-012), never read this.
                is_correct=True,
            )
        )
        await session.commit()
        return user.id, path.id


def _lesson(
    path: Path,
    unit: Unit,
    *,
    position: int,
    title: str,
    state: LessonGenerationState,
    completed: bool = False,
) -> Lesson:
    return Lesson(
        unit=unit,
        path=path,
        position_in_path=position,
        position_in_unit=position,
        title=title,
        generation_state=state,
        # Every lesson carries a body, including the ungenerated ones: the point
        # of half these assertions is that none of it can reach shaping scope.
        read_passage=PASSAGE,
        completed_at=datetime.now(UTC) if completed else None,
    )


async def _owned_path(session: AsyncSession, path_id: uuid.UUID) -> Path:
    """The ``Path`` row the router hands the seam (its ownership check's result)."""
    path = await session.get(Path, path_id)
    assert path is not None
    return path


async def _lesson_id_at(path_id: uuid.UUID, position: int) -> uuid.UUID:
    async with db.async_session() as session:
        lesson_id = await session.scalar(
            select(Lesson.id).where(
                Lesson.path_id == path_id, Lesson.position_in_path == position
            )
        )
    assert lesson_id is not None
    return lesson_id


async def _record_turns(
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    count: int,
    *,
    kind: ConversationKind = ConversationKind.SHAPING,
    proposal: dict[str, Any] | None = None,
) -> list[uuid.UUID]:
    """Append ``count`` turns to ``kind``'s thread; return the tutor message ids.

    ``proposal`` rides on the **last** tutor row, as the shaping turn service
    persists an observed Proposal payload.
    """
    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _created = await repository.upsert_for_path(path_id, kind=kind)
        tutor_ids: list[uuid.UUID] = []
        for index in range(1, count + 1):
            _learner, tutor = await repository.insert_turn(
                conversation_id=conversation.id,
                lesson_id=lesson_id,
                learner_content=f"question {index}",
                source=MessageSource.TYPED,
                tutor_content=f"reply {index}",
                proposal=proposal if index == count else None,
            )
            tutor_ids.append(tutor.id)
        await session.commit()
        return tutor_ids


async def _create_change(
    *,
    path_id: uuid.UUID,
    message_id: uuid.UUID | None = None,
    kind: PathChangeKind = PathChangeKind.ADD_LESSONS,
    payload: dict[str, Any],
    status: PathChangeStatus = PathChangeStatus.APPLIED,
) -> uuid.UUID:
    async with db.async_session() as session:
        change = await ChangeRepository(session).create(
            path_id=path_id, message_id=message_id, kind=kind, payload=payload
        )
        if status is PathChangeStatus.UNDONE:
            change.status = status
            change.undone_at = datetime.now(UTC)
        await session.commit()
        return change.id


def _add_proposal(*, position: int, title: str) -> dict[str, Any]:
    return {
        "summary": f"Adds one lesson on {title} (about 5 min).",
        "operations": [
            {
                "insert_at_position": position,
                "lessons": [{"title": title}],
                "rationale": "It is the gap before borrowing makes sense.",
                "estimated_minutes": 5,
                "new_unit": None,
            }
        ],
    }


async def _assemble(path_id: uuid.UUID) -> AssembledContext[ShaperDeps]:
    async with db.async_session() as session:
        return await assemble_shaping_context(
            session, path=await _owned_path(session, path_id)
        )


def _texts(context: AssembledContext[ShaperDeps]) -> list[str]:
    flattened: list[str] = []
    for message in context.message_history:
        for part in message.parts:
            content = getattr(part, "content", "")
            flattened.append(content if isinstance(content, str) else str(content))
    return flattened


# --------------------------------------------------------------------------- #
# Deps: the shaping digest
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_deps_carry_the_paths_topic_and_level() -> None:
    _user_id, path_id = await _arrange()

    context = await _assemble(path_id)

    assert context.deps.topic == "Rust ownership"
    assert context.deps.level == "beginner"


@pytest.mark.anyio
async def test_digest_maps_every_lesson_state_to_outcome_and_engagement() -> None:
    """The four states shaping scope has to tell apart, over real rows."""
    _user_id, path_id = await _arrange()

    context = await _assemble(path_id)

    digest = context.deps.digest
    assert [entry.lesson_title for entry in digest] == [
        "What ownership is",
        "Moves and copies",
        "Shared references",
        "Mutable references",
    ]
    assert [entry.unit_title for entry in digest] == [
        "Foundations",
        "Foundations",
        "Borrowing",
        "Borrowing",
    ]
    assert [entry.unlock_state for entry in digest] == [
        UnlockState.COMPLETE,
        UnlockState.AVAILABLE,
        UnlockState.LOCKED,
        UnlockState.LOCKED,
    ]
    # complete-without-attempt, attempted, untouched, untouched
    assert [entry.engaged for entry in digest] == [True, True, False, False]
    assert [entry.outcome for entry in digest] == [
        None,
        Outcome.INCORRECT,
        None,
        None,
    ]


@pytest.mark.anyio
async def test_the_outcome_is_regraded_never_read_from_is_correct() -> None:
    """The fixture Attempt stores ``is_correct=True`` against a wrong index."""
    _user_id, path_id = await _arrange()

    context = await _assemble(path_id)

    assert context.deps.digest[1].outcome is Outcome.INCORRECT


@pytest.mark.anyio
async def test_outcomes_are_scoped_to_the_paths_own_lessons() -> None:
    """A second path's identical fixture must not colour this path's digest."""
    _user_id, path_id = await _arrange()
    _other_user_id, _other_path_id = await _arrange(username="other-learner")

    context = await _assemble(path_id)

    assert [entry.outcome for entry in context.deps.digest] == [
        None,
        Outcome.INCORRECT,
        None,
        None,
    ]
    assert len(context.deps.digest) == 4  # noqa: PLR2004 - the fixture path's lessons


@pytest.mark.anyio
async def test_digest_lesson_ids_are_the_real_rows() -> None:
    """``revise_lesson`` names its target by id, so they must be resolvable."""
    _user_id, path_id = await _arrange()

    context = await _assemble(path_id)

    assert context.deps.digest[2].lesson_id == str(await _lesson_id_at(path_id, 3))


@pytest.mark.anyio
async def test_deps_carry_no_lesson_body() -> None:
    """Shaping scope is names, states, outcomes and history — never a body."""
    _user_id, path_id = await _arrange()

    context = await _assemble(path_id)

    rendered = render_shaping_context(context.deps)
    assert PASSAGE not in rendered
    assert STEM not in rendered
    assert EXPLANATION not in rendered
    for option in OPTIONS:
        assert option not in rendered
    assert PASSAGE not in repr(context.deps)


# --------------------------------------------------------------------------- #
# Deps: the caps, computed here and handed over as data (§5.1)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_caps_state_the_engagement_boundary_as_data() -> None:
    _user_id, path_id = await _arrange()

    context = await _assemble(path_id)

    # Lessons 1 (complete) and 2 (attempted) are engaged; 3 is the boundary.
    assert context.deps.caps.first_shapeable_position == 3  # noqa: PLR2004
    assert context.deps.caps.lessons_remaining == settings.max_lessons_per_path - 4
    assert (
        context.deps.caps.max_lessons_per_proposal == settings.max_lessons_per_proposal
    )


@pytest.mark.anyio
async def test_the_boundary_markers_are_rendered_exactly_once() -> None:
    """AL-302 reads them first-match-wins; the seam must not add a second copy."""
    _user_id, path_id = await _arrange()
    lesson_id = await _lesson_id_at(path_id, 3)
    await _record_turns(
        path_id, lesson_id, count=1, proposal=_add_proposal(position=3, title="Slices")
    )

    context = await _assemble(path_id)

    request = "\n".join([render_shaping_context(context.deps), *_texts(context)])
    assert request.count(FIRST_SHAPEABLE_POSITION_MARKER) == 1
    assert request.count(FIRST_SHAPEABLE_LESSON_ID_MARKER) == 1


# --------------------------------------------------------------------------- #
# Deps: the Change history
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_change_history_is_empty_on_an_unshaped_path() -> None:
    _user_id, path_id = await _arrange()

    context = await _assemble(path_id)

    assert context.deps.change_history == ()


@pytest.mark.anyio
async def test_change_history_carries_plain_lines_newest_first_with_status() -> None:
    _user_id, path_id = await _arrange()
    await _create_change(
        path_id=path_id,
        payload={"summary": "Added 1 lesson on slices."},
    )
    await _create_change(
        path_id=path_id,
        kind=PathChangeKind.REVISE_LESSON,
        payload={"summary": "Revised 'Shared references' to go slower."},
        status=PathChangeStatus.UNDONE,
    )

    context = await _assemble(path_id)

    assert [entry.summary for entry in context.deps.change_history] == [
        "Revised 'Shared references' to go slower.",
        "Added 1 lesson on slices.",
    ]
    assert [entry.status for entry in context.deps.change_history] == [
        "undone",
        "applied",
    ]


@pytest.mark.anyio
async def test_change_history_is_scoped_to_its_path() -> None:
    _user_id, path_id = await _arrange()
    _other_user_id, other_path_id = await _arrange(username="other-learner")
    await _create_change(
        path_id=other_path_id, payload={"summary": "Another path's change."}
    )

    context = await _assemble(path_id)

    assert context.deps.change_history == ()


# --------------------------------------------------------------------------- #
# History: the shaping thread only, windowed
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_empty_shaping_thread_yields_empty_history() -> None:
    _user_id, path_id = await _arrange()

    context = await _assemble(path_id)

    assert context.message_history == []


@pytest.mark.anyio
async def test_history_carries_the_configured_window_oldest_first() -> None:
    _user_id, path_id = await _arrange()
    lesson_id = await _lesson_id_at(path_id, 3)
    window = settings.tutor_context_turns
    await _record_turns(path_id, lesson_id, count=window + 2)

    context = await _assemble(path_id)

    carried = _texts(context)
    assert len(carried) == window * 2
    assert carried[:2] == ["question 3", "reply 3"]
    assert carried[-2:] == [f"question {window + 2}", f"reply {window + 2}"]


@pytest.mark.anyio
async def test_the_in_lesson_thread_is_invisible_from_shaping_scope() -> None:
    """Two threads per path; the shaping seam reads exactly one (D3, W21)."""
    _user_id, path_id = await _arrange()
    lesson_id = await _lesson_id_at(path_id, 2)
    await _record_turns(path_id, lesson_id, count=2, kind=ConversationKind.LESSON)

    context = await _assemble(path_id)

    assert context.message_history == []


# --------------------------------------------------------------------------- #
# History: a prior Proposal and its derived resolution (§4)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_pending_proposal_rides_as_compact_text() -> None:
    _user_id, path_id = await _arrange()
    lesson_id = await _lesson_id_at(path_id, 3)
    proposal = _add_proposal(position=3, title="Slices")
    await _record_turns(path_id, lesson_id, count=1, proposal=proposal)

    context = await _assemble(path_id)

    carried = _texts(context)
    assert carried[0] == "question 1"
    assert carried[1] == f"reply 1\n\n[Proposal — pending] {proposal['summary']}"
    assert "insert_at_position" not in carried[1]


@pytest.mark.anyio
async def test_an_applied_proposal_reads_as_applied() -> None:
    _user_id, path_id = await _arrange()
    lesson_id = await _lesson_id_at(path_id, 3)
    proposal = _add_proposal(position=3, title="Slices")
    (tutor_id,) = await _record_turns(path_id, lesson_id, count=1, proposal=proposal)
    await _create_change(
        path_id=path_id,
        message_id=tutor_id,
        payload={"summary": proposal["summary"], **proposal},
    )

    context = await _assemble(path_id)

    assert "[Proposal — applied]" in _texts(context)[1]


@pytest.mark.anyio
async def test_an_undone_proposal_reads_as_undone() -> None:
    _user_id, path_id = await _arrange()
    lesson_id = await _lesson_id_at(path_id, 3)
    proposal = _add_proposal(position=3, title="Slices")
    (tutor_id,) = await _record_turns(path_id, lesson_id, count=1, proposal=proposal)
    await _create_change(
        path_id=path_id,
        message_id=tutor_id,
        payload=proposal,
        status=PathChangeStatus.UNDONE,
    )

    context = await _assemble(path_id)

    assert "[Proposal — undone]" in _texts(context)[1]


@pytest.mark.anyio
async def test_a_proposal_a_later_apply_invalidated_reads_as_superseded() -> None:
    """The later Change took the title; the earlier card can no longer apply."""
    _user_id, path_id = await _arrange()
    lesson_id = await _lesson_id_at(path_id, 3)
    proposal = _add_proposal(position=3, title="Shared references")
    (earlier_id,) = await _record_turns(path_id, lesson_id, count=1, proposal=proposal)
    (later_id,) = await _record_turns(
        path_id, lesson_id, count=1, proposal=_add_proposal(position=3, title="Slices")
    )
    await _create_change(path_id=path_id, message_id=later_id, payload=proposal)

    context = await _assemble(path_id)

    assert earlier_id != later_id
    carried = _texts(context)
    # The earlier proposal's title now collides with a lesson on the path.
    assert "[Proposal — superseded]" in carried[1]
    assert "[Proposal — applied]" in carried[3]


# --------------------------------------------------------------------------- #
# Purity: the seam reads and never triggers (§5.2)
# --------------------------------------------------------------------------- #


async def _fingerprint() -> tuple[Any, ...]:
    async with db.async_session() as session:
        lessons = (
            await session.execute(
                select(
                    Lesson.id,
                    Lesson.generation_state,
                    Lesson.generation_started_at,
                    Lesson.completed_at,
                ).order_by(Lesson.id)
            )
        ).all()
        counts = []
        for model in (
            User,
            Path,
            Unit,
            Lesson,
            QuickCheck,
            Attempt,
            Conversation,
            Message,
            PathChange,
        ):
            counts.append(await session.scalar(select(func.count()).select_from(model)))
        return (tuple(lessons), tuple(counts))


@pytest.mark.anyio
async def test_assembly_writes_nothing_and_triggers_no_generation() -> None:
    """Shaping a path must no more start generation than asking a question does.

    Two *ungenerated* lessons sit on the fixture path; the paths/lessons read
    seams would poll-as-trigger and claim them (stamping
    ``generation_started_at``). The seam's session is committed here, so an
    accidental write would land rather than being rolled back.
    """
    _user_id, path_id = await _arrange()
    lesson_id = await _lesson_id_at(path_id, 3)
    await _record_turns(
        path_id, lesson_id, count=2, proposal=_add_proposal(position=3, title="Slices")
    )
    await _create_change(path_id=path_id, payload={"summary": "Added 1 lesson."})
    before = await _fingerprint()

    async with db.async_session() as session:
        await assemble_shaping_context(
            session, path=await _owned_path(session, path_id)
        )
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        await session.commit()

    assert await _fingerprint() == before
