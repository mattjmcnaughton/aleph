"""Contract tests for the Tutor conversation API (AL-221, Phase 2 TDD §6).

The three non-streaming tutor routes — read the thread, clear it ("new
conversation", PRD §5.8), and record a Tutor-check answer (PRD §5.5) — exercised
end to end against real Postgres. Auth is the real cookie flow with a stubbed
OIDC code exchange (mirroring ``test_paths_api`` / ``test_lessons_api``), so the
``401`` gate and ownership are genuine.

Two invariants get their own coverage because they are the ticket's whole point:

* **The surface does not exist while the flag is off** (epic #82, owner
  amendment 1). ``routers/v1/tutor.py`` mounts ``require_tutor_enabled`` as a
  *router-level* dependency, so every route — including AL-220's send endpoint
  once it lands — answers ``404`` (never ``403``) for an account the ``tutor``
  flag resolves off for. The ``tutor_flag_enabled`` fixture (AL-203) is how a
  test opts into the post-launch world; an admin needs no fixture at all, which
  is what makes production dogfooding real.
* **A Tutor check creates no Attempt** (W12). The check-answer write is a JSONB
  reassignment on the tutor message and nothing else — asserted here by counting
  the ``attempts`` table before and after.

The check-answer persistence assertions deliberately re-read the row in a
**fresh session**. ``Message.tutor_check`` is plain ``JSONB`` with no ORM
mutation tracking, so an in-place ``check["answered_index"] = i`` is never
flushed; re-reading through the same identity map would still show the mutated
dict and pass. A new session is what makes that bug fail the test.

Rows are seeded directly (a path, a unit, one generated lesson, and turns
through ``ConversationRepository``) rather than driven through the generation
stub: none of these routes touch generation, so the fastest arrange that
produces a real owned path is the honest one.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.config import settings
from aleph.models import (
    Attempt,
    Conversation,
    ConversationKind,
    Lesson,
    LessonGenerationState,
    Level,
    Message,
    MessageSource,
    Path,
    Unit,
)
from aleph.repositories import ConversationRepository

if TYPE_CHECKING:
    from fastapi import FastAPI

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="tutor-owner-subject",
    username="tutor-owner",
    display_name="Tutor Owner",
    email="owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="tutor-other-subject",
    username="tutor-other",
    display_name="Tutor Other",
    email="other@example.com",
)
# The email domain (``mattjmcnaughton.com``) is the default admin domain, so this
# identity resolves ``tutor`` on through ``ADMIN_DEFAULT_FLAGS`` with no fixture.
ADMIN = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="tutor-admin-subject",
    username="tutor-admin",
    display_name="Tutor Admin",
    email="admin@mattjmcnaughton.com",
)

TUTOR_CHECK: dict[str, Any] = {
    "stem": "Which binding owns the String after a move?",
    "options": ["The first", "The second", "Both"],
    "correct_index": 1,
    "explanation": "A move transfers ownership to the new binding.",
    "answered_index": None,
}


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


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
    user_id: uuid.UUID, *, topic: str = "Rust ownership"
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Commit a path + unit + one generated lesson; returns ids and lesson title."""
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
            generation_state=LessonGenerationState.GENERATED,
            read_passage="Ownership is Rust's memory model.",
        )
        session.add(lesson)
        await session.flush()
        await session.commit()
        return path.id, lesson.id, lesson.title


