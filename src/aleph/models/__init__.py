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
from aleph.models.beat_scan_progress import BeatScanProgress
from aleph.models.brief import Brief, BriefSource
from aleph.models.conversation import Conversation
from aleph.models.device import Device
from aleph.models.enums import (
    BeatResearchState,
    BriefKind,
    ConversationKind,
    LessonGenerationState,
    Level,
    MessageRole,
    MessageSource,
    PathChangeKind,
    PathChangeStatus,
    PathStatus,
)
from aleph.models.feature_flags import UserFeatureOverride
from aleph.models.lesson import Lesson
from aleph.models.message import Message
from aleph.models.notification_preferences import NotificationPreferences
from aleph.models.path import Path
from aleph.models.path_change import PathChange
from aleph.models.quick_check import QuickCheck
from aleph.models.reminder_send import ReminderSend
from aleph.models.unit import Unit
from aleph.models.users import User

__all__ = [
    "Attempt",
    "Beat",
    "BeatResearchRun",
    "BeatResearchState",
    "BeatScanProgress",
    "Brief",
    "BriefKind",
    "BriefSource",
    "Conversation",
    "ConversationKind",
    "Device",
    "Lesson",
    "LessonGenerationState",
    "Level",
    "Message",
    "MessageRole",
    "MessageSource",
    "NotificationPreferences",
    "Path",
    "PathChange",
    "PathChangeKind",
    "PathChangeStatus",
    "PathStatus",
    "QuickCheck",
    "ReminderSend",
    "Unit",
    "User",
    "UserFeatureOverride",
]
