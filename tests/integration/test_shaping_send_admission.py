"""Pre-stream admission for the shaping send endpoint (AL-320, TDD §5.5/§6).

Everything the shaping send endpoint can still refuse as an **ordinary JSON
envelope**, because SSE starts only once the turn is admitted: the ``shaping``
flag gate, ownership, the path's status, auth, the daily cap, the in-flight
conflict, and the per-message model picker. The rule under test in every case is
the same one — a pre-stream failure must not open a stream and must not write a
row.

Stream behaviour once a turn *is* admitted lives in ``test_shaping_send``; the
shared harness is ``_shaping_send_harness``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.models.function import FunctionModel

from aleph import db
from aleph.config import settings
from aleph.models import Conversation, Message, Path, PathStatus
from aleph.services import tutor as tutor_service
from aleph.services.stub_model import build_stub_model

from ._shaping_send_harness import (
    ADMIN,
    ASK,
    OTHER,
    OWNER,
    _body,
    _client,
    _conversation_url,
    _json_error,
    _seed_lesson_turn,
    _seed_path,
    _seed_shaping_turn,
    _send,
    _send_url,
    _sign_in,
    _thread,
    _use_model,
)
from ._shaping_send_harness import (
    app as app,  # noqa: PLC0414 - re-exported so the fixture resolves here
)
from ._shaping_send_harness import (
    isolated_shaping_limiter as isolated_shaping_limiter,  # noqa: PLC0414
)
from ._shaping_send_harness import (
    stub_shaping_model as stub_shaping_model,  # noqa: PLC0414
)

# The **2A** rail's own harness, for the one test that has to send an in-lesson
# turn (the W21 direction of §7's separate budgets). Aliased rather than
# shadowing this suite's shaping helpers, so a call site always says which rail
# it is on; its two autouse fixtures are re-exported for the same reason 2A's
# suites re-export them — a tutor send with no stub would reach a real provider.
from ._tutor_send_harness import (
    _body as _tutor_body,
)
from ._tutor_send_harness import (
    _count,
)
from ._tutor_send_harness import (
    _seed_path as _seed_tutor_path,
)
from ._tutor_send_harness import (
    _send_url as _tutor_send_url,
)
from ._tutor_send_harness import (
    isolated_reply_limiter as isolated_reply_limiter,  # noqa: PLC0414
)
from ._tutor_send_harness import (
    stub_tutor_model as stub_tutor_model,  # noqa: PLC0414
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from aleph.services.lifecycle import TutorReplyLimiter


# --------------------------------------------------------------------------- #
# The flag gate (epic #114, adopted convention 1)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_off_hides_the_send_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The router-level gate covers the streamed route too.

    ``404`` (never ``403``), *before* the stream opens, and with nothing written
    — the request is otherwise entirely valid, so the gate is the only thing
    standing between it and a turn.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        response = await _json_error(client, _send_url(path_id), _body())

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert await _count(Message) == 0
    assert await _count(Conversation) == 0


@pytest.mark.anyio
async def test_flag_off_hides_the_conversation_routes(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read and clear are gated by the same router-level dependency."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        read = await client.get(_conversation_url(path_id))
        cleared = await client.delete(_conversation_url(path_id))

    assert read.status_code == 404, read.text
    assert cleared.status_code == 404, cleared.text
    assert read.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_the_tutor_flag_does_not_open_the_shaping_surface(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Two flags, independently thrown (epic #114): ``tutor`` on is not consent.

    The in-lesson tutor is already launched; shaping must still be able to ship
    dark behind its own key, and be killed on its own.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        response = await _json_error(client, _send_url(path_id), _body())

    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- #
# Pre-stream validation (§5.5, §6)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [
        PathStatus.PENDING,
        PathStatus.GENERATING,
        PathStatus.FAILED,
        PathStatus.REFUSED,
    ],
)
async def test_a_path_that_is_not_ready_is_409(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    status: PathStatus,
) -> None:
    """No structure, nothing to shape (PRD §5.1) — and it is a *plain* 409.

    A pre-stream refusal, so an ordinary JSON error envelope rather than a
    stream that opens and immediately errors: the rail's fetch error handler is
    what reads this, and the entry it hides on a non-``ready`` path is the
    convenience, not the rule.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id, status=status)

        response = await _json_error(client, _send_url(path_id), _body())

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"
    assert await _count(Message) == 0
    assert await _count(Conversation) == 0


