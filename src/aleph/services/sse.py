"""Server-sent-events wire encoding (AL-220, Phase 2 TDD §5.4/D1).

The codebase's first streaming transport, kept as small as the TDD insists: four
named events, one heartbeat comment, and the response headers that carry them
intact through the proxy chain. Everything here is pure string work over a
Pydantic payload — no I/O, no framework — so the framing that the rail's parser
(AL-230) depends on is pinned by unit tests rather than discovered in a browser.

**Why an encoder and not an f-string at the call site.** A ``data:`` line ends
at the first ``\\n``, ``\\r`` or ``\\r\\n``, and a tutor reply is arbitrary
Markdown — paragraph breaks, bullet lists, fenced code. Serialising the payload
as JSON is what keeps a frame one line: JSON escapes every line terminator, so
the blank-line frame boundary can only ever come from this module. (The
alternative — splitting a multi-line payload across repeated ``data:`` lines,
which the SSE spec also allows — would work, but re-joining it is the client's
problem and JSON is already the wire format for every field.)

The event vocabulary itself lives in ``dtos/tutor.py`` (the payload shapes) and
``services/tutor.py`` (which emits them); this module knows only how to frame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

# The response's media type and the headers §5.4/§12 require alongside it.
SSE_MEDIA_TYPE = "text/event-stream"

# ``Cache-Control: no-store`` is the TDD's (§5.4): a reply is one learner's,
# once, and an intermediary that cached it would be serving someone else's turn.
# ``X-Accel-Buffering: no`` addresses §12's actual operational risk — a
# buffering reverse proxy holding the whole reply until the stream ends, which
# turns progressive rendering back into blocking JSON without any error to show
# for it. It is the one header nginx-family proxies honour for this, costs
# nothing where it is not understood, and `compose-smoke` checks the transport
# end to end through the production image so a regression is caught before
# deploy.
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
}


def sse_event(name: str, data: BaseModel) -> str:
    """One named SSE frame: ``event: <name>`` + a JSON ``data:`` line.

    ``data`` is serialised with ``model_dump_json`` so UUIDs and datetimes take
    the same wire shape they have on every other endpoint, and so the payload
    can never break the frame (see the module docstring).
    """
    return f"event: {name}\ndata: {data.model_dump_json()}\n\n"


def sse_heartbeat() -> str:
    """The ``: ping`` keep-alive sent during model silence (§5.4).

    A comment frame — a leading ``:`` — so every conforming client parser
    ignores it. Its whole job is to put bytes on the socket so an idle-timeout
    in the proxy chain never kills a stream that is merely waiting on a slow
    first token.
    """
    return ": ping\n\n"
