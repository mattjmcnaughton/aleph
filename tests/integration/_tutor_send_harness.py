"""Shared harness for the tutor send-endpoint integration suites (AL-220/AL-240).

The send endpoint's coverage is split across sibling modules — pre-stream
admission (``test_tutor_send_admission``), stream behaviour and atomicity
(``test_tutor_send``), and product events (``test_tutor_events``) — because one
file carrying all three had grown past the point where a reader could find the
test they came for. Everything the three share lives here, once: the identities,
the autouse fixtures that keep a test off a real provider, the seeding helpers,
the SSE wire parser, the injected fake models, and the two helpers that drive the
route's *response object* directly (the states httpx's ASGI transport cannot
reach).

Fixtures are imported by name into each suite (``from ._tutor_send_harness import
app, ...``), which is what puts them in that module's fixture namespace — the
autouse ones included. Nothing here asserts anything: it is arrange-and-observe
machinery only, so a behavioural change lands in exactly one test file.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.config import settings
from aleph.dtos.tutor import SendMessageRequest
from aleph.models import (
    Lesson,
    LessonGenerationState,
    Level,
    Message,
    MessageSource,
    Path,
    QuickCheck,
    Unit,
    User,
)
from aleph.repositories import ConversationRepository
from aleph.routers.v1.tutor import send_message
from aleph.services import tutor as tutor_service
from aleph.services.lifecycle import TutorReplyLimiter
from aleph.services.stub_model import (
    TUTOR_CHECK_TOOL_NAME,
    build_stub_model,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from fastapi import FastAPI
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession
    from starlette.responses import StreamingResponse

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="send-owner-subject",
    username="send-owner",
    display_name="Send Owner",
    email="owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="send-other-subject",
    username="send-other",
    display_name="Send Other",
    email="other@example.com",
)
# ``mattjmcnaughton.com`` is the default admin domain, so this identity is both
# an admin (the model picker) and resolves the ``tutor`` flag on with no fixture.
ADMIN = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="send-admin-subject",
    username="send-admin",
    display_name="Send Admin",
    email="admin@mattjmcnaughton.com",
)

QUESTION = "Why does a move invalidate the source binding?"


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture(autouse=True)
def stub_tutor_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every tutor reply through the deterministic streamed stub.

    Autouse because a test that forgets it would reach a real provider with an
    empty API key. Individual tests override ``_resolve_model`` again with their
    own injected model.
    """
    _use_model(monkeypatch, build_stub_model())


@pytest.fixture(autouse=True)
def isolated_reply_limiter(monkeypatch: pytest.MonkeyPatch) -> TutorReplyLimiter:
    """A fresh in-flight registry + semaphore per test.

    The service singleton owns one for the process; sharing it across tests
    would leak a reservation from a failed test into the next one, and the
    semaphore would be bound to a dead event loop.
    """
    limiter = TutorReplyLimiter(
        max_concurrent=settings.max_concurrent_tutor_replies,
    )
    monkeypatch.setattr(tutor_service.tutor_turn_service, "_replies", limiter)
    return limiter


def _use_model(monkeypatch: pytest.MonkeyPatch, model: Model) -> list[str]:
    """Bind ``model`` behind the service's resolver; returns the ids requested.

    The recorded ids are how the per-message model override is proven to reach
    the model call (§5.3) without asserting on a provider.
    """
    calls: list[str] = []

    def resolve(model_id: str) -> Model:
        calls.append(model_id)
        return model

    monkeypatch.setattr(tutor_service.tutor_turn_service, "_resolve_model", resolve)
    return calls


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, identity: AuthIdentity
) -> uuid.UUID:
    """Complete the stubbed OIDC callback; returns the local account id."""
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: identity)
    response = await client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 303, response.text
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200, session.text
    return uuid.UUID(session.json()["user"]["id"])