@pytest.mark.anyio
async def test_a_non_ready_path_does_not_lock_its_conversation(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    isolated_shaping_limiter: TutorReplyLimiter,
) -> None:
    """The status check runs *before* the reservation is claimed (§5.5's order).

    A refused send that had already claimed the thread would wedge it on a
    permanent ``409`` until the process restarted.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id, status=PathStatus.PENDING)

        await _json_error(client, _send_url(path_id), _body())

    assert isolated_shaping_limiter.in_flight == frozenset()


@pytest.mark.anyio
async def test_another_learners_path_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Ownership failures never disclose existence (404, never 403)."""
    async with _client(app) as owner_client:
        owner_id = await _sign_in(owner_client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(owner_id)

    async with _client(app) as other_client:
        await _sign_in(other_client, monkeypatch, OTHER)
        response = await _json_error(other_client, _send_url(path_id), _body())

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_an_unknown_path_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        response = await _json_error(client, _send_url(uuid.uuid4()), _body())

    assert response.status_code == 404, response.text


@pytest.mark.anyio
async def test_oversize_content_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """2A's 2000-character bound, reused verbatim and enforced pre-stream."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        response = await _json_error(
            client, _send_url(path_id), _body(content="x" * 2001)
        )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_a_lesson_id_is_not_part_of_this_contract(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Shaping is path-level: an extra ``lesson_id`` is ignored, never recorded.

    The 2A body shape must not accidentally work here — a turn that quietly
    tagged itself with a lesson would make the two threads look alike in the
    record and would be the first step towards the rails sharing one.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)

        wire = await _send(client, _send_url(path_id), _body(lesson_id=str(lessons[0])))

    assert wire.names[-1] == "done", wire.names
    assert [message.lesson_id for message in await _thread(path_id)] == [None, None]


@pytest.mark.anyio
async def test_anonymous_send_is_401(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.post(_send_url(uuid.uuid4()), json=_body())

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.anyio
async def test_rate_limited_send_is_429_pre_stream(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """With the cap raised off its default of 0, the check runs pre-stream (§7)."""
    monkeypatch.setattr(settings, "rate_limit_shaping_messages_per_day", 1)
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        await _seed_shaping_turn(path_id=path_id)

        response = await _json_error(client, _send_url(path_id), _body())

    assert response.status_code == 429, response.text
    assert response.json()["error"]["code"] == "rate_limited"
    assert await _count(Message) == 2, "nothing new was written"


@pytest.mark.anyio
async def test_the_shaping_cap_does_not_count_in_lesson_turns(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Separate budgets (§7): an in-lesson thread must not close the shaping rail."""
    monkeypatch.setattr(settings, "rate_limit_shaping_messages_per_day", 1)
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)
        await _seed_lesson_turn(path_id=path_id, lesson_id=lessons[0])

        wire = await _send(client, _send_url(path_id), _body())

    assert wire.names[-1] == "done", wire.names


