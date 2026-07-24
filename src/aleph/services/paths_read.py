"""Read-side composition for the Paths API detail view (AL-050, TDD §6).

The path-detail poll target (``GET /paths/{id}``) is not a single read: it polls
the orchestrator (trigger + effective-status/refusal/progress snapshot) and
assembles the outline from three repositories plus the pure progression domain —
the units, each lesson's **effective** generation state (stale ``generating`` →
``failed``, §5.4), and each lesson's **derived** unlock state (the two orthogonal
axes, CONTEXT.md). That assembly lives here, behind one function, so the router
stays parse/authz/translate and AL-051's lesson detail reuses the same seam
instead of copying a ~70-line handler (layering: routers -> services ->
repositories/domains).

The composed :class:`PathDetailView` carries no ``Path`` identity fields (topic,
level, id) — the router already holds the owned ``Path`` row from its ownership
check and maps those directly, so this seam never re-reads them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aleph.domains.progression import LessonProgress, derive_unlock_states
from aleph.repositories import LessonRepository, UnitRepository

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.domains.progression import UnlockState
    from aleph.models import LessonGenerationState, PathStatus
    from aleph.repositories import PathGenerationProgress
    from aleph.services.generation import GenerationOrchestrator


@dataclass(frozen=True)
class LessonSlotView:
    """One lesson's slot in the composed path outline (both axes resolved).

    Named ``LessonSlotView`` (not ``LessonDetailView``) to avoid colliding with
    ``lessons_read.LessonDetailView`` — the *whole* lesson-detail view AL-051's
    ``GET /lessons/{id}`` composes. This one is only a lesson's summary slot
    inside a path's outline (TN-2)."""

    id: uuid.UUID
    title: str
    position_in_path: int
    position_in_unit: int
    generation_state: LessonGenerationState
    unlock_state: UnlockState


@dataclass(frozen=True)
class UnitDetailView:
    """A unit and its ordered lessons in the composed outline."""

    id: uuid.UUID
    title: str
    summary: str
    position: int
    lessons: list[LessonSlotView]


@dataclass(frozen=True)
class PathDetailView:
    """The composed path-detail snapshot the router translates to the DTO.

    ``status`` is the **effective** path status and ``refusal_message`` is set
    only for a ``refused`` path (both straight from the orchestrator snapshot);
    ``units`` is the assembled outline.
    """

    status: PathStatus
    refusal_message: str | None
    progress: PathGenerationProgress
    units: list[UnitDetailView]


async def load_path_detail(
    session: AsyncSession,
    orchestrator: GenerationOrchestrator,
    path_id: uuid.UUID,
) -> PathDetailView | None:
    """Poll + assemble the detail view for an already-owned path, or ``None``.

    ``poll_path`` both spawns the idempotent resume (poll-as-trigger, §5.4) and
    returns the effective status/refusal/progress snapshot. ``None`` means the
    path vanished between the caller's ownership read and the poll (a raced
    delete) — the router maps that to ``404``. On a live path the outline is
    loaded and each lesson's effective + derived state is zipped back on.

    Unlock states are derived over **all** lessons in ``position_in_path`` order,
    then split by unit. Within a unit, lessons are appended in ``position_in_path``
    order — which is ``position_in_unit`` order (a unit's lessons are contiguous
    in the total order) — so no per-unit re-sort is needed.
    """
    snapshot = await orchestrator.poll_path(path_id)
    if snapshot is None:
        return None

    units = await UnitRepository(session).list_for_path(path_id)
    lessons = await LessonRepository(session).list_for_path_with_effective_state(
        path_id
    )
    unlock_states = derive_unlock_states(
        [
            LessonProgress(
                position_in_path=lesson.position_in_path,
                completed_at=lesson.completed_at,
            )
            for lesson, _ in lessons
        ]
    )
    lessons_by_unit: dict[uuid.UUID, list[LessonSlotView]] = defaultdict(list)
    for (lesson, effective_state), unlock_state in zip(
        lessons, unlock_states, strict=True
    ):
        lessons_by_unit[lesson.unit_id].append(
            LessonSlotView(
                id=lesson.id,
                title=lesson.title,
                position_in_path=lesson.position_in_path,
                position_in_unit=lesson.position_in_unit,
                generation_state=effective_state,
                unlock_state=unlock_state,
            )
        )

    return PathDetailView(
        status=snapshot.status,
        refusal_message=snapshot.refusal_message,
        progress=snapshot.progress,
        units=[
            UnitDetailView(
                id=unit.id,
                title=unit.title,
                summary=unit.summary,
                position=unit.position,
                lessons=lessons_by_unit[unit.id],
            )
            for unit in units
        ],
    )
