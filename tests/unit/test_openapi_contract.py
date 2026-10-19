"""The committed OpenAPI contract (iOS TDD §4.1, D7).

``clients/openapi.json`` is what both clients generate from: the web's
``schema.d.ts`` (openapi-typescript) and, from AL-602, the iOS app's Swift client.
These tests pin the two contract corrections that land with the export, so a
generated client can type every body it reads:

- the tutor and shaping send routes declare ``200`` as ``text/event-stream``, with
  the frame payloads as named component schemas;
- every ``/api/v1`` error response references the shared error envelope.

They also pin the export itself: ``/api/v1`` only, the components those paths
reference and nothing else, and byte-stable output.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from aleph.app import create_app
from aleph.dtos.shaping import ProposalPayloadDTO
from aleph.dtos.stream import (
    StreamDelta,
    StreamDone,
    StreamError,
    StreamProposal,
    StreamTutorCheck,
)
from aleph.dtos.tutor import (
    MessageDeltaDTO,
    MessageDoneDTO,
    MessageErrorDTO,
    TutorCheckDTO,
)
from aleph.errors import ErrorEnvelope, error_response
from scripts import export_openapi

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel

REF = "#/components/schemas/"
TUTOR_SEND = "/api/v1/paths/{path_id}/conversation/messages"
SHAPING_SEND = "/api/v1/paths/{path_id}/shaping/conversation/messages"


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return create_app().openapi()


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return export_openapi.build_contract(create_app())


def _refs(node: Any) -> set[str]:
    """Every component name referenced anywhere under ``node``."""
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(REF):
            found.add(ref.removeprefix(REF))
        for value in node.values():
            found |= _refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs(value)
    return found


# --- the SSE frames -----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "frames"),
    [
        (TUTOR_SEND, {"StreamDelta", "StreamTutorCheck", "StreamDone", "StreamError"}),
        (SHAPING_SEND, {"StreamDelta", "StreamProposal", "StreamDone", "StreamError"}),
    ],
)
def test_send_routes_declare_event_stream_with_their_frames(
    document: dict[str, Any], path: str, frames: set[str]
) -> None:
    ok = document["paths"][path]["post"]["responses"]["200"]
    assert set(ok["content"]) == {"text/event-stream"}
    schema = ok["content"]["text/event-stream"]["schema"]
    assert {ref["$ref"].removeprefix(REF) for ref in schema["oneOf"]} == frames


def test_the_five_frame_components_exist(document: dict[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    for name in (
        "StreamDelta",
        "StreamTutorCheck",
        "StreamProposal",
        "StreamDone",
        "StreamError",
    ):
        assert name in schemas, name
        assert schemas[name]["title"] == name


@pytest.mark.parametrize(
    ("frame", "emitted"),
    [
        (StreamDelta, MessageDeltaDTO),
        (StreamTutorCheck, TutorCheckDTO),
        (StreamProposal, ProposalPayloadDTO),
        (StreamDone, MessageDoneDTO),
        (StreamError, MessageErrorDTO),
    ],
)
def test_frame_schemas_are_the_shapes_the_services_emit(
    frame: type[BaseModel], emitted: type[BaseModel]
) -> None:
    """The services emit the base DTOs; the named frames must be the same wire shape.

    Only the schema title differs, so a generated client decodes exactly the
    bytes ``services/sse.py`` frames.
    """
    assert issubclass(frame, emitted)
    assert frame.model_fields.keys() == emitted.model_fields.keys()
    frame_schema = frame.model_json_schema(mode="serialization")
    emitted_schema = emitted.model_json_schema(mode="serialization")
    frame_schema.pop("title")
    emitted_schema.pop("title")
    frame_schema.pop("description", None)
    emitted_schema.pop("description", None)
    assert frame_schema == emitted_schema


# --- the error envelope ---------------------------------------------------------


def test_every_api_v1_operation_references_the_error_envelope(
    document: dict[str, Any],
) -> None:
    for path, item in document["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method, operation in item.items():
            default = operation["responses"].get("default")
            assert default is not None, f"{method.upper()} {path}"
            schema = default["content"]["application/json"]["schema"]
            assert schema == {"$ref": REF + "ErrorEnvelope"}, f"{method.upper()} {path}"


def test_fastapis_validation_error_schema_is_gone(document: dict[str, Any]) -> None:
    """The real 422 body is the envelope (``app.py``), never ``HTTPValidationError``."""
    schemas = document["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert "ErrorEnvelope" in schemas


def _envelope_app() -> FastAPI:
    app = create_app()

    @app.get("/__test__/conflict")
    async def conflict() -> None:
        raise HTTPException(status_code=409, detail={"message": "busy", "reason": "x"})

    @app.get("/__test__/validation")
    async def validation(n: int) -> int:
        return n

    return app


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("/__test__/conflict", 409),
        ("/__test__/validation?n=nope", 422),
        ("/api/v1/paths", 401),
        ("/api/v1/nonexistent", 404),
    ],
)
def test_real_error_bodies_validate_against_the_envelope(url: str, status: int) -> None:
    client = TestClient(_envelope_app())
    response = client.get(url)
    assert response.status_code == status
    envelope = ErrorEnvelope.model_validate(response.json())
    assert envelope.error.code
    assert envelope.error.message


def test_error_response_omits_details_when_absent() -> None:
    request = Request({"type": "http", "state": {"request_id": "rid"}})
    response = error_response(
        request,
        status_code=404,
        code="not_found",
        message="no",
    )
    body = json.loads(bytes(response.body))
    assert body == {
        "error": {"code": "not_found", "message": "no", "request_id": "rid"}
    }
    ErrorEnvelope.model_validate(body)


# --- the export -------------------------------------------------------------------


def test_export_keeps_only_api_v1_paths(contract: dict[str, Any]) -> None:
    assert contract["paths"]
    assert all(path.startswith("/api/v1/") for path in contract["paths"])


def test_export_keeps_exactly_the_referenced_components(
    contract: dict[str, Any],
) -> None:
    schemas = contract["components"]["schemas"]
    reachable: set[str] = set()
    frontier = _refs(contract["paths"])
    while frontier:
        name = frontier.pop()
        if name in reachable:
            continue
        reachable.add(name)
        frontier |= _refs(schemas[name]) - reachable
    assert set(schemas) == reachable
    assert {"StreamDelta", "StreamProposal", "ErrorEnvelope"} <= set(schemas)


def test_export_output_is_stable_and_formatted(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    export_openapi.main(["--output", str(first)])
    export_openapi.main(["--output", str(second)])
    text = first.read_text()
    assert text == second.read_text()
    assert text.endswith("}\n")
    reformatted = json.dumps(
        json.loads(text), indent=2, sort_keys=True, ensure_ascii=False
    )
    assert text == reformatted + "\n"