@pytest.mark.anyio
@pytest.mark.workflow("W21")
async def test_the_tutor_cap_does_not_count_shaping_turns(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """The other direction of §7's separate budgets — and 2B's one edit to 2A.

    ``UsageRepository.count_tutor_messages_since`` grew a ``kind='lesson'``
    filter this phase: without it a shaping ask would quietly spend the *tutor's*
    daily budget, which is a change to what 2A's cap counts made by the phase
    that promised not to change 2A (W21). A fake-backed unit test can only pin
    the argument; this pins the **SQL**, against real Postgres and through the
    real route.

    Both directions in one arrange: a shaping turn is on the path and the tutor
    cap is 1, so the in-lesson send must stream (the shaping rows did not count)
    — and the *second* one must be refused (the in-lesson rows did).
    """
    monkeypatch.setattr(settings, "rate_limit_tutor_messages_per_day", 1)
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_tutor_path(user_id)
        await _seed_shaping_turn(path_id=path_id)

        wire = await _send(client, _tutor_send_url(path_id), _tutor_body(lesson_id))
        spent = await _json_error(
            client, _tutor_send_url(path_id), _tutor_body(lesson_id)
        )

    assert wire.status_code == 200, wire.body
    assert wire.names[-1] == "done", wire.names
    assert spent.status_code == 429, spent.text
    assert spent.json()["error"]["code"] == "rate_limited"


@pytest.mark.anyio
async def test_a_second_send_while_one_is_in_flight_is_409(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """One reply at a time per shaping conversation (D11), refused pre-stream.

    The first send is held open by a model that waits on an event; the second
    arrives while it is still running and must be an ordinary ``409`` rather
    than a second stream computing the same ``max(position)``.
    """
    release = asyncio.Event()
    started = asyncio.Event()

    async def blocked(_messages: object, _info: object):  # noqa: ANN202 - test double
        started.set()
        yield "Thinking"
        await release.wait()
        yield " — and here is the answer."

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        _use_model(monkeypatch, FunctionModel(stream_function=blocked))

        first = asyncio.create_task(_send(client, _send_url(path_id), _body()))
        await asyncio.wait_for(started.wait(), timeout=5)

        second = await _json_error(client, _send_url(path_id), _body(content="again"))
        release.set()
        wire = await first

    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "conflict"
    assert wire.names[-1] == "done", wire.names
    assert len(await _thread(path_id)) == 2, "only the admitted turn was written"


@pytest.mark.anyio
async def test_an_in_flight_shaping_reply_does_not_block_the_in_lesson_rail(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Two rails, two locks (D11) — and W21: 2A's thread is not made busy here.

    Asserted at the limiter, which is where the rule lives: the shaping claim is
    keyed in the shaping service's own registry, so the tutor's is untouched.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        wire = await _send(client, _send_url(path_id), _body())

    assert wire.names[-1] == "done", wire.names
    assert tutor_service.tutor_turn_service.replies.in_flight == frozenset()


# --------------------------------------------------------------------------- #
# The per-message model override (§5.3 / D10) — 2A's matrix, shaper slot
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_default_reply_routes_the_configured_shaper_slot(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        routed = _use_model(monkeypatch, build_stub_model())

        wire = await _send(client, _send_url(path_id), _body())

    assert wire.names[-1] == "done", wire.names
    assert routed == [settings.model_shaper]


@pytest.mark.anyio
async def test_admin_model_override_routes_the_reply_and_persists_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chosen id reaches the model call and is written down nowhere."""
    override = "anthropic/claude-haiku-4-5"
    assert override in settings.allowlist_ids

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, ADMIN)
        path_id, _lessons = await _seed_path(user_id)
        routed = _use_model(monkeypatch, build_stub_model())

        wire = await _send(client, _send_url(path_id), _body(model=override))

    assert wire.names[-1] == "done", wire.names
    assert routed == [override]
    # A reply is request-scoped: there is nothing to resume and no column to add.
    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        assert path is not None
        assert path.model_outline is None
        assert path.model_lesson is None
    assert not any(
        override in (message.content or "") for message in await _thread(path_id)
    )


@pytest.mark.anyio
async def test_non_admin_override_is_403_before_the_allowlist(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """403 first, so a non-admin never learns the allowlist's shape."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        response = await _json_error(
            client, _send_url(path_id), _body(model="not/a-real-model")
        )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    assert "not/a-real-model" not in response.text
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_off_allowlist_override_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, ADMIN)
        path_id, _lessons = await _seed_path(user_id)

        response = await _json_error(
            client, _send_url(path_id), _body(model="evil/model")
        )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_the_picker_is_gated_before_the_path_status_check(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """The order §6 fixes: no billed work is even considered before the picker.

    ``ASK`` against a ``pending`` path with a non-admin override is refusable
    twice over; ``403`` is the answer, because who may spend money on which
    model is decided before anything about the path is.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id, status=PathStatus.PENDING)

        response = await _json_error(
            client, _send_url(path_id), _body(content=ASK, model="not/a-real-model")
        )

    assert response.status_code == 403, response.text
