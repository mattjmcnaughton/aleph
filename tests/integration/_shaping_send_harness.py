"""Shared harness for the shaping-endpoint integration suites (AL-320).

The Phase 2B twin of ``_tutor_send_harness``, and deliberately a thin one: the
SSE wire parser, the HTTP client and the stubbed OIDC sign-in are **imported**
from that module rather than re-written, because the transport is Phase 2 §5.4
verbatim and a second parser is how two rails start disagreeing about what a
frame is. What lives here is only what shaping genuinely has of its own — a
``ready`` path with real lessons to shape, its own identities, and fixtures that
isolate the *shaping* service's seams.

The coverage is split the same way 2A's is: pre-stream admission
(``test_shaping_send_admission``), stream behaviour and atomicity
(``test_shaping_send``), and the non-streaming conversation routes
(``test_shaping_api``). Nothing here asserts anything — it is arrange-and-observe
machinery only, so a behavioural change lands in exactly one test file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from aleph import db
from aleph.auth import AuthIdentity
from aleph.config import settings
from aleph.models import (
    ConversationKind,
    Lesson,
    LessonGenerationState,
    Level,
    Message,
    MessageSource,
    Path,
    PathStatus,
    QuickCheck,
    Unit,
)
from aleph.repositories import ConversationRepository
from aleph.services import shaping as shaping_service
from aleph.services.lifecycle import TutorReplyLimiter
from aleph.services.stub_model import build_stub_model

# The transport-level machinery, one definition (see the module docstring).
from ._tutor_send_harness import (
    Wire as Wire,
)
from ._tutor_send_harness import (
    _client as _client,
)
from ._tutor_send_harness import (
    _json_error as _json_error,
)
from ._tutor_send_harness import (
    _send as _send,
)
from ._tutor_send_harness import (
    _sign_in as _sign_in,
)
from ._tutor_send_harness import (
    app as app,  # noqa: PLC0414 - re-exported so the fixture resolves in suites
)

if TYPE_CHECKING:
    import uuid

    from pydantic_ai.models import Model

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="shaping-owner-subject",
    username="shaping-owner",
    display_name="Shaping Owner",
    email="owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="shaping-other-subject",
    username="shaping-other",
    display_name="Shaping Other",
    email="other@example.com",
)
# ``mattjmcnaughton.com`` is the default admin domain, so this identity is both
# an admin (the model picker) and resolves the ``shaping`` flag on with no
# fixture — which is what makes production dogfooding real.
ADMIN = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="shaping-admin-subject",
    username="shaping-admin",
    display_name="Shaping Admin",
    email="admin@mattjmcnaughton.com",
)

ASK = "Could we go deeper on the borrow checker before the generics unit?"

# How many lessons ``_seed_path`` puts on the path. Three is enough for a
# digest with a real ``first_shapeable_position`` and for an insertion to land
# somewhere other than the end.
LESSON_COUNT = 3


@pytest.fixture(autouse=True)
def stub_shaping_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every shaping reply through the deterministic streamed stub.

    Autouse because a test that forgot it would reach a real provider with an
    empty API key. Individual tests override ``_resolve_model`` again with their
    own injected model.
    """
    _use_model(monkeypatch, build_stub_model())


@pytest.fixture(autouse=True)
def isolated_shaping_limiter(monkeypatch: pytest.MonkeyPatch) -> TutorReplyLimiter:
    """A fresh in-flight registry + semaphore per test, for the shaping service.

    The service singleton shares the *tutor's* semaphore in production (D11);
    sharing either across tests would leak a reservation from a failed test into
    the next one and bind the semaphore to a dead event loop. That the two
    singletons really do share one pool is asserted where it belongs — a unit
    test on the wiring — rather than by making every test here fight over it.
    """
    limiter = TutorReplyLimiter(max_concurrent=settings.max_concurrent_tutor_replies)
    monkeypatch.setattr(shaping_service.shaping_turn_service, "_replies", limiter)
    return limiter


