"""Lesson **Visuals** against real Postgres (docs/tdds/lesson-visuals.md).

* Generation stores a visual in the same transaction as the passage and
  rewrites the passage's placeholder fence to the row's UUID.
* The lesson API serves the visual by that UUID.
* A **Revision** deletes the visual with the content; **Undo** restores it under
  the same id, so the restored passage's fence still resolves.
* Continuity never carries another lesson's fence; the tutor sees title and alt
  only, never the source.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from aleph import db
from aleph.config import Settings
from aleph.domains.visuals import visual_fence_refs
from aleph.models import (
    Lesson,
    LessonGenerationState,
    LessonVisual,
    Level,
    Path,
    PathStatus,
    Unit,
)
from aleph.repositories import LessonVisualRepository
from aleph.services import generation as gen_module
from aleph.services.generation import GenerationOrchestrator
from aleph.services.stub_model import (
    COMPOUND_VISUAL_SOURCE,
    SHAPING_REVISION_INSTRUCTION,
    STUB_VISUAL_ALT,
    STUB_VISUAL_TITLE,
)
from aleph.services.tutor_context import assemble_lesson_context

from ._shaping_send_harness import (
    OWNER,
    _client,
    _seed_path,
    _seed_shaping_turn,
    _sign_in,
)
from ._shaping_send_harness import (
    app as app,  # noqa: PLC0414 - re-exported so the fixture resolves here
)
from ._shaping_send_harness import (
    isolated_shaping_limiter as isolated_shaping_limiter,  # noqa: PLC0414
)
from ._shaping_send_harness import (
    stub_shaping_model as stub_shaping_model,  # noqa: PLC0414
)
from .conftest import CollectingSpawn, create_user, stub_resolver

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI


@pytest.fixture(autouse=True)
async def spawn(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[CollectingSpawn]:
    """The generation singleton on the stub with a parked spawn (as shaping_apply)."""
    collector = CollectingSpawn(hold=True)
    monkeypatch.setattr(
        gen_module.generation_orchestrator, "_resolve_model", stub_resolver()
    )
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)
    yield collector
    await collector.cancel_pending()


async def _seed_ungenerated_path(
    *, positions: int = 2
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    async with db.async_session() as session:
        user = await create_user(session)
        path = Path(
            user_id=user.id,
            topic="Interest",
            level=Level.NEW_TO_IT,
            status=PathStatus.READY,
        )
        session.add(path)
        await session.flush()
        unit = Unit(path=path, position=1, title="Unit 1", summary="s")
        session.add(unit)
        await session.flush()
        ids: list[uuid.UUID] = []
        for position in range(1, positions + 1):
            lesson = Lesson(
                unit=unit,
                path=path,
                position_in_path=position,
                position_in_unit=position,
                title=f"Lesson {position}",
            )
            session.add(lesson)
            await session.flush()
            ids.append(lesson.id)
        await session.commit()
        return path.id, ids


async def _lesson(lesson_id: uuid.UUID) -> Lesson:
    async with db.async_session() as session:
        lesson = await session.get(Lesson, lesson_id)
        assert lesson is not None
        return lesson


async def _visuals(lesson_id: uuid.UUID) -> list[LessonVisual]:
    async with db.async_session() as session:
        return await LessonVisualRepository(session).list_for_lesson(lesson_id)


def _orchestrator() -> GenerationOrchestrator:
    return GenerationOrchestrator(
        session_factory=lambda: db.async_session(),
        spawn=CollectingSpawn(),
        resolve_model_fn=stub_resolver(),
        config=Settings(),
        prefetch_n=0,
    )


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_generation_stores_the_visual_and_fences_its_uuid() -> None:
    path_id, (first, second) = await _seed_ungenerated_path()

    await _orchestrator().ensure_generated_through(path_id, 2)

    lesson = await _lesson(first)
    assert lesson.generation_state is LessonGenerationState.GENERATED
    (visual,) = await _visuals(first)
    assert visual.title == STUB_VISUAL_TITLE
    assert visual.alt == STUB_VISUAL_ALT
    assert visual.source == COMPOUND_VISUAL_SOURCE
    assert visual.kit_version == 1
    assert lesson.read_passage is not None
    # The placeholder never reaches storage: the fence names the row's UUID.
    assert visual_fence_refs(lesson.read_passage) == [str(visual.id)]
    assert "visual-1" not in lesson.read_passage
    # The stub's later lessons carry none.
    assert await _visuals(second) == []


@pytest.mark.anyio
async def test_continuity_strips_earlier_visual_fences() -> None:
    path_id, _ids = await _seed_ungenerated_path()
    orchestrator = _orchestrator()
    await orchestrator.ensure_generated_through(path_id, 1)

    async with db.async_session() as session:
        prior = await orchestrator.build_prior_context(session, path_id, 2)

    (passage,) = prior
    assert "```visual" not in passage.read_passage
    assert "Try it" in passage.read_passage  # the prose half stays


@pytest.mark.anyio
async def test_deleting_a_lesson_cascades_its_visuals() -> None:
    path_id, (first, _second) = await _seed_ungenerated_path()
    await _orchestrator().ensure_generated_through(path_id, 1)
    assert await _visuals(first)

    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        await session.delete(path)
        await session.commit()

    async with db.async_session() as session:
        rows = (await session.execute(select(LessonVisual))).scalars().all()
    assert list(rows) == []


# --------------------------------------------------------------------------- #
# API, Revision, Undo, tutor context
# --------------------------------------------------------------------------- #

_SOURCE = "<p>static</p><script>kit.ready()</script>"


async def _attach_visual(lesson_id: uuid.UUID) -> uuid.UUID:
    """Give a seeded lesson a stored visual and a passage that fences it."""
    visual_id = uuid.uuid4()
    async with db.async_session() as session:
        await LessonVisualRepository(session).create(
            visual_id=visual_id,
            lesson_id=lesson_id,
            title="Compound interest",
            alt="Two bars compare simple and compound growth.",
            source=_SOURCE,
        )
        lesson = await session.get(Lesson, lesson_id)
        assert lesson is not None
        lesson.read_passage = (
            f"{lesson.read_passage}\n\n```visual {visual_id}\n```\n\nAfter."
        )
        await session.commit()
    return visual_id


@pytest.mark.anyio
async def test_the_lesson_api_serves_the_visual_by_uuid(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        _path_id, lesson_ids = await _seed_path(user_id)
        visual_id = await _attach_visual(lesson_ids[0])

        detail = await client.get(f"/api/v1/lessons/{lesson_ids[0]}")
        other = await client.get(f"/api/v1/lessons/{lesson_ids[1]}")

    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["visuals"] == [
        {
            "id": str(visual_id),
            "kind": "interactive",
            "title": "Compound interest",
            "alt": "Two bars compare simple and compound growth.",
            "source": _SOURCE,
            "kit_version": 1,
        }
    ]
    assert f"```visual {visual_id}" in body["read_passage"]
    assert other.json()["visuals"] == []


@pytest.mark.anyio
async def test_a_revision_drops_the_visual_and_undo_restores_it_by_id(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_ids = await _seed_path(user_id)
        target = lesson_ids[0]
        visual_id = await _attach_visual(target)
        before = await _lesson(target)
        _learner, message_id = await _seed_shaping_turn(
            path_id=path_id,
            proposal={
                "operations": [
                    {
                        "lesson_id": str(target),
                        "instruction": SHAPING_REVISION_INSTRUCTION,
                        "rationale": "Not started yet.",
                        "new_title": None,
                    }
                ],
                "summary": "Re-teaches one lesson.",
            },
        )

        applied = await client.post(f"/api/v1/messages/{message_id}/apply-proposal")
        assert applied.status_code == 200, applied.text
        assert await _visuals(target) == []
        assert (await _lesson(target)).read_passage is None

        undone = await client.post(
            f"/api/v1/changes/{applied.json()['change']['id']}/undo"
        )

    assert undone.status_code == 204, undone.text
    restored = await _lesson(target)
    assert restored.read_passage == before.read_passage
    (visual,) = await _visuals(target)
    assert visual.id == visual_id
    assert visual.source == _SOURCE
    assert visual_fence_refs(restored.read_passage or "") == [str(visual_id)]


@pytest.mark.anyio
async def test_the_tutor_sees_title_and_alt_never_source(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
    path_id, lesson_ids = await _seed_path(user_id)
    await _attach_visual(lesson_ids[0])

    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        assert path is not None
        context = await assemble_lesson_context(
            session, path=path, lesson_id=lesson_ids[0]
        )

    (summary,) = context.deps.visuals
    assert summary.title == "Compound interest"
    assert summary.alt == "Two bars compare simple and compound growth."
    assert not hasattr(summary, "source")
