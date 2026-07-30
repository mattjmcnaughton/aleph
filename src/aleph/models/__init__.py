"""SQLAlchemy ORM models.

One module per table (entity), with shared enums in :mod:`.enums`. All models
are imported here so ``Base.metadata`` sees every table and SQLAlchemy can
resolve cross-module relationship references through its class registry. Call
sites keep importing ``from aleph.models import X``.
"""

from __future__ import annotations

from aleph.models.attempt import Attempt
from aleph.models.conversation import Conversation
from aleph.models.enums import (
    LessonGenerationState,
    Level,
    MessageRole,
    MessageSource,
    PathStatus,
)
from aleph.models.lesson import Lesson
from aleph.models.message import Message
from aleph.models.path import Path
from aleph.models.quick_check import QuickCheck
from aleph.models.unit import Unit
from aleph.models.users import User

__all__ = [
    "Attempt",
    "Conversation",
    "Lesson",
    "LessonGenerationState",
    "Level",
    "Message",
    "MessageRole",
    "MessageSource",
    "Path",
    "PathStatus",
    "QuickCheck",
    "Unit",
    "User",
]
