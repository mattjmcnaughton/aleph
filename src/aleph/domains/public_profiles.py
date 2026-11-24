"""Pure Public profile rules: Handles, credit, and the sign-in copy intent.

Public profiles TDD §3 and §5 (CONTEXT.md: *Public profile*, *Handle*,
*Shared*, *Copy*). Stdlib only, like every module in this package.

- :func:`validate_handle` is the one statement of what a Handle may be. The
  ``ck_users_handle_format`` CHECK is the storage backstop for the grammar;
  the reserved list lives only here.
- :func:`credit_for` decides whether a Copy's source is still credited (D7).
  Credit is non-authoritative, so it is re-derived on every read.
- :func:`parse_copy_intent` and :func:`copy_destination` are the typed
  sign-in return (D6, SR 6): the only input is a ``kind:handle:uuid`` string,
  and the destination is built here, never taken from the request.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Literal

HANDLE_MIN_LENGTH = 3
HANDLE_MAX_LENGTH = 30

_HANDLE_CHARACTERS = re.compile(r"[a-z0-9-]+")

# Names that would read as Aleph speaking, or collide with a route. Compared
# after lowercasing.
RESERVED_HANDLES: frozenset[str] = frozenset(
    {
        "about",
        "account",
        "admin",
        "aleph",
        "api",
        "app",
        "auth",
        "beats",
        "briefs",
        "flow",
        "help",
        "home",
        "learning",
        "lessons",
        "login",
        "logout",
        "me",
        "new",
        "paths",
        "privacy",
        "public",
        "root",
        "settings",
        "staff",
        "support",
        "system",
        "terms",
    }
)

HandleRejection = Literal["too_short", "too_long", "bad_characters", "reserved"]


@dataclass(frozen=True)
class Handle:
    """A Handle that passed every rule (CONTEXT.md: *Handle*)."""

    value: str


@dataclass(frozen=True)
class HandleRejected:
    """Why a proposed Handle was refused."""

    reason: HandleRejection


def validate_handle(raw: str) -> Handle | HandleRejected:
    """Normalize (trim, lowercase) and check a proposed Handle."""
    value = raw.strip().lower()
    if len(value) < HANDLE_MIN_LENGTH:
        return HandleRejected("too_short")
    if len(value) > HANDLE_MAX_LENGTH:
        return HandleRejected("too_long")
    if not _HANDLE_CHARACTERS.fullmatch(value):
        return HandleRejected("bad_characters")
    if value in RESERVED_HANDLES:
        return HandleRejected("reserved")
    return Handle(value)


def is_handle(value: str) -> bool:
    """Whether ``value`` is already a valid, normalized Handle."""
    return validate_handle(value) == Handle(value)


@dataclass(frozen=True)
class SourceItem:
    """What credit needs to know about a Copy's source path or Beat.

    ``profiles_enabled`` is the ``public_profiles`` flag resolved for the
    source's owner (D9).
    """

    item_id: uuid.UUID
    shared: bool
    owner_display_name: str
    owner_handle: str | None
    owner_profile_public: bool
    profiles_enabled: bool


@dataclass(frozen=True)
class Credit:
    """\"Started from {display_name}'s path\": the credit line a Copy shows."""

    display_name: str
    handle: str
    item_id: uuid.UUID


def credit_for(source: SourceItem | None) -> Credit | None:
    """The source's credit while it is public, else ``None`` (D7).

    Public means: it still exists, it is Shared, and its owner's profile is
    on, has a Handle, and the flag is on for them.
    """
    if (
        source is None
        or not source.shared
        or not source.owner_profile_public
        or source.owner_handle is None
        or not source.profiles_enabled
    ):
        return None
    return Credit(
        display_name=source.owner_display_name,
        handle=source.owner_handle,
        item_id=source.item_id,
    )


CopyKind = Literal["path", "beat"]


@dataclass(frozen=True)
class CopyIntent:
    """Where sign-in returns a visitor who asked to copy a Shared item."""

    kind: CopyKind
    handle: str
    item_id: uuid.UUID


_INTENT_MAX_LENGTH = 100


def parse_copy_intent(raw: str | None) -> CopyIntent | None:
    """Parse ``path:<handle>:<uuid>`` or ``beat:<handle>:<uuid>``; else ``None``."""
    if not raw or len(raw) > _INTENT_MAX_LENGTH:
        return None
    parts = raw.split(":")
    if len(parts) != 3:
        return None
    kind, handle, raw_id = parts
    if kind not in ("path", "beat") or not is_handle(handle):
        return None
    try:
        item_id = uuid.UUID(raw_id)
    except ValueError:
        return None
    if str(item_id) != raw_id.lower():
        return None
    return CopyIntent(kind=kind, handle=handle, item_id=item_id)


def copy_destination(intent: CopyIntent) -> str:
    """The app-relative URL the form opens on: built here, never echoed."""
    base = "/new" if intent.kind == "path" else "/beats/new"
    return f"{base}?from={intent.handle}/{intent.item_id}"
