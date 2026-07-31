"""Contract tests for the shaping conversation API (AL-320, TDD §6).

The two non-streaming shaping routes — read the thread (with each Proposal's
**derived** resolution) and clear it ("new conversation", PRD §5.8) — exercised
end to end against real Postgres. Auth is the real cookie flow with a stubbed
OIDC code exchange, so the ``401`` gate and ownership are genuine; the streamed
route has its own two suites (``test_shaping_send``,
``test_shaping_send_admission``) and the shared harness is
``_shaping_send_harness``.

Three invariants get their own coverage because they are what this surface is
*for*:

* **Resolution is derived, never stored** (D3). The same
  ``derive_proposal_resolutions`` the shaper's carried history uses decides what
  the card says, so a Proposal with a live change row reads *applied* here
  without anything having written a status anywhere.
* **The two threads are separate rows** (D3, W21). Clearing the shaping thread
  leaves the in-lesson one exactly as it was, and vice versa.
* **The Change history outlives the conversation** (PRD §5.8). "New
  conversation" is not "undo everything I did" — applied changes are real path
  structure, and the record of them survives the ``DELETE`` with its
  ``message_id`` nulled.

Rows are seeded directly rather than driven through the streamed endpoint: none
of these routes stream, so the fastest arrange that produces a real thread is
the honest one.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from aleph import db
from aleph.models import (
    Conversation,
    ConversationKind,
    Message,
    PathChange,
    PathChangeKind,
    PathChangeStatus,
    PathStatus,
)
from aleph.repositories import ChangeRepository, ConversationRepository

from ._shaping_send_harness import (
    ADMIN,
    OTHER,
    OWNER,
    _client,
    _conversation_url,
    _seed_lesson_turn,
    _seed_path,
    _seed_shaping_turn,
    _sign_in,
    _thread,
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
from ._tutor_send_harness import _count

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx import AsyncClient


def _addition(*, insert_at_position: int = 1, title: str = "A brand new lesson") -> Any:
    """A valid **Addition** payload, as the tool would have produced it."""
    return {
        "operations": [
            {
                "insert_at_position": insert_at_position,
                "lessons": [{"title": title}],
                "rationale": "The path does not cover this yet.",
                "estimated_minutes": 5,
                "new_unit": None,
            }
        ],
        "summary": f"Adds 1 lesson at position {insert_at_position}, about 5 minutes.",
    }


async def _seed_change(
    *, path_id: uuid.UUID, message_id: uuid.UUID, undone: bool = False
) -> uuid.UUID:
    """Record an applied (or undone) Change against ``message_id``."""
    async with db.async_session() as session:
        change = await ChangeRepository(session).create(
            path_id=path_id,
            message_id=message_id,
            kind=PathChangeKind.ADD_LESSONS,
            payload=_addition(),
        )
        if undone:
            change.status = PathChangeStatus.UNDONE
        await session.commit()
        return change.id


async def _read(client: AsyncClient, path_id: uuid.UUID) -> Any:
    response = await client.get(_conversation_url(path_id))
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# GET the thread (§6)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_path_with_no_shaping_thread_reads_as_an_empty_list(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """``200`` with no messages, never ``404`` — the row is created lazily."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        body = await _read(client, path_id)

    assert body == {"messages": []}


