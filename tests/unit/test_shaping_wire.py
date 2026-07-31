"""Unit tests for the shaping wire contract (AL-320, Phase 2B TDD §5.4/§6).

Everything about the shaping surface that can be pinned without a server or a
database: how the one new SSE event frames, what its data is (and is not), the
two proposal DTO shapes, and the D11 wiring that makes shaping share the tutor's
concurrency pool while keeping its own per-conversation lock.

The framing rules themselves are 2A's and are tested once, in ``test_sse``; what
is asserted here is the *shaping* half — the event's name (AL-330 matches the
literal string), the bare payload the frontend contract fixes, and the fact that
a validated Proposal survives the round trip through JSON unchanged, since the
frame the rail draws its card from and the row a later thread read returns are
the same object.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from aleph.agents.shaper import ProposalResolution
from aleph.dtos.shaping import (
    ProposalDTO,
    ProposalPayloadDTO,
    ProposalResolutionDTO,
    SendShapingMessageRequest,
)
from aleph.services.lifecycle import TutorReplyLimiter
from aleph.services.shaping import PROPOSAL_EVENT, shaping_turn_service
from aleph.services.sse import sse_event
from aleph.services.tutor import tutor_turn_service

ADDITION: dict[str, Any] = {
    "insert_at_position": 3,
    "lessons": [{"title": "Lifetimes, gently"}, {"title": "Borrowing in practice"}],
    "rationale": "The path jumps from moves straight to generics.",
    "estimated_minutes": 10,
    "new_unit": None,
}
REVISION: dict[str, Any] = {
    "lesson_id": "0f7f4e0e-6b1c-4a1e-9a2e-3b6f9d5c8a11",
    "instruction": "Slow it down and work one example all the way through.",
    "rationale": "You have not started this one yet.",
    "new_title": None,
}


def _payload(*operations: dict[str, Any], summary: str = "Adds 2 lessons.") -> Any:
    return {"operations": list(operations), "summary": summary}


# --------------------------------------------------------------------------- #
# The ``proposal`` event (§5.4's one addition)
# --------------------------------------------------------------------------- #


def test_the_event_name_is_literally_proposal() -> None:
    """AL-330's stream parser matches this string; it is the contract."""
    assert PROPOSAL_EVENT == "proposal"


def test_a_proposal_frames_as_one_event_line_and_one_data_line() -> None:
    card = ProposalPayloadDTO.model_validate(_payload(ADDITION))

    frame = sse_event(PROPOSAL_EVENT, card)

    lines = frame.split("\n")
    assert lines[0] == "event: proposal"
    assert lines[1].startswith("data: ")
    assert lines[2:] == ["", ""]


def test_the_frame_carries_the_bare_validated_payload() -> None:
    """``{operations, summary}`` and nothing else (the AL-330 contract).

    No ``resolution``: a Proposal that has just been made is pending by
    definition, and a field with one possible value is noise the client would
    have to decide whether to trust.
    """
    card = ProposalPayloadDTO.model_validate(_payload(ADDITION, REVISION))

    data = json.loads(sse_event(PROPOSAL_EVENT, card).split("\n")[1][len("data: ") :])

    assert set(data) == {"operations", "summary"}
    assert data == _payload(ADDITION, REVISION)


def test_a_summary_full_of_markdown_cannot_break_the_frame() -> None:
    """A summary is model-written prose; JSON escaping is what keeps it one line."""
    summary = "Adds two lessons:\n\n- one on lifetimes\n- one on borrowing\n"
    card = ProposalPayloadDTO.model_validate(_payload(ADDITION, summary=summary))

    frame = sse_event(PROPOSAL_EVENT, card)

    assert frame.count("\n") == 3
    data = json.loads(frame.split("\n")[1][len("data: ") :])
    assert data["summary"] == summary


