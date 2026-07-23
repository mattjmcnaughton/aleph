"""Database repositories for Aleph domain models (data access layer).

One module per bounded context (path, unit, lesson, quick check, attempt).
Repositories are constructed with an injected :class:`AsyncSession` and never
open or commit transactions — the service layer owns the unit of work. They
import models and ``db`` only (layering: routers -> services -> repositories).

Everything is re-exported here so call sites keep importing
``from aleph.repositories import X``.
"""

from __future__ import annotations

from aleph.repositories.attempts import AttemptRepository
from aleph.repositories.lessons import LessonRepository, PathGenerationProgress
from aleph.repositories.paths import PathRepository
from aleph.repositories.quick_checks import QuickCheckRepository
from aleph.repositories.units import UnitRepository

__all__ = [
    "AttemptRepository",
    "LessonRepository",
    "PathGenerationProgress",
    "PathRepository",
    "QuickCheckRepository",
    "UnitRepository",
]
