"""Pure Public profile rules: Handles, credit, and the sign-in copy intent.

Public profiles TDD §3 and §5. Concrete examples and counterexamples, no I/O.
"""

from __future__ import annotations

import uuid

import pytest

from aleph.domains.public_profiles import (
    CopyIntent,
    Credit,
    Handle,
    HandleRejected,
    HandleRejection,
    SourceItem,
    copy_destination,
    credit_for,
    parse_copy_intent,
    validate_handle,
)

ITEM = uuid.UUID("5b0f4c1e-1a2b-4c3d-8e9f-0a1b2c3d4e5f")


# --------------------------------------------------------------------------- #
# Handles
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw", ["abc", "matt", "a-b-c", "x" * 30, "learner-42"])
def test_valid_handles_are_accepted(raw: str) -> None:
    assert validate_handle(raw) == Handle(raw)


def test_a_handle_is_lowercased_and_trimmed_before_checking() -> None:
    assert validate_handle("  Matt ") == Handle("matt")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("ab", "too_short"),
        ("", "too_short"),
        ("x" * 31, "too_long"),
        ("matt_m", "bad_characters"),
        ("matt.m", "bad_characters"),
        ("mätt", "bad_characters"),
        ("a b c", "bad_characters"),
        ("admin", "reserved"),
        ("aleph", "reserved"),
        ("settings", "reserved"),
        ("new", "reserved"),
        ("Login", "reserved"),
    ],
)
def test_invalid_handles_are_rejected_with_a_reason(
    raw: str, reason: HandleRejection
) -> None:
    assert validate_handle(raw) == HandleRejected(reason)


# --------------------------------------------------------------------------- #
# Credit (D7): shown only while the source is public
# --------------------------------------------------------------------------- #


def _source(**overrides: object) -> SourceItem:
    values: dict[str, object] = {
        "item_id": ITEM,
        "shared": True,
        "owner_display_name": "Matt McNaughton",
        "owner_handle": "matt",
        "owner_profile_public": True,
        "profiles_enabled": True,
    }
    values.update(overrides)
    return SourceItem(**values)  # type: ignore[arg-type]


def test_a_public_source_is_credited() -> None:
    assert credit_for(_source()) == Credit(
        display_name="Matt McNaughton", handle="matt", item_id=ITEM
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"shared": False},
        {"owner_profile_public": False},
        {"owner_handle": None},
        {"profiles_enabled": False},
    ],
)
def test_a_source_that_is_not_public_is_not_credited(overrides: dict) -> None:
    assert credit_for(_source(**overrides)) is None


def test_a_missing_source_is_not_credited() -> None:
    assert credit_for(None) is None


# --------------------------------------------------------------------------- #
# Sign-in copy intent (§5): a typed grammar, never a URL
# --------------------------------------------------------------------------- #


def test_a_path_intent_parses_and_builds_its_own_destination() -> None:
    intent = parse_copy_intent(f"path:matt:{ITEM}")
    assert intent is not None
    assert intent == CopyIntent(kind="path", handle="matt", item_id=ITEM)
    assert copy_destination(intent) == f"/new?from=matt/{ITEM}"


def test_a_beat_intent_goes_to_new_beat() -> None:
    intent = parse_copy_intent(f"beat:matt:{ITEM}")
    assert intent is not None
    assert copy_destination(intent) == f"/beats/new?from=matt/{ITEM}"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "https://evil.example",
        "//evil.example",
        f"lesson:matt:{ITEM}",
        f"path:ab:{ITEM}",
        f"path:admin:{ITEM}",
        f"path:Matt/../x:{ITEM}",
        "path:matt:not-a-uuid",
        f"path:matt:{ITEM}:extra",
        f"path:matt:{ITEM}?next=https://evil.example",
        "x" * 5000,
    ],
)
def test_anything_off_grammar_is_ignored(raw: str | None) -> None:
    assert parse_copy_intent(raw) is None