async def _seed_path(
    user_id: uuid.UUID,
    *,
    topic: str = "Rust ownership",
    generated: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Commit a path + unit + one lesson; returns ``(path_id, lesson_id)``.

    ``generated=False`` leaves the lesson without a Read passage or Quick check
    — the "not generated yet" state the send endpoint answers ``409`` for.
    """
    async with db.async_session() as session:
        path = Path(user_id=user_id, topic=topic, level=Level.SOME_EXPERIENCE)
        session.add(path)
        await session.flush()
        unit = Unit(path=path, position=1, title="Foundations", summary="s")
        session.add(unit)
        await session.flush()
        lesson = Lesson(
            unit=unit,
            path=path,
            position_in_path=1,
            position_in_unit=1,
            title="What ownership is",
            generation_state=(
                LessonGenerationState.GENERATED
                if generated
                else LessonGenerationState.UNGENERATED
            ),
            read_passage=(
                "## Lesson 1: the core concepts\n\nOwnership is Rust's memory model."
                if generated
                else None
            ),
        )
        session.add(lesson)
        await session.flush()
        if generated:
            session.add(
                QuickCheck(
                    lesson_id=lesson.id,
                    stem="Which binding owns the value after a move?",
                    options=["The first", "The second", "Both"],
                    correct_index=1,
                    explanation="A move transfers ownership to the new binding.",
                )
            )
        await session.commit()
        return path.id, lesson.id


async def _seed_turn(*, path_id: uuid.UUID, lesson_id: uuid.UUID) -> None:
    """Commit one pre-existing turn so the next one must land at ``max + 1``."""
    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _created = await repository.upsert_for_path(path_id)
        await repository.insert_turn(
            conversation_id=conversation.id,
            lesson_id=lesson_id,
            learner_content="An earlier question",
            source=MessageSource.TYPED,
            tutor_content="An earlier answer",
        )
        await session.commit()


def _send_url(path_id: uuid.UUID | str) -> str:
    return f"/api/v1/paths/{path_id}/conversation/messages"


def _body(
    lesson_id: uuid.UUID, content: str = QUESTION, **extra: Any
) -> dict[str, Any]:
    return {"lesson_id": str(lesson_id), "content": content, **extra}


# --------------------------------------------------------------------------- #
# SSE parsing
# --------------------------------------------------------------------------- #


class Wire:
    """One consumed SSE response: its headers, its events and its comments."""

    def __init__(self, status_code: int, headers: dict[str, str], body: str) -> None:
        self.status_code = status_code
        self.headers = headers
        self.body = body
        self.events: list[tuple[str, Any]] = []
        self.comments: list[str] = []
        for frame in body.split("\n\n"):
            if not frame:
                continue
            if frame.startswith(":"):
                self.comments.append(frame)
                continue
            name: str | None = None
            data: Any = None
            for line in frame.split("\n"):
                if line.startswith("event: "):
                    name = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data = json.loads(line.removeprefix("data: "))
            assert name is not None, f"frame without an event name: {frame!r}"
            self.events.append((name, data))

    @property
    def names(self) -> list[str]:
        return [name for name, _data in self.events]

    def payloads(self, name: str) -> list[Any]:
        return [data for event, data in self.events if event == name]

    def only(self, name: str) -> Any:
        found = self.payloads(name)
        assert len(found) == 1, f"expected exactly one {name}: {self.names}"
        return found[0]

    @property
    def text(self) -> str:
        """The reply as the client accumulates it — every delta, in order."""
        return "".join(delta["text"] for delta in self.payloads("delta"))


async def _send(client: AsyncClient, url: str, body: dict[str, Any]) -> Wire:
    """POST a turn and consume the whole SSE response."""
    async with client.stream("POST", url, json=body) as response:
        chunks = [chunk async for chunk in response.aiter_text()]
        return Wire(response.status_code, dict(response.headers), "".join(chunks))


async def _json_error(client: AsyncClient, url: str, body: dict[str, Any]) -> Any:
    """POST a turn expected to fail *before* the stream: an ordinary envelope."""
    response = await client.post(url, json=body)
    assert response.headers["content-type"].startswith("application/json"), (
        f"a pre-stream failure must not open a stream: {response.headers}"
    )
    return response


# --------------------------------------------------------------------------- #
# Row counting (D2's atomicity assertions)
# --------------------------------------------------------------------------- #


async def _count(model: type[Any]) -> int:
    async with db.async_session() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def _thread(path_id: uuid.UUID) -> list[Message]:
    async with db.async_session() as session:
        return [
            entry.message
            for entry in await ConversationRepository(session).load_thread(path_id)
        ]


# --------------------------------------------------------------------------- #
# Injected models (fakes over mocks)
# --------------------------------------------------------------------------- #


def _silent_model() -> FunctionModel:
    """A model that never produces a token — the hung-provider case (§5.6)."""

    async def never(
        _messages: list[ModelMessage], _info: Any
    ) -> AsyncIterator[str]:  # pragma: no cover - cancelled by the timeout
        await asyncio.Event().wait()
        yield ""

    return FunctionModel(stream_function=never)


def _malformed_check_model() -> FunctionModel:
    """A model that keeps posing an invalid Tutor check until the budget is gone.

    ``agents/tutor.py`` spends one shared ``retries=2`` budget on tool-argument
    retries *and* output validation, so a model that never corrects itself ends
    the run in ``UnexpectedModelBehavior``. That has to read as a failed reply —
    an ``error`` event with nothing persisted — not a 500.
    """

    async def always_bad(
        _messages: list[ModelMessage], _info: Any
    ) -> AsyncIterator[Any]:
        yield {
            0: DeltaToolCall(
                name=TUTOR_CHECK_TOOL_NAME,
                json_args=json.dumps(
                    {
                        "stem": "",  # empty stem: rejected by validate_tutor_check
                        "options": ["a", "b", "c"],
                        "correct_index": 0,
                        "explanation": "e",
                    }
                ),
            )
        }

    return FunctionModel(stream_function=always_bad)


def _transient_resolver(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Fail the first reply, serve the stub for every later one (W14, D10).

    The integration tier owns "retry succeeds" — Phase 1's W8 posture — so the
    failure is injected here rather than made a stub sentinel.
    """
    attempts = {"n": 0}
    stub = build_stub_model()

    async def flaky(messages: list[ModelMessage], info: Any) -> AsyncIterator[Any]:
        attempts["n"] += 1
        if attempts["n"] == 1:
            yield "Starting to answer"
            raise RuntimeError("transient provider blip")
        assert stub.stream_function is not None
        async for item in stub.stream_function(messages, info):
            yield item

    _use_model(monkeypatch, FunctionModel(stream_function=flaky))
    return lambda: attempts["n"]


def _blocking_model(
    release: asyncio.Event, *, started: asyncio.Event | None = None
) -> FunctionModel:
    """A model that holds the reply open until ``release`` is set.

    ``started`` is set the moment the model is entered — which is *after* the
    turn was admitted and its conversation reserved. It is the handshake a
    racing test waits on, rather than counting event-loop turns and hoping the
    count is still enough after the next refactor.
    """

    async def blocked(_messages: list[ModelMessage], _info: Any) -> AsyncIterator[str]:
        if started is not None:
            started.set()
        yield "Thinking"
        await release.wait()
        yield " — and here is the answer."

    return FunctionModel(stream_function=blocked)


def _integrity_error_on_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the *settle* transaction's ``COMMIT`` with an ``IntegrityError``.

    The only way a turn's position pair can collide is a bypassed reservation
    (a second process, or a bug), so the state cannot be arranged honestly from
    the outside — it is injected at the service's ``_session_factory`` seam
    instead, which keeps every other part real: the reply streams, the turn's
    INSERTs flush, the commit fails, and the ``async with`` rolls the whole
    thing back. Admission opens the first session; the settle opens the second.
    """
    opened = {"n": 0}

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        opened["n"] += 1
        async with db.async_session() as session:
            if opened["n"] > 1:

                async def conflict() -> None:
                    raise IntegrityError(
                        "INSERT INTO messages ...",
                        (),
                        Exception("uq_messages_conversation_position"),
                    )

                monkeypatch.setattr(session, "commit", conflict)
            yield session

    monkeypatch.setattr(tutor_service.tutor_turn_service, "_session_factory", factory)


# --------------------------------------------------------------------------- #
# Driving the route directly (the response object, not the HTTP client)
#
# httpx's ASGITransport runs the app to completion before handing back a
# response, so it can neither abort a stream nor observe a response that is
# cancelled before its body generator ever starts. Both are real ASGI states,
# and both are where the conversation's reservation is won or lost, so these
# helpers call the route handler and then drive the response object itself.
# --------------------------------------------------------------------------- #


def _asgi_scope() -> dict[str, Any]:
    """A minimal HTTP scope at ASGI spec 2.3 — the disconnect-listening path.

    Below spec 2.4 Starlette races ``stream_response`` against
    ``listen_for_disconnect`` in a task group and cancels the scope when the
    client goes away; that is the path uvicorn takes today and the one that can
    cancel a response before its generator has ever been stepped.
    """
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "POST",
        "path": "/api/v1/paths/x/conversation/messages",
        "headers": [],
    }


async def _route_response(
    session: AsyncSession,
    *,
    path: Path,
    user: User,
    lesson_id: uuid.UUID,
    content: str = QUESTION,
) -> StreamingResponse:
    """Call the real route handler with resolved dependencies; return its response.

    The flag gate and ownership are router-level dependencies that these tests
    already prove elsewhere; what is under test here is what the handler builds.
    """
    return await send_message(
        path=path,
        body=SendMessageRequest(lesson_id=lesson_id, content=content),
        user=user,
        session=session,
    )


async def _seed_owner_and_path(
    *, username: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A committed learner + generated path; returns ``(user, path, lesson)``."""
    async with db.async_session() as session:
        from .conftest import create_user

        user = await create_user(session, username=username, subject=username)
        await session.commit()
        user_id = user.id
    path_id, lesson_id = await _seed_path(user_id)
    return user_id, path_id, lesson_id
