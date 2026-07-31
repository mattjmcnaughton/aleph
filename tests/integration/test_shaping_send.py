"""Stream behaviour of the shaping send endpoint (AL-320, TDD §5.4-§5.5, §5.8).

``POST /api/v1/paths/{id}/shaping/conversation/messages`` once the turn is
admitted: the frames on the wire (Phase 2 §5.4 verbatim **plus** the new
``proposal`` event), whole-turn-or-nothing atomicity, lazy per-kind conversation
creation, and the isolation from the in-lesson thread that W21 turns on.
Everything that can still fail *before* the stream opens lives in
``test_shaping_send_admission``; the shared harness is ``_shaping_send_harness``.

Two things about this suite's shape are inherited from 2A's and are deliberate:

* **Progressive arrival is asserted structurally, not by timing.** httpx's
  ``ASGITransport`` collects the body before handing it back, so "streaming"
  here means *the wire contains many ``delta`` frames whose texts concatenate to
  the reply*. Wall-clock progressiveness is ``compose-smoke``'s and Playwright's
  to prove; what this tier owns is the protocol.
* **Atomicity is asserted by counting rows, in a fresh session** — a turn exists
  whole or not at all, so every failure test ends by counting rather than by
  inspecting what the service thinks it did.

Model behaviour is injected at the shaping service's ``_resolve_model`` seam, so
the real agent, the real router and the real repository all run unchanged; the
proposal cases are driven by the D12 stub sentinels rather than by hand-built
tool calls, which is what keeps this suite honest about the *whole* path from
tool call to persisted payload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from aleph import db
from aleph.models import (
    Attempt,
    Conversation,
    ConversationKind,
    Lesson,
    Message,
    MessageRole,
    MessageSource,
    PathChange,
    Unit,
)
from aleph.repositories import LessonRepository
from aleph.services.stub_model import (
    FORCE_PROPOSAL_ADD,
    FORCE_PROPOSAL_REVISE,
    FORCE_SHAPING_DECLINE,
    FORCE_SHAPING_FAILURE,
    SHAPING_DECLINED_EDIT_REPLY,
    SHAPING_REVISION_INSTRUCTION,
    build_stub_addition_proposal,
    build_stub_revision_proposal,
    clean_topic,
)

from ._shaping_send_harness import (
    ASK,
    OWNER,
    _body,
    _client,
    _seed_lesson_turn,
    _seed_path,
    _seed_shaping_turn,
    _send,
    _send_url,
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
    import uuid

    from fastapi import FastAPI


def _ask(sentinel: str) -> str:
    """A learner ask carrying one of the D12 shaping sentinels."""
    return f"{sentinel} {ASK}"


# --------------------------------------------------------------------------- #
# The happy path (W17's backend half)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W17")
async def test_full_turn_round_trip_streams_and_persists_the_pair(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Deltas, then ``done`` with both ids, and the pair at ``max+1``/``max+2``."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        await _seed_shaping_turn(path_id=path_id)

        wire = await _send(client, _send_url(path_id), _body())

    assert wire.status_code == 200, wire.body
    assert wire.headers["content-type"].startswith("text/event-stream")
    assert wire.headers["cache-control"] == "no-store"
    assert wire.headers["x-accel-buffering"] == "no"

    assert len(wire.payloads("delta")) > 1, wire.names
    assert wire.names[-1] == "done", wire.names
    assert ASK in wire.text

    done = wire.only("done")
    thread = await _thread(path_id)
    assert [message.position for message in thread] == [1, 2, 3, 4]
    learner, tutor = thread[2], thread[3]
    assert str(learner.id) == done["learner_message_id"]
    assert str(tutor.id) == done["tutor_message_id"]
    assert learner.role is MessageRole.LEARNER
    assert learner.content == ASK
    assert learner.source is MessageSource.TYPED
    assert tutor.role is MessageRole.TUTOR
    assert tutor.content == wire.text, "the persisted reply is what was streamed"
    assert tutor.proposal is None
    assert tutor.tutor_check is None
    # Shaping is path-level: neither row names a lesson (migration 0006).
    assert [message.lesson_id for message in thread] == [None, None, None, None]


@pytest.mark.anyio
async def test_first_turn_creates_the_shaping_conversation_lazily(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """No conversation row exists until a reply settles (§5.5), and it is *shaping*."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        assert await _count(Conversation) == 0

        wire = await _send(client, _send_url(path_id), _body())

    assert wire.names[-1] == "done", wire.names
    assert await _count(Conversation) == 1
    assert await _count(Message) == 2
    assert await _thread(path_id, kind=ConversationKind.LESSON) == []