async def _seed_turn(
    *,
    path_id: uuid.UUID,
    lesson_id: uuid.UUID,
    learner_content: str = "Why does a move invalidate the source?",
    tutor_content: str = "Because ownership is unique.",
    tutor_check: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Commit one whole turn; returns ``(learner_message_id, tutor_message_id)``."""
    async with db.async_session() as session:
        repository = ConversationRepository(session)
        conversation, _created = await repository.upsert_for_path(
            path_id, kind=ConversationKind.LESSON
        )
        learner, tutor = await repository.insert_turn(
            conversation_id=conversation.id,
            lesson_id=lesson_id,
            learner_content=learner_content,
            source=MessageSource.TYPED,
            tutor_content=tutor_content,
            tutor_check=tutor_check,
        )
        await session.commit()
        return learner.id, tutor.id


async def _stored_check(message_id: uuid.UUID) -> dict[str, Any] | None:
    """Re-read a message's ``tutor_check`` in a **fresh** session.

    A new session means a new identity map: an in-place mutation that was never
    flushed cannot masquerade as a persisted write (see the module docstring).
    """
    async with db.async_session() as session:
        return await session.scalar(
            select(Message.tutor_check).where(Message.id == message_id)
        )


async def _count(model: type[Any]) -> int:
    async with db.async_session() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


def _conversation_url(path_id: uuid.UUID | str) -> str:
    return f"/api/v1/paths/{path_id}/conversation"


def _answer_url(message_id: uuid.UUID | str) -> str:
    return f"/api/v1/messages/{message_id}/tutor-check-answer"


# --------------------------------------------------------------------------- #
# The flag gate (epic #82, owner amendment 1)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_off_hides_every_tutor_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``tutor`` off, the whole surface reads ``404`` — never ``403``.

    Asserted against rows that genuinely exist and belong to the caller, so the
    only thing standing between the request and a ``200``/``204`` is the gate.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(user_id)
        _learner_id, tutor_id = await _seed_turn(
            path_id=path_id, lesson_id=lesson_id, tutor_check=dict(TUTOR_CHECK)
        )

        get = await client.get(_conversation_url(path_id))
        delete = await client.delete(_conversation_url(path_id))
        answer = await client.post(_answer_url(tutor_id), json={"selected_index": 1})

    for response in (get, delete, answer):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"
    # The gated DELETE and answer really did nothing: the thread survived whole.
    assert await _count(Message) == 2
    assert await _stored_check(tutor_id) == TUTOR_CHECK


@pytest.mark.anyio
async def test_flag_on_serves_the_conversation(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """The same request the gate refused succeeds once the flag resolves on."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(user_id)
        await _seed_turn(path_id=path_id, lesson_id=lesson_id)

        response = await client.get(_conversation_url(path_id))

    assert response.status_code == 200, response.text
    assert len(response.json()["messages"]) == 2


@pytest.mark.anyio
async def test_admin_resolves_the_flag_on_with_defaults_untouched(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admin dogfoods the tutor with the global default still off (AL-203).

    No ``tutor_flag_enabled`` fixture here on purpose: this is the production
    posture during the build-out — ``FEATURE_FLAG_DEFAULTS`` silent, the code
    default off, and ``ADMIN_DEFAULT_FLAGS`` opening the surface for the admin
    class alone.
    """
    assert not settings.feature_flag_defaults, "the global default must stay off"

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, ADMIN)
        path_id, _lesson_id, _title = await _seed_path(user_id)

        response = await client.get(_conversation_url(path_id))

    assert response.status_code == 200, response.text
    assert not settings.feature_flag_defaults


@pytest.mark.anyio
async def test_anonymous_requests_get_401_not_404(app: FastAPI) -> None:
    """The auth gate runs before the flag gate: signed-out is ``401``.

    ``require_tutor_enabled`` depends on ``get_current_user``, so an anonymous
    caller never reaches flag resolution and is told to sign in rather than that
    the route does not exist.
    """
    path_id = uuid.uuid4()
    async with _client(app) as client:
        response = await client.get(_conversation_url(path_id))

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthenticated"


# --------------------------------------------------------------------------- #
# GET /paths/{id}/conversation
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_empty_thread_is_200_with_no_messages(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """A path with no conversation reads ``200 {"messages": []}``, never ``404``.

    The conversation row is created lazily on the first completed turn (TDD §4),
    so "no thread yet" is the normal opening state of every path — an empty list,
    not a missing resource.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lesson_id, _title = await _seed_path(user_id)

        response = await client.get(_conversation_url(path_id))

    assert response.status_code == 200, response.text
    assert response.json() == {"messages": []}


@pytest.mark.anyio
@pytest.mark.workflow("W11")
async def test_thread_shape_carries_lesson_title_and_full_check(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """The thread returns, in position order, everything the rail renders.

    Each message carries its lesson id **and title** (the per-message lesson tag
    PRD §5.8 requires, and 2B's dividers), and the tutor row carries the whole
    Tutor check — ``correct_index`` and ``explanation`` included. That asymmetry
    with ``QuickCheckDTO`` is deliberate (TDD §6): a Tutor check is non-scoring,
    its feedback is immediate and client-side, and nothing grades it.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, title = await _seed_path(user_id)
        learner_id, tutor_id = await _seed_turn(
            path_id=path_id, lesson_id=lesson_id, tutor_check=dict(TUTOR_CHECK)
        )

        response = await client.get(_conversation_url(path_id))

    assert response.status_code == 200, response.text
    messages = response.json()["messages"]
    assert [message["id"] for message in messages] == [str(learner_id), str(tutor_id)]

    learner, tutor = messages
    assert learner["role"] == "learner"
    assert learner["content"] == "Why does a move invalidate the source?"
    assert learner["lesson_id"] == str(lesson_id)
    assert learner["lesson_title"] == title
    assert learner["tutor_check"] is None
    assert learner["created_at"] is not None

    assert tutor["role"] == "tutor"
    assert tutor["lesson_title"] == title
    assert tutor["tutor_check"] == TUTOR_CHECK


@pytest.mark.anyio
@pytest.mark.workflow("W11")
async def test_thread_spans_lessons_in_position_order(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """One conversation per path, not per lesson (PRD §5.8).

    A question asked in one lesson is still in the thread from the next, tagged
    with the lesson it was asked in.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, first_lesson_id, _title = await _seed_path(user_id)
        async with db.async_session() as session:
            path = await session.get(Path, path_id)
            assert path is not None
            unit = await session.scalar(select(Unit).where(Unit.path_id == path_id))
            assert unit is not None
            second = Lesson(
                unit=unit,
                path=path,
                position_in_path=2,
                position_in_unit=2,
                title="Borrowing",
                generation_state=LessonGenerationState.GENERATED,
                read_passage="Borrows are temporary.",
            )
            session.add(second)
            await session.commit()
            second_lesson_id, second_title = second.id, second.title

        await _seed_turn(
            path_id=path_id, lesson_id=first_lesson_id, learner_content="first lesson"
        )
        await _seed_turn(
            path_id=path_id, lesson_id=second_lesson_id, learner_content="second lesson"
        )

        response = await client.get(_conversation_url(path_id))

    messages = response.json()["messages"]
    assert [message["content"] for message in messages][::2] == [
        "first lesson",
        "second lesson",
    ]
    assert messages[2]["lesson_id"] == str(second_lesson_id)
    assert messages[2]["lesson_title"] == second_title


# --------------------------------------------------------------------------- #
# DELETE /paths/{id}/conversation  ("new conversation", PRD §5.8)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_delete_clears_the_thread_and_is_idempotent(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """ "New conversation" drops the row + cascades its messages; ``204`` always.

    The second ``DELETE`` is the affordance being tapped twice (or retried after
    a dropped response): there is nothing left to delete and that is not an
    error.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(user_id)
        await _seed_turn(path_id=path_id, lesson_id=lesson_id)

        first = await client.delete(_conversation_url(path_id))
        second = await client.delete(_conversation_url(path_id))
        after = await client.get(_conversation_url(path_id))

    assert first.status_code == 204, first.text
    assert second.status_code == 204, second.text
    assert after.status_code == 200
    assert after.json() == {"messages": []}
    assert await _count(Conversation) == 0
    assert await _count(Message) == 0


@pytest.mark.anyio
async def test_delete_leaves_the_path_and_its_lessons_intact(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Clearing a thread touches no Phase 1 state (TDD §3: reads and speaks)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(user_id)
        await _seed_turn(path_id=path_id, lesson_id=lesson_id)

        response = await client.delete(_conversation_url(path_id))

    assert response.status_code == 204, response.text
    assert await _count(Path) == 1
    assert await _count(Lesson) == 1


@pytest.mark.anyio
async def test_deleting_the_path_cascades_the_conversation(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Deleting a path removes its conversation (PRD §5.8) — schema, not code.

    ``conversations.path_id`` is ``ON DELETE CASCADE`` and ``messages`` cascades
    from there, so the Phase 1 delete route needed no change. This test is what
    keeps that free property from silently regressing.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(user_id)
        await _seed_turn(path_id=path_id, lesson_id=lesson_id)
        assert await _count(Conversation) == 1

        response = await client.delete(f"/api/v1/paths/{path_id}")

    assert response.status_code == 204, response.text
    assert await _count(Conversation) == 0
    assert await _count(Message) == 0


# --------------------------------------------------------------------------- #
# POST /messages/{id}/tutor-check-answer  (PRD §5.5)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W12")
async def test_check_answer_writes_answered_index_and_creates_no_attempt(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """``answered_index`` lands in the payload; the ``attempts`` table is untouched.

    The non-scoring rule (PRD §5.5) as a test: a Tutor check is the tutor's own
    question, so answering it records **nothing** in Phase 1's tables. The
    payload is otherwise preserved byte for byte, and the read is a fresh
    session — an in-place JSONB mutation would never have been flushed.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(user_id)
        _learner_id, tutor_id = await _seed_turn(
            path_id=path_id, lesson_id=lesson_id, tutor_check=dict(TUTOR_CHECK)
        )

        response = await client.post(_answer_url(tutor_id), json={"selected_index": 2})
        thread = await client.get(_conversation_url(path_id))

    assert response.status_code == 204, response.text
    assert await _stored_check(tutor_id) == {**TUTOR_CHECK, "answered_index": 2}
    assert await _count(Attempt) == 0
    assert thread.json()["messages"][1]["tutor_check"]["answered_index"] == 2


@pytest.mark.anyio
async def test_check_answer_records_the_latest_answer(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Answering twice overwrites: nothing scores it, so nothing is protected.

    Deliberately **not** Phase 1's first-wins Attempt rule — that exists because
    an Attempt is graded and feeds progress metrics. A Tutor check is neither, so
    the endpoint is a plain idempotent write of the learner's latest choice.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(user_id)
        _learner_id, tutor_id = await _seed_turn(
            path_id=path_id, lesson_id=lesson_id, tutor_check=dict(TUTOR_CHECK)
        )

        first = await client.post(_answer_url(tutor_id), json={"selected_index": 0})
        second = await client.post(_answer_url(tutor_id), json={"selected_index": 1})

    assert (first.status_code, second.status_code) == (204, 204)
    stored = await _stored_check(tutor_id)
    assert stored is not None
    assert stored["answered_index"] == 1


@pytest.mark.anyio
async def test_answering_a_message_without_a_check_is_409(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """A learner row (or a tutor row that posed no check) has nothing to answer.

    ``409``, not ``404``: the message exists and is the caller's — the request
    conflicts with its state (the Phase 1 "not generated yet" precedent).
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(user_id)
        learner_id, _tutor_id = await _seed_turn(
            path_id=path_id, lesson_id=lesson_id, tutor_check=None
        )

        response = await client.post(
            _answer_url(learner_id), json={"selected_index": 0}
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"


@pytest.mark.anyio
async def test_out_of_range_selected_index_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """``selected_index`` must index the stored options (they drive the reveal)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(user_id)
        _learner_id, tutor_id = await _seed_turn(
            path_id=path_id, lesson_id=lesson_id, tutor_check=dict(TUTOR_CHECK)
        )

        high = await client.post(_answer_url(tutor_id), json={"selected_index": 3})
        negative = await client.post(_answer_url(tutor_id), json={"selected_index": -1})

    assert high.status_code == 422, high.text
    assert high.json()["error"]["code"] == "validation_error"
    assert negative.status_code == 422, negative.text
    assert await _stored_check(tutor_id) == TUTOR_CHECK


# --------------------------------------------------------------------------- #
# Ownership: 404 never 403 (TDD §6, Phase 1 verbatim)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_another_accounts_path_and_message_are_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Someone else's thread is indistinguishable from one that does not exist."""
    async with _client(app) as client:
        owner_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(owner_id)
        _learner_id, tutor_id = await _seed_turn(
            path_id=path_id, lesson_id=lesson_id, tutor_check=dict(TUTOR_CHECK)
        )

        await _sign_in(client, monkeypatch, OTHER)
        get = await client.get(_conversation_url(path_id))
        delete = await client.delete(_conversation_url(path_id))
        answer = await client.post(_answer_url(tutor_id), json={"selected_index": 1})

    for response in (get, delete, answer):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"
    # The refused DELETE really did nothing: the owner's thread is still whole.
    assert await _count(Message) == 2
    assert await _stored_check(tutor_id) == TUTOR_CHECK


@pytest.mark.anyio
async def test_another_accounts_checkless_message_is_404_not_409(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """Ownership is decided before message state — a prober learns nothing.

    Answering someone else's *check-less* message must read ``404``: a ``409``
    here would disclose "this message exists but posed no check".
    """
    async with _client(app) as client:
        owner_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, _title = await _seed_path(owner_id)
        learner_id, _tutor_id = await _seed_turn(
            path_id=path_id, lesson_id=lesson_id, tutor_check=None
        )

        await _sign_in(client, monkeypatch, OTHER)
        response = await client.post(
            _answer_url(learner_id), json={"selected_index": 0}
        )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_unknown_ids_are_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """A well-formed UUID that addresses nothing reads the same as unowned."""
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        missing = uuid.uuid4()

        get = await client.get(_conversation_url(missing))
        delete = await client.delete(_conversation_url(missing))
        answer = await client.post(_answer_url(missing), json={"selected_index": 0})

    for response in (get, delete, answer):
        assert response.status_code == 404, response.text