def _use_model(monkeypatch: pytest.MonkeyPatch, model: Model) -> list[str]:
    """Bind ``model`` behind the shaping service's resolver; returns ids requested.

    The recorded ids are how the per-message model override is proven to reach
    the model call (§5.3) without asserting on a provider.
    """
    calls: list[str] = []

    def resolve(model_id: str) -> Model:
        calls.append(model_id)
        return model

    monkeypatch.setattr(shaping_service.shaping_turn_service, "_resolve_model", resolve)
    return calls


async def _seed_path(
    user_id: uuid.UUID,
    *,
    topic: str = "Rust ownership",
    status: PathStatus = PathStatus.READY,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Commit a path + unit + :data:`LESSON_COUNT` generated lessons.

    Returns ``(path_id, lesson_ids)`` in ``position_in_path`` order. ``status``
    is ``ready`` by default because that is the only status a shaping turn is
    admitted on (PRD §5.1); the other values are what the ``409`` test drives.
    """
    async with db.async_session() as session:
        path = Path(user_id=user_id, topic=topic, level=Level.SOME_EXPERIENCE)
        path.status = status
        session.add(path)
        await session.flush()
        unit = Unit(path=path, position=1, title="Foundations", summary="s")
        session.add(unit)
        await session.flush()
        lesson_ids: list[uuid.UUID] = []
        for position in range(1, LESSON_COUNT + 1):
            lesson = Lesson(
                unit=unit,
                path=path,
                position_in_path=position,
                position_in_unit=position,
                title=f"Ownership, part {position}",
                generation_state=LessonGenerationState.GENERATED,
                read_passage=(
                    f"## Lesson {position}: the core concepts\n\n"
                    "Ownership is Rust's memory model."
                ),
            )
            session.add(lesson)
            await session.flush()
            session.add(
                QuickCheck(
                    lesson_id=lesson.id,
                    stem=f"Which binding owns the value after move {position}?",
                    options=["The first", "The second", "Both"],
                    correct_index=1,
                    explanation="A move transfers ownership to the new binding.",
                )
            )
            lesson_ids.append(lesson.id)
        await session.commit()
        return path.id, lesson_ids


async def _seed_shaping_turn(
    *,
    path_id: uuid.UUID,
    learner_content: str = "An earlier ask",
    tutor_content: str = "An earlier answer",
    proposal: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Commit one pre-existing shaping turn; returns ``(learner_id, tutor_id)``."""
    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _created = await repository.upsert_for_path(
            path_id, kind=ConversationKind.SHAPING
        )
        learner, tutor = await repository.insert_turn(
            conversation_id=conversation.id,
            lesson_id=None,
            learner_content=learner_content,
            source=MessageSource.TYPED,
            tutor_content=tutor_content,
            proposal=proposal,
        )
        await session.commit()
        return learner.id, tutor.id


async def _seed_lesson_turn(*, path_id: uuid.UUID, lesson_id: uuid.UUID) -> None:
    """Commit one turn on the path's **in-lesson** thread (the isolation fixture)."""
    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _created = await repository.upsert_for_path(
            path_id, kind=ConversationKind.LESSON
        )
        await repository.insert_turn(
            conversation_id=conversation.id,
            lesson_id=lesson_id,
            learner_content="A question about this lesson",
            source=MessageSource.TYPED,
            tutor_content="An answer about this lesson",
        )
        await session.commit()


def _send_url(path_id: uuid.UUID | str) -> str:
    return f"/api/v1/paths/{path_id}/shaping/conversation/messages"


def _conversation_url(path_id: uuid.UUID | str) -> str:
    return f"/api/v1/paths/{path_id}/shaping/conversation"


def _body(content: str = ASK, **extra: Any) -> dict[str, Any]:
    return {"content": content, **extra}


async def _thread(
    path_id: uuid.UUID, *, kind: ConversationKind = ConversationKind.SHAPING
) -> list[Message]:
    async with db.async_session() as session:
        return [
            entry.message
            for entry in await ConversationRepository(session).load_thread(
                path_id, kind=kind
            )
        ]