@pytest.mark.anyio
async def test_suggestion_source_is_recorded_on_the_learner_row(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """A §5.3 suggestion sends as if typed, tagged ``suggestion`` (the §7 mix)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        wire = await _send(client, _send_url(path_id), _body(source="suggestion"))

    assert wire.names[-1] == "done", wire.names
    thread = await _thread(path_id)
    assert thread[0].source is MessageSource.SUGGESTION


# --------------------------------------------------------------------------- #
# The Proposal: observed from the event stream, emitted, persisted (D4, §5.4)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W17")
async def test_an_addition_proposal_reaches_the_wire_and_the_row(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """``[force-proposal-add]`` → one ``proposal`` event, the same payload stored.

    The event's data is the **bare validated payload** — ``{operations, summary}``
    and nothing else — because the rail draws its card from this frame mid-stream
    and re-draws the same card from a later thread read. A ``resolution`` here
    would be a field with one possible value (a proposal just made is pending).
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        wire = await _send(
            client, _send_url(path_id), _body(content=_ask(FORCE_PROPOSAL_ADD))
        )

    assert wire.names[-1] == "done", wire.names
    proposal = wire.only("proposal")
    # The path has no engagement, so the boundary is position 1 — which is what
    # the stub read out of the assembled prompt to place its Addition.
    expected = build_stub_addition_proposal(
        _clean(_ask(FORCE_PROPOSAL_ADD)), insert_at_position=1
    )
    assert proposal == {
        "operations": expected["operations"],
        "summary": expected["summary"],
    }
    assert set(proposal) == {"operations", "summary"}, (
        "the stream event carries the bare payload (AL-330's contract)"
    )

    thread = await _thread(path_id)
    learner, tutor = thread
    assert learner.proposal is None
    assert tutor.proposal == proposal, "the wire card and the stored card are one"


@pytest.mark.anyio
@pytest.mark.workflow("W18")
async def test_a_revision_proposal_names_the_first_unengaged_lesson(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """``[force-proposal-revise]`` targets the boundary lesson, by id (D2).

    The instruction is the stub's own, which is the closed loop W18 later asserts
    on: apply (AL-321) writes it to ``revision_instruction`` and the regenerated
    passage carries its marker.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)

        wire = await _send(
            client, _send_url(path_id), _body(content=_ask(FORCE_PROPOSAL_REVISE))
        )

    assert wire.names[-1] == "done", wire.names
    proposal = wire.only("proposal")
    expected = build_stub_revision_proposal(
        _clean(_ask(FORCE_PROPOSAL_REVISE)), lesson_id=str(lessons[0])
    )
    assert proposal["operations"] == expected["operations"]
    operation = proposal["operations"][0]
    assert operation["lesson_id"] == str(lessons[0])
    assert operation["instruction"] == SHAPING_REVISION_INSTRUCTION


@pytest.mark.anyio
@pytest.mark.workflow("W17")
async def test_a_proposal_writes_no_path_structure(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Consent is structural: proposing changes nothing until **Apply** (D5).

    The strongest form of this ticket's central claim, asserted the only way that
    is worth anything — by counting the tables a Proposal talks *about*.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)
        before = (await _count(Lesson), await _count(Unit))

        wire = await _send(
            client, _send_url(path_id), _body(content=_ask(FORCE_PROPOSAL_ADD))
        )

    assert wire.payloads("proposal"), wire.names
    assert (await _count(Lesson), await _count(Unit)) == before
    assert await _count(PathChange) == 0
    assert await _count(Attempt) == 0
    titles = await _lesson_titles(path_id)
    assert all("Added on request" not in title for title in titles)
    assert len(titles) == len(lessons)


@pytest.mark.anyio
@pytest.mark.workflow("W20")
async def test_a_declined_edit_persists_like_any_other_reply(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """``[force-shaping-decline]`` is an ordinary turn — wording only, no tag.

    No ``proposal`` frame, no payload on the row, no machine-readable marker of
    any kind: a **declined edit** is distinguished from any other reply purely by
    what it says (§5.5, the 2A refusal posture). The whole record of it is the
    text the learner read.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        wire = await _send(
            client, _send_url(path_id), _body(content=_ask(FORCE_SHAPING_DECLINE))
        )

    assert wire.names[-1] == "done", wire.names
    assert "proposal" not in wire.names
    assert wire.text == SHAPING_DECLINED_EDIT_REPLY

    thread = await _thread(path_id)
    assert [message.role for message in thread] == [
        MessageRole.LEARNER,
        MessageRole.TUTOR,
    ]
    tutor = thread[1]
    assert tutor.content == SHAPING_DECLINED_EDIT_REPLY
    assert tutor.proposal is None
    assert tutor.tutor_check is None


# --------------------------------------------------------------------------- #
# Atomicity and failure (§5.8)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_mid_stream_failure_ends_in_error_and_persists_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """``[force-shaping-failure]`` raises after deltas are already on the wire."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        wire = await _send(
            client, _send_url(path_id), _body(content=_ask(FORCE_SHAPING_FAILURE))
        )

    assert wire.status_code == 200, wire.body
    assert wire.payloads("delta"), "the failure must land mid-stream, after deltas"
    assert wire.names[-1] == "error", wire.names
    assert "done" not in wire.names
    error = wire.only("error")
    assert error["code"] == "upstream_error"
    assert "connection" not in error["message"].lower(), (
        "an upstream failure must not be worded as the learner's network problem"
    )
    # Whole turn or nothing.
    assert await _count(Message) == 0
    assert await _count(Conversation) == 0


@pytest.mark.anyio
async def test_a_failed_turn_leaves_an_existing_thread_untouched(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """Atomicity on a thread that already has turns: nothing is appended."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)
        await _seed_shaping_turn(path_id=path_id)

        wire = await _send(
            client, _send_url(path_id), _body(content=_ask(FORCE_SHAPING_FAILURE))
        )

    assert wire.names[-1] == "error", wire.names
    thread = await _thread(path_id)
    assert [message.position for message in thread] == [1, 2]
    assert thread[0].content == "An earlier ask"


@pytest.mark.anyio
async def test_a_failed_turn_releases_the_conversation(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """A failure must not wedge the thread on a permanent 409 (D11)."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _lessons = await _seed_path(user_id)

        failed = await _send(
            client, _send_url(path_id), _body(content=_ask(FORCE_SHAPING_FAILURE))
        )
        assert failed.names[-1] == "error", failed.names

        retried = await _send(client, _send_url(path_id), _body())

    assert retried.names[-1] == "done", retried.names
    assert [message.position for message in await _thread(path_id)] == [1, 2]


# --------------------------------------------------------------------------- #
# Two threads on one path (D3, W21)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W21")
async def test_the_two_threads_are_isolated_on_one_path(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """A shaping turn lands in the shaping thread and nowhere else (PRD §5.8).

    The in-lesson conversation keeps exactly the turns it had, its own positions,
    and its own lesson tags — the rails never show each other's turns, which is
    what lets 2A's surface stay bit-identical.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)
        await _seed_lesson_turn(path_id=path_id, lesson_id=lessons[0])

        wire = await _send(client, _send_url(path_id), _body())

    assert wire.names[-1] == "done", wire.names
    assert await _count(Conversation) == 2, "one thread of each kind"

    lesson_thread = await _thread(path_id, kind=ConversationKind.LESSON)
    assert [message.position for message in lesson_thread] == [1, 2]
    assert [message.lesson_id for message in lesson_thread] == [lessons[0], lessons[0]]
    assert lesson_thread[0].content == "A question about this lesson"

    shaping_thread = await _thread(path_id)
    assert [message.position for message in shaping_thread] == [1, 2]
    assert shaping_thread[0].content == ASK


@pytest.mark.anyio
@pytest.mark.workflow("W21")
async def test_the_in_lesson_thread_reads_identically_after_a_shaping_turn(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    shaping_flag_enabled: None,
    tutor_flag_enabled: None,
) -> None:
    """``GET /conversation`` (2A's route) is byte-identical across a shaping turn.

    The one assertion that turns "the outer join did not change 2A" from a claim
    into a test: the whole 2A payload is captured before and compared after.

    Compared as **raw bytes**, not as parsed JSON. Parsed equality would let a
    reordered key, a changed number format or a new whitespace convention pass as
    "identical" — and W21's promise to the already-shipped rail is that its
    response did not change at all, which is a statement about what goes on the
    wire.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lessons = await _seed_path(user_id)
        await _seed_lesson_turn(path_id=path_id, lesson_id=lessons[0])
        # Both flags: the 2A surface has its own, and the two fixtures compose.
        before = await client.get(f"/api/v1/paths/{path_id}/conversation")

        wire = await _send(client, _send_url(path_id), _body())
        after = await client.get(f"/api/v1/paths/{path_id}/conversation")

    assert wire.names[-1] == "done", wire.names
    assert before.status_code == 200, before.text
    assert before.content == after.content, "2A's payload changed byte-for-byte"
    messages: list[dict[str, Any]] = after.json()["messages"]
    assert len(messages) == 2
    assert messages[0]["lesson_id"] == str(lessons[0])
    assert messages[0]["lesson_title"] == "Ownership, part 1"


def _clean(text: str) -> str:
    """The stub's own sentinel-stripping, for building an expected payload."""
    return clean_topic(text)


async def _lesson_titles(path_id: uuid.UUID) -> list[str]:
    """The path's lesson titles, read back in a fresh session."""
    async with db.async_session() as session:
        lessons = await LessonRepository(session).list_for_path(path_id)
        return [lesson.title for lesson in lessons]
