"""Pre-stream admission for the send endpoint (AL-220, Phase 2 TDD §5.5/§6).

Everything the send endpoint can still refuse as an **ordinary JSON envelope**,
because SSE starts only once the turn is admitted: the ``tutor`` flag gate, the
lesson/ownership/size checks, auth, the daily cap, and the per-message model
picker. The rule under test in every case is the same one — a pre-stream failure
must not open a stream and must not write a row.

Stream behaviour once a turn *is* admitted lives in ``test_tutor_send``; the
shared harness is ``_tutor_send_harness``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from aleph import db
from aleph.config import settings
from aleph.models import Conversation, Message, Path
from aleph.services.stub_model import build_stub_model

from ._tutor_send_harness import (
    ADMIN,
    OTHER,
    OWNER,
    _body,
    _client,
    _count,
    _json_error,
    _seed_path,
    _seed_turn,
    _send,
    _send_url,
    _sign_in,
    _thread,
    _use_model,
)
from ._tutor_send_harness import (
    app as app,  # noqa: PLC0414 - re-exported so the fixture resolves here
)
from ._tutor_send_harness import (
    isolated_reply_limiter as isolated_reply_limiter,  # noqa: PLC0414
)
from ._tutor_send_harness import (
    stub_tutor_model as stub_tutor_model,  # noqa: PLC0414
)

if TYPE_CHECKING:
    from fastapi import FastAPI


# --------------------------------------------------------------------------- #
# The flag gate (epic #82, owner amendment 1)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_off_hides_the_send_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The inherited router-level gate covers the send endpoint too.

    ``404`` (never ``403``), *before* the stream opens, and with nothing written
    — the request is otherwise entirely valid, so the gate is the only thing
    standing between it and a turn.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        response = await _json_error(client, _send_url(path_id), _body(lesson_id))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert await _count(Message) == 0
    assert await _count(Conversation) == 0


# --------------------------------------------------------------------------- #
# Pre-stream validation (§6)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_ungenerated_lesson_is_409(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Lesson scope is empty until a Read passage exists — nothing to ground on."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id, generated=False)

        response = await _json_error(client, _send_url(path_id), _body(lesson_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_another_paths_lesson_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """A real lesson that is not on *this* path is not addressable here."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_id = await _seed_path(user_id, topic="Rust ownership")
        _other_path, other_lesson = await _seed_path(user_id, topic="Go channels")

        response = await _json_error(client, _send_url(path_id), _body(other_lesson))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_another_learners_path_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Ownership failures never disclose existence (404, never 403)."""
    async with _client(app) as owner_client:
        owner_id = await _sign_in(owner_client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(owner_id)

    async with _client(app) as other_client:
        await _sign_in(other_client, monkeypatch, OTHER)
        response = await _json_error(other_client, _send_url(path_id), _body(lesson_id))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_oversize_content_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """The 2000-character DTO bound is enforced before the stream opens."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        response = await _json_error(
            client, _send_url(path_id), _body(lesson_id, content="x" * 2001)
        )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_anonymous_send_is_401(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.post(_send_url(uuid.uuid4()), json=_body(uuid.uuid4()))

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.anyio
async def test_rate_limited_send_is_429_pre_stream(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """With the cap raised off its default, the check runs before the stream."""
    monkeypatch.setattr(settings, "rate_limit_tutor_messages_per_day", 1)
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        await _seed_turn(path_id=path_id, lesson_id=lesson_id)

        response = await _json_error(client, _send_url(path_id), _body(lesson_id))

    assert response.status_code == 429, response.text
    assert response.json()["error"]["code"] == "rate_limited"
    assert await _count(Message) == 2, "nothing new was written"


# --------------------------------------------------------------------------- #
# The per-message model override (§5.3)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_admin_model_override_routes_the_reply_and_persists_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chosen id reaches the model call and is written down nowhere."""
    override = "anthropic/claude-haiku-4-5"
    assert override in settings.allowlist_ids
    assert override != settings.model_tutor

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, ADMIN)
        path_id, lesson_id = await _seed_path(user_id)
        routed = _use_model(monkeypatch, build_stub_model())

        wire = await _send(client, _send_url(path_id), _body(lesson_id, model=override))

    assert wire.names[-1] == "done", wire.names
    assert routed == [override]
    # Nothing about the override is persisted: a reply is request-scoped, so
    # there is nothing to resume and no column to add.
    async with db.async_session() as session:
        path = await session.get(Path, path_id)
        assert path is not None
        assert path.model_outline is None
        assert path.model_lesson is None
    thread = await _thread(path_id)
    assert not any(override in (message.content or "") for message in thread)


@pytest.mark.anyio
async def test_default_reply_routes_the_configured_tutor_slot(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)
        routed = _use_model(monkeypatch, build_stub_model())

        wire = await _send(client, _send_url(path_id), _body(lesson_id))

    assert wire.names[-1] == "done", wire.names
    assert routed == [settings.model_tutor]


@pytest.mark.anyio
async def test_non_admin_override_is_403_before_the_allowlist(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """403 first, so a non-admin never learns the allowlist's shape."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id = await _seed_path(user_id)

        response = await _json_error(
            client, _send_url(path_id), _body(lesson_id, model="not/a-real-model")
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
        path_id, lesson_id = await _seed_path(user_id)

        response = await _json_error(
            client, _send_url(path_id), _body(lesson_id, model="evil/model")
        )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert await _count(Message) == 0
