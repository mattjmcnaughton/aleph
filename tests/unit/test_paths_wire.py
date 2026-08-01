"""Unit tests for the Path title / Guidance wire contract (CONTEXT.md).

Everything about the two new fields — ``title`` (display) and ``guidance``
(generation input) — that can be pinned without a server or a database:
``CreatePathRequest``'s optional, stripped ``guidance``; ``UpdatePathRequest``'s
single required, bounded ``title``; and ``Path.display_title``'s topic
fallback. No network, no Postgres.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aleph.dtos.paths import CreatePathRequest, UpdatePathRequest
from aleph.models import Level
from aleph.models.path import Path

# --------------------------------------------------------------------------- #
# CreatePathRequest.guidance: optional, stripped, bounded
# --------------------------------------------------------------------------- #


def test_create_path_request_accepts_absent_guidance() -> None:
    body = CreatePathRequest(topic="Rust ownership", level=Level.SOME_EXPERIENCE)
    assert body.guidance is None


def test_create_path_request_strips_guidance_whitespace() -> None:
    body = CreatePathRequest(
        topic="Rust ownership",
        level=Level.SOME_EXPERIENCE,
        guidance="  focus on borrowing  ",
    )
    assert body.guidance == "focus on borrowing"


def test_create_path_request_rejects_blank_guidance() -> None:
    # A present-but-whitespace-only guidance is a validation error, not silently
    # coerced to None — the caller said something, it just was not real content.
    with pytest.raises(ValidationError):
        CreatePathRequest(
            topic="Rust ownership", level=Level.SOME_EXPERIENCE, guidance="   "
        )


def test_create_path_request_rejects_overlong_guidance() -> None:
    with pytest.raises(ValidationError):
        CreatePathRequest(
            topic="Rust ownership",
            level=Level.SOME_EXPERIENCE,
            guidance="x" * 4001,
        )


def test_create_path_request_accepts_guidance_at_the_bound() -> None:
    body = CreatePathRequest(
        topic="Rust ownership", level=Level.SOME_EXPERIENCE, guidance="x" * 4000
    )
    assert body.guidance == "x" * 4000


# --------------------------------------------------------------------------- #
# UpdatePathRequest.title: the ONLY field, required, stripped, bounded
# --------------------------------------------------------------------------- #


def test_update_path_request_has_exactly_one_field() -> None:
    # The whole point (docstring): a single-field body is what makes "topic is
    # immutable" a fact about the wire contract, not a review habit.
    assert set(UpdatePathRequest.model_fields) == {"title"}


def test_update_path_request_strips_title_whitespace() -> None:
    assert UpdatePathRequest(title="  Rust, the fun parts  ").title == (
        "Rust, the fun parts"
    )


def test_update_path_request_rejects_blank_title() -> None:
    with pytest.raises(ValidationError):
        UpdatePathRequest(title="   ")


def test_update_path_request_rejects_missing_title() -> None:
    with pytest.raises(ValidationError):
        UpdatePathRequest()  # ty: ignore[missing-argument]


def test_update_path_request_rejects_overlong_title() -> None:
    with pytest.raises(ValidationError):
        UpdatePathRequest(title="x" * 201)


def test_update_path_request_accepts_title_at_the_bound() -> None:
    assert UpdatePathRequest(title="x" * 200).title == "x" * 200


# --------------------------------------------------------------------------- #
# Path.display_title: the topic fallback
# --------------------------------------------------------------------------- #


def test_display_title_falls_back_to_topic_when_unset() -> None:
    path = Path(topic="Rust ownership", title=None)
    assert path.display_title == "Rust ownership"


def test_display_title_prefers_the_set_title() -> None:
    path = Path(topic="Rust ownership", title="Rust, the fun parts")
    assert path.display_title == "Rust, the fun parts"