@pytest.mark.anyio
async def test_the_thread_reads_oldest_first_without_lesson_fields(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Shaping is path-level, so no message carries a lesson on the wire (§6)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        await _seed_shaping_turn(
            path_id=path_id, learner_content="First ask", tutor_content="First answer"
        )
        await _seed_shaping_turn(
            path_id=path_id, learner_content="Second ask", tutor_content="Second answer"
        )

        body = await _read(client, path_id)

    contents = [message["content"] for message in body["messages"]]
    assert contents == ["First ask", "First answer", "Second ask", "Second answer"]
    roles = [message["role"] for message in body["messages"]]
    assert roles == ["learner", "tutor", "learner", "tutor"]
    for message in body["messages"]:
        assert set(message) == {"id", "role", "content", "proposal", "created_at"}
        assert message["proposal"] is None


@pytest.mark.anyio
async def test_a_pending_proposal_reads_with_its_payload_and_resolution(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """The stored payload plus the derived state the card renders from."""
    payload = _addition()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        await _seed_shaping_turn(path_id=path_id, proposal=payload)

        body = await _read(client, path_id)

    tutor = body["messages"][1]
    assert tutor["proposal"] == {**payload, "resolution": "pending"}
    assert body["messages"][0]["proposal"] is None, "never on a learner row"


@pytest.mark.anyio
async def test_an_applied_proposal_reads_as_applied(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """A live ``path_changes`` row referencing the message *is* the state (D3)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        _learner, tutor_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_addition()
        )
        await _seed_change(path_id=path_id, message_id=tutor_id)

        body = await _read(client, path_id)

    assert body["messages"][1]["proposal"]["resolution"] == "applied"


@pytest.mark.anyio
async def test_an_undone_proposal_reads_as_undone(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        _learner, tutor_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_addition()
        )
        await _seed_change(path_id=path_id, message_id=tutor_id, undone=True)

        body = await _read(client, path_id)

    assert body["messages"][1]["proposal"]["resolution"] == "undone"


@pytest.mark.anyio
async def test_a_stale_earlier_proposal_reads_as_superseded(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """*Superseded* is re-validation against **live** path state, not a column.

    The earlier Proposal would add a lesson whose title is already on the path,
    so the shared D1 predicates now reject it; a later Proposal has been applied;
    therefore it is superseded. The derivation is the same function the shaper's
    carried history runs, which is what stops the card and the model disagreeing.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        # The stale one: it proposes a title the path already carries.
        await _seed_shaping_turn(
            path_id=path_id, proposal=_addition(title="Ownership, part 2")
        )
        _learner, later_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_addition(title="Something else entirely")
        )
        await _seed_change(path_id=path_id, message_id=later_id)

        body = await _read(client, path_id)

    assert body["messages"][1]["proposal"]["resolution"] == "superseded"
    assert body["messages"][3]["proposal"]["resolution"] == "applied"


@pytest.mark.anyio
async def test_the_shaping_thread_never_shows_in_lesson_turns(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Two threads on one path, and each read is scoped to its kind (PRD §5.8)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)
        await _seed_lesson_turn(path_id=path_id, lesson_id=lessons[0])
        await _seed_shaping_turn(path_id=path_id, learner_content="A shaping ask")

        body = await _read(client, path_id)

    assert [message["content"] for message in body["messages"]] == [
        "A shaping ask",
        "An earlier answer",
    ]


@pytest.mark.anyio
async def test_another_learners_thread_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as owner_client:
        owner_id = await _sign_in(owner_client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(owner_id)
        await _seed_shaping_turn(path_id=path_id)

    async with _client(app) as other_client:
        await _sign_in(other_client, monkeypatch, OTHER)
        response = await other_client.get(_conversation_url(path_id))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_anonymous_read_is_401(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.get(_conversation_url(uuid.uuid4()))

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.anyio
async def test_an_admin_reaches_the_surface_with_no_fixture(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ADMIN_DEFAULT_FLAGS`` is what makes production dogfooding real."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, ADMIN)
        path_id, _lessons = await _seed_path(user_id)

        response = await client.get(_conversation_url(path_id))

    assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_a_non_ready_path_can_still_be_read(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """The ``ready`` rule bounds *sending*, not reading (§5.5).

    A path whose outline later failed must not swallow the conversation the
    learner already had — the record is theirs either way.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id, status=PathStatus.FAILED)
        await _seed_shaping_turn(path_id=path_id)

        body = await _read(client, path_id)

    assert len(body["messages"]) == 2


# --------------------------------------------------------------------------- #
# DELETE the thread — "new conversation" (PRD §5.8)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_clearing_the_thread_is_204_and_empties_it(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        await _seed_shaping_turn(path_id=path_id)

        response = await client.delete(_conversation_url(path_id))
        body = await _read(client, path_id)

    assert response.status_code == 204, response.text
    assert body == {"messages": []}
    assert await _count(Message) == 0
    assert await _count(Conversation) == 0


@pytest.mark.anyio
async def test_clearing_an_empty_thread_is_still_204(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """One tap, so retries and double taps must not read as errors."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        first = await client.delete(_conversation_url(path_id))
        second = await client.delete(_conversation_url(path_id))

    assert (first.status_code, second.status_code) == (204, 204)


@pytest.mark.anyio
@pytest.mark.workflow("W21")
async def test_clearing_the_shaping_thread_leaves_the_in_lesson_one(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """One rail's "new conversation" must never clear the other's (D3)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)
        await _seed_lesson_turn(path_id=path_id, lesson_id=lessons[0])
        await _seed_shaping_turn(path_id=path_id)

        response = await client.delete(_conversation_url(path_id))

    assert response.status_code == 204, response.text
    lesson_thread = await _thread(path_id, kind=ConversationKind.LESSON)
    assert [message.content for message in lesson_thread] == [
        "A question about this lesson",
        "An answer about this lesson",
    ]
    assert await _thread(path_id) == []


@pytest.mark.anyio
@pytest.mark.workflow("W21")
async def test_clearing_the_in_lesson_thread_leaves_the_shaping_one(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    tutor_flag_enabled: None,
) -> None:
    """And the reverse, through 2A's own route — unchanged by this phase."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)
        await _seed_lesson_turn(path_id=path_id, lesson_id=lessons[0])
        await _seed_shaping_turn(path_id=path_id)

        response = await client.delete(f"/api/v1/paths/{path_id}/conversation")

    assert response.status_code == 204, response.text
    assert await _thread(path_id, kind=ConversationKind.LESSON) == []
    assert len(await _thread(path_id)) == 2


@pytest.mark.anyio
async def test_clearing_the_thread_keeps_the_change_history(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """ "New conversation" is not "undo everything" (PRD §5.8 / D3).

    The change survives with its ``message_id`` nulled by the ``SET NULL`` FK —
    it has to, because an applied Change is real path structure and the history
    is the learner's record of it.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        _learner, tutor_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_addition()
        )
        change_id = await _seed_change(path_id=path_id, message_id=tutor_id)

        response = await client.delete(_conversation_url(path_id))

    assert response.status_code == 204, response.text
    assert await _count(Message) == 0
    async with db.async_session() as session:
        change = await session.get(PathChange, change_id)
        assert change is not None
        assert change.message_id is None


@pytest.mark.anyio
async def test_clearing_another_learners_thread_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    async with _client(app) as owner_client:
        owner_id = await _sign_in(owner_client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(owner_id)
        await _seed_shaping_turn(path_id=path_id)

    async with _client(app) as other_client:
        await _sign_in(other_client, monkeypatch, OTHER)
        response = await other_client.delete(_conversation_url(path_id))

    assert response.status_code == 404, response.text
    async with db.async_session() as session:
        remaining = await ConversationRepository(session).get_for_path(
            path_id, kind=ConversationKind.SHAPING
        )
        assert remaining is not None, "someone else's thread was not touched"