def test_both_operation_shapes_survive_the_round_trip() -> None:
    """The wire shape is the agent's own models, so a payload is not re-spelled.

    Discrimination is structural (an Addition carries ``lessons``, a Revision
    carries ``lesson_id``), which is how the stub builds them and how apply will
    dispatch on them.
    """
    card = ProposalPayloadDTO.model_validate(_payload(ADDITION, REVISION))

    assert card.model_dump(mode="json") == _payload(ADDITION, REVISION)


def test_an_out_of_vocabulary_operation_is_rejected() -> None:
    """The vocabulary is closed (D1): a third shape never reaches the wire."""
    with pytest.raises(ValidationError):
        ProposalPayloadDTO.model_validate(
            _payload({"remove_lesson": {"lesson_id": "x"}})
        )


# --------------------------------------------------------------------------- #
# The conversation read's shape (§6)
# --------------------------------------------------------------------------- #


def test_the_read_shape_adds_the_derived_resolution() -> None:
    dto = ProposalDTO.model_validate({**_payload(ADDITION), "resolution": "superseded"})

    assert dto.resolution is ProposalResolutionDTO.SUPERSEDED
    assert dto.model_dump(mode="json")["resolution"] == "superseded"


def test_the_resolution_vocabulary_is_the_agents_own() -> None:
    """One spelling from the derivation to the card (D3)."""
    assert {member.value for member in ProposalResolutionDTO} == set(
        get_args(ProposalResolution)
    )


def test_an_unknown_resolution_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProposalDTO.model_validate({**_payload(ADDITION), "resolution": "maybe"})


# --------------------------------------------------------------------------- #
# The send request (§6)
# --------------------------------------------------------------------------- #


def test_the_send_body_has_no_lesson_and_defaults_to_typed() -> None:
    """Shaping is path-level: there is no lesson a turn is asked in."""
    request = SendShapingMessageRequest.model_validate({"content": "  add a lesson  "})

    assert request.content == "add a lesson"
    assert request.source.value == "typed"
    assert request.model is None
    assert "lesson_id" not in request.model_dump()


@pytest.mark.parametrize("content", ["", "   ", "x" * 2001])
def test_the_send_body_bounds_the_learner_message(content: str) -> None:
    """2A's ``TutorMessageStr``, reused rather than re-declared."""
    with pytest.raises(ValidationError):
        SendShapingMessageRequest.model_validate({"content": content})


# --------------------------------------------------------------------------- #
# The D11 wiring: one pool, two locks
# --------------------------------------------------------------------------- #


def test_shaping_shares_the_tutors_semaphore() -> None:
    """Both reply kinds are one workload class, so they queue against one bound.

    Asserted on the module singletons, because "shared" is a property of the
    process-wide wiring rather than of either class — splitting the pool would
    let a shaping burst and a tutor burst starve each other rather than queue
    together.
    """
    assert shaping_turn_service.replies.slot() is tutor_turn_service.replies.slot()


def test_the_two_rails_keep_separate_in_flight_registries() -> None:
    """A shaping reply must never make the in-lesson thread read as busy (W21)."""
    assert shaping_turn_service.replies is not tutor_turn_service.replies

    path_id = uuid.uuid4()
    reservation = shaping_turn_service.replies.reserve(path_id)
    try:
        assert shaping_turn_service.replies.in_flight == frozenset({path_id})
        assert tutor_turn_service.replies.in_flight == frozenset()
    finally:
        shaping_turn_service.replies.release(reservation)


def test_a_limiter_needs_a_bound() -> None:
    """Neither a size nor a shared semaphore is an unbounded fan-out."""
    with pytest.raises(ValueError, match="max_concurrent"):
        TutorReplyLimiter()


def test_a_limiter_refuses_two_bounds() -> None:
    """Exactly one, so "which one won?" is never a question worth asking.

    Silently preferring the shared semaphore would make ``max_concurrent=8``
    read as an enforced bound while the caller queued against somebody else's
    pool — the D11 mistake that is hardest to see, because both spellings look
    correct at the call site and the wrong one only shows up as a load figure.
    """
    with pytest.raises(ValueError, match="not both"):
        TutorReplyLimiter(max_concurrent=8, semaphore=asyncio.Semaphore(2))
