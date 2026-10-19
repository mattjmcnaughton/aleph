"""The SSE frame payloads as named contract schemas (iOS TDD §4.1).

The tutor and shaping send routes answer ``200 text/event-stream`` (``services/
sse.py`` frames them): each frame is ``event: <name>`` plus one JSON ``data:``
line whose shape depends on the name. FastAPI cannot see those payloads, because
the body is a ``StreamingResponse``. So each send route declares them twice, as
metadata only:

- ``response_model=`` the union of its frames, which is what makes FastAPI
  register each frame as a component. It has no runtime effect: an endpoint that
  returns a ``Response`` is sent as-is, never validated or serialised through
  ``response_model``. With ``StreamingResponse`` (no media type), FastAPI emits no
  JSON body schema for it either.
- ``responses=`` :func:`event_stream_responses`: the ``200 text/event-stream``
  body, a ``oneOf`` of ``$ref`` s to those components.

Both clients then generate the frame types from ``clients/openapi.json``.

Each frame is a thin subclass of the DTO the services already emit. It adds no
field, so the wire shape is identical by construction, and only the schema name
changes to the one the TDD fixes. The services keep emitting the base DTOs.

| ``event:``    | Schema             | Emitted as            | Routes           |
| ------------- | ------------------ | --------------------- | ---------------- |
| ``delta``      | ``StreamDelta``      | ``MessageDeltaDTO``    | tutor, shaping   |
| ``tutor_check`` | ``StreamTutorCheck`` | ``TutorCheckDTO``      | tutor            |
| ``proposal``   | ``StreamProposal``   | ``ProposalPayloadDTO`` | shaping          |
| ``done``       | ``StreamDone``       | ``MessageDoneDTO``     | tutor, shaping   |
| ``error``      | ``StreamError``      | ``MessageErrorDTO``    | tutor, shaping   |
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

from aleph.dtos.shaping import ProposalPayloadDTO
from aleph.dtos.tutor import (
    MessageDeltaDTO,
    MessageDoneDTO,
    MessageErrorDTO,
    TutorCheckDTO,
)

if TYPE_CHECKING:
    from types import UnionType


class StreamDelta(MessageDeltaDTO):
    """``event: delta``: one streamed fragment of the reply's Markdown."""


class StreamTutorCheck(TutorCheckDTO):
    """``event: tutor_check``: the Tutor check card, attached to the reply."""


class StreamProposal(ProposalPayloadDTO):
    """``event: proposal``: the validated Proposal the shaping reply offers."""


class StreamDone(MessageDoneDTO):
    """``event: done``: terminal success. The turn is persisted."""


class StreamError(MessageErrorDTO):
    """``event: error``: terminal failure. Nothing is persisted."""


#: The tutor send route's frames: its ``response_model`` and its ``oneOf``.
TutorStreamFrame = StreamDelta | StreamTutorCheck | StreamDone | StreamError

#: The shaping send route's frames: its ``response_model`` and its ``oneOf``.
ShapingStreamFrame = StreamDelta | StreamProposal | StreamDone | StreamError


def event_stream_responses(frames: UnionType, description: str) -> dict[int | str, Any]:
    """The ``responses=`` metadata for a send route: ``200 text/event-stream``.

    The schema is a ``oneOf`` over the route's frame payloads (``frames``, the
    same union the route passes as ``response_model``). It documents the
    ``data:`` line of each frame; the ``event:`` name picks which. Generators
    treat an event-stream body as raw bytes, so this names the frame types
    rather than decoding them.
    """
    return {
        200: {
            "description": description,
            "content": {
                "text/event-stream": {
                    "schema": {
                        "oneOf": [
                            {"$ref": f"#/components/schemas/{frame.__name__}"}
                            for frame in get_args(frames)
                        ]
                    }
                }
            },
        }
    }
