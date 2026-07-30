"""Unit tests for the SSE wire encoding (AL-220, Phase 2 TDD §5.4).

The transport is the phase's headline risk, and framing is the part of it that
can be pinned without a server: a named event is ``event: <name>`` + one
``data:`` line + a blank line, a heartbeat is a comment frame, and *nothing* the
model produces may break either shape. The last one is the whole reason this
module exists rather than f-strings at the call site — a reply is arbitrary
Markdown, and a raw newline inside a ``data:`` line silently truncates the event
on the client.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from aleph.services.sse import (
    SSE_HEADERS,
    SSE_MEDIA_TYPE,
    sse_event,
    sse_heartbeat,
)


class _Payload(BaseModel):
    text: str


def test_named_event_is_one_event_line_one_data_line_and_a_blank_line() -> None:
    frame = sse_event("delta", _Payload(text="hello"))

    assert frame == 'event: delta\ndata: {"text":"hello"}\n\n'


def test_data_is_json_so_a_multiline_reply_stays_one_frame() -> None:
    """A Markdown reply is full of newlines; a frame must survive them.

    JSON string escaping is what makes ``data:`` a single line, so the frame
    boundary (the blank line) can only come from the encoder. Without this the
    first paragraph break in a tutor reply would end the event early and the
    rest would parse as garbage.
    """
    reply = "First paragraph.\n\n- bullet\n- bullet\n"

    frame = sse_event("delta", _Payload(text=reply))

    lines = frame.split("\n")
    assert lines[0] == "event: delta"
    assert lines[1].startswith("data: ")
    # Exactly one data line, then the frame terminator (two trailing newlines
    # render as two empty trailing entries).
    assert lines[2:] == ["", ""]
    assert json.loads(lines[1].removeprefix("data: "))["text"] == reply


def test_carriage_returns_do_not_split_a_frame_either() -> None:
    """CR and CRLF terminate SSE lines too, and JSON escapes them as well."""
    frame = sse_event("delta", _Payload(text="a\rb\r\nc"))

    assert frame.count("\n") == 3  # event line, data line, blank-line terminator
    assert "\r" not in frame


def test_heartbeat_is_a_comment_frame() -> None:
    """``: ping`` — a comment, so a client parser ignores it (§5.4)."""
    assert sse_heartbeat() == ": ping\n\n"


@pytest.mark.parametrize("name", ["delta", "tutor_check", "done", "error"])
def test_every_wire_event_name_encodes(name: str) -> None:
    assert sse_event(name, _Payload(text="x")).startswith(f"event: {name}\n")


def test_headers_declare_the_media_type_and_forbid_caching() -> None:
    """``Cache-Control: no-store`` is §5.4's; the rest is §12's proxy chain."""
    assert SSE_MEDIA_TYPE == "text/event-stream"
    assert SSE_HEADERS["Cache-Control"] == "no-store"
    assert SSE_HEADERS["X-Accel-Buffering"] == "no"
