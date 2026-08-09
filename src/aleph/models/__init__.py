"""SQLAlchemy ORM models.

One module per table (entity), with shared enums in :mod:`.enums`. All models
are imported here so ``Base.metadata`` sees every table and SQLAlchemy can
resolve cross-module relationship references through its class registry. Call
sites keep importing ``from aleph.models import X``.
"""

from __future__ import annotations

from aleph.models.attempt import Attempt
from aleph.models.beat import Beat
from aleph.models.beat_research_run import BeatResearchRun
from aleph.models.brief import Brief, BriefSource
from aleph.models.conversation import Conversation
from aleph.models.enums import (
    BeatResearchState,
    BriefKind,
    ConversationKind,
    FlashcardDraftRunState,
    FlashcardGrade,
    LessonGenerationState,
    Level,
    MessageRole,
    MessageSource,
    PathChangeKind,
    PathChangeStatus,
    PathStatus,
)
from aleph.models.feature_flags import UserFeatureOverride
from aleph.models.flashcard import Flashcard, FlashcardDraftRun, FlashcardReview
from aleph.models.lesson import Lesson
from aleph.models.message import Message
from aleph.models.path import Path
from aleph.models.path_change import PathChange
from aleph.models.quick_check import QuickCheck
from aleph.models.unit import Unit
from aleph.models.users import User

__all__ = [
    "Attempt",
    "Beat",
    "BeatResearchRun",
    "BeatResearchState",
    "Brief",
    "BriefKind",
    "BriefSource",
    "Conversation",
    "ConversationKind",
    "Flashcard",
    "FlashcardDraftRun",
    "FlashcardDraftRunState",
    "FlashcardGrade",
    "FlashcardReview",
    "Lesson",
    "LessonGenerationState",
    "Level",
    "Message",
    "MessageRole",
    "MessageSource",
    "Path",
    "PathChange",
    "PathChangeKind",
    "PathChangeStatus",
    "PathStatus",
    "QuickCheck",
    "Unit",
    "User",
    "UserFeatureOverride",
]
