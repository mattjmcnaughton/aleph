"""Contract tests for the Lessons API (AL-051, TDD §6).

The learner-facing HTTP surface over one lesson's content and progress, exercised
end-to-end against real Postgres with the deterministic stub model at the
model-resolution seam (fakes over mocks). Auth is the real cookie flow (a stubbed
OIDC code exchange, mirroring ``test_paths_api``) so ownership and the ``401``
gate are genuine. Generation is fire-and-forget: the module-level orchestrator's
``spawn`` seam is swapped for a collecting one so each background trigger drains
deterministically — the same mechanism ``test_paths_api`` uses.

Workflow tags (``@pytest.mark.workflow(...)``, TDD §12): W6 is the answer-hiding
invariant (no keyed answer before an Attempt); W8 is generate-retry on a failed
lesson.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.config import settings
from aleph.models import Lesson, LessonGenerationState, Path, QuickCheck
from aleph.services import generation as gen_module

from .conftest import CollectingSpawn, stub_resolver

if TYPE_CHECKING:
    from fastapi import FastAPI

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="owner-subject",
    username="owner",
    display_name="Owner User",
    email="owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="other-subject",
    username="other",
    display_name="Other User",
    email="other@example.com",
)
ADMIN = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="admin-subject",
    username="admin",
    display_name="Admin User",
    email="admin@mattjmcnaughton.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    """Point the module-level orchestrator at the stub model + a drainable spawn.

    Identical to ``test_paths_api``'s fixture (the router imports the singleton,
    so patching its seams in place is what the routes see).
    """
    collector = CollectingSpawn()
    monkeypatch.setattr(
        gen_module.generation_orchestrator, "_resolve_model", stub_resolver()
    )
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)
    return collector


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, identity: AuthIdentity
) -> None:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: identity)
    resp = await client.get("/auth/callback", follow_redirects=False)
    assert resp.status_code == 303


async def _create_and_ready(
    client: AsyncClient,
    spawn: CollectingSpawn,
    topic: str,
    level: str = "some_experience",
) -> dict:
    """POST a path, drain outline+prefetch, poll it ready, return the detail body."""
    resp = await client.post("/api/v1/paths", json={"topic": topic, "level": level})
    assert resp.status_code == 202, resp.text
    path_id = resp.json()["id"]
    await spawn.drain()
    got = await client.get(f"/api/v1/paths/{path_id}")
    await spawn.drain()
    assert got.status_code == 200, got.text
    return got.json()


def _lessons(path_body: dict) -> list[dict]:
    """Flat list of the path's lesson summaries in ``position_in_path`` order."""
    lessons = [lesson for unit in path_body["units"] for lesson in unit["lessons"]]
    return sorted(lessons, key=lambda lesson: lesson["position_in_path"])


async def _lesson(client: AsyncClient, spawn: CollectingSpawn, lesson_id: str) -> dict:
    """GET the lesson poll target, draining the resume it spawns."""
    resp = await client.get(f"/api/v1/lessons/{lesson_id}")
    await spawn.drain()
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _quick_check_row(lesson_id: str) -> QuickCheck:
    async with db.async_session() as session:
        from sqlalchemy import select

        row = await session.scalar(
            select(QuickCheck).where(QuickCheck.lesson_id == uuid.UUID(lesson_id))
        )
        assert row is not None, "expected a generated lesson to have a quick check"
        return row


async def _lesson_row(lesson_id: str) -> Lesson:
    async with db.async_session() as session:
        row = await session.get(Lesson, uuid.UUID(lesson_id))
        assert row is not None
        return row


# --------------------------------------------------------------------------- #
# W6: answer-hiding — no keyed answer in the serialized payload before an Attempt
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W6")
async def test_generated_lesson_hides_answer_before_attempt(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(client, spawn, "Rust ownership")
        first = _lessons(path)[0]
        assert first["generation_state"] == "generated"
        assert first["unlock_state"] == "available"

        resp = await client.get(f"/api/v1/lessons/{first['id']}")
        await spawn.drain()
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Content present; the two orthogonal axes both surface.
        assert body["generation_state"] == "generated"
        assert body["unlock_state"] == "available"
        assert body["read_passage"]
        assert body["quick_check"] is not None
        assert set(body["quick_check"]) == {"stem", "options"}
        assert 3 <= len(body["quick_check"]["options"]) <= 4
        # No Attempt yet, so no revealed answer object.
        assert body["attempt"] is None

        # W6 (the load-bearing assertion): the keyed answer appears NOWHERE in the
        # serialized payload before an Attempt — not just absent from a field we
        # inspected, but absent from the raw JSON string entirely.
        raw = resp.text
        assert "correct_index" not in raw
        assert "explanation" not in raw
        # And the stored keyed answer is genuinely non-trivial (the test would be
        # vacuous if the DB had no correct_index/explanation to leak).
        qc = await _quick_check_row(first["id"])
        assert qc.explanation
        assert 0 <= qc.correct_index < len(qc.options)


# --------------------------------------------------------------------------- #
# attempt: correct / incorrect feedback shape + first-wins (W6)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W6")
async def test_attempt_correct_then_second_attempt_is_unchanged(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(client, spawn, "SQL performance")
        first = _lessons(path)[0]
        qc = await _quick_check_row(first["id"])

        # First Attempt: the correct option.
        resp = await client.post(
            f"/api/v1/lessons/{first['id']}/attempt",
            json={"selected_index": qc.correct_index},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "correct"
        assert body["selected_index"] == qc.correct_index
        assert body["correct_index"] == qc.correct_index
        assert body["explanation"] == qc.explanation

        # Second Attempt with a DIFFERENT (wrong) index: first wins, unchanged.
        wrong = (qc.correct_index + 1) % len(qc.options)
        resp2 = await client.post(
            f"/api/v1/lessons/{first['id']}/attempt",
            json={"selected_index": wrong},
        )
        assert resp2.status_code == 200, resp2.text
        body2 = resp2.json()
        assert body2["outcome"] == "correct"  # unchanged
        assert body2["selected_index"] == qc.correct_index  # the first index

        # GET now reveals the Attempt (and only now carries the keyed answer).
        got = await _lesson(client, spawn, first["id"])
        assert got["attempt"] is not None
        assert got["attempt"]["outcome"] == "correct"
        assert got["attempt"]["selected_index"] == qc.correct_index
        assert got["attempt"]["correct_index"] == qc.correct_index
        assert got["attempt"]["explanation"] == qc.explanation


@pytest.mark.anyio
@pytest.mark.workflow("W6")
async def test_attempt_incorrect_reveals_correct_option(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(client, spawn, "TypeScript generics")
        first = _lessons(path)[0]
        qc = await _quick_check_row(first["id"])

        wrong = (qc.correct_index + 1) % len(qc.options)
        resp = await client.post(
            f"/api/v1/lessons/{first['id']}/attempt",
            json={"selected_index": wrong},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "incorrect"
        assert body["selected_index"] == wrong
        # An incorrect Attempt still reveals the keyed correct option + explanation
        # (formative, non-gating — CONTEXT.md).
        assert body["correct_index"] == qc.correct_index
        assert body["explanation"] == qc.explanation


# --------------------------------------------------------------------------- #
# attempt/complete gating on the unlock axis (locked → 403); complete unlocks next
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_locked_lesson_attempt_and_complete_are_403(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(client, spawn, "Rust ownership")
        lessons = _lessons(path)
        # Lesson 2 is generated by prefetch but LOCKED (lesson 1 is the available
        # one). Its state is still readable on GET, but it cannot be acted on.
        second = lessons[1]
        assert second["unlock_state"] == "locked"

        got = await _lesson(client, spawn, second["id"])
        assert got["unlock_state"] == "locked"  # visible on GET

        attempt = await client.post(
            f"/api/v1/lessons/{second['id']}/attempt", json={"selected_index": 0}
        )
        assert attempt.status_code == 403
        assert attempt.json()["error"]["code"] == "forbidden"

        complete = await client.post(f"/api/v1/lessons/{second['id']}/complete")
        assert complete.status_code == 403
        assert complete.json()["error"]["code"] == "forbidden"


@pytest.mark.anyio
async def test_complete_available_unlocks_next_and_is_idempotent(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(client, spawn, "SQL performance")
        lessons = _lessons(path)
        first, second = lessons[0], lessons[1]
        assert first["unlock_state"] == "available"
        assert second["unlock_state"] == "locked"

        # Complete the available lesson.
        resp = await client.post(f"/api/v1/lessons/{first['id']}/complete")
        await spawn.drain()
        assert resp.status_code == 200, resp.text
        assert resp.json()["unlock_state"] == "complete"

        # Idempotent: completing an already-complete lesson is a 200 no-op.
        again = await client.post(f"/api/v1/lessons/{first['id']}/complete")
        await spawn.drain()
        assert again.status_code == 200
        assert again.json()["unlock_state"] == "complete"

        # The next lesson is now available; it can be attempted.
        second_view = await _lesson(client, spawn, second["id"])
        assert second_view["unlock_state"] == "available"
        qc = await _quick_check_row(second["id"])
        attempt = await client.post(
            f"/api/v1/lessons/{second['id']}/attempt",
            json={"selected_index": qc.correct_index},
        )
        assert attempt.status_code == 200
        assert attempt.json()["outcome"] == "correct"

        # First lesson reads complete on the path detail.
        path_after = await client.get(f"/api/v1/paths/{path['id']}")
        await spawn.drain()
        first_after = _lessons(path_after.json())[0]
        assert first_after["unlock_state"] == "complete"


@pytest.mark.anyio
@pytest.mark.workflow("W3")
async def test_complete_reports_path_completion_only_on_the_last_lesson(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``path_completion`` is absent mid-path and populated on the final lesson.

    The learner-facing half of W3: the completion response is what the
    celebration renders from, so it has to answer "was that the last one?" in
    the same round trip as the tap, and it has to keep answering it on a
    re-complete (a double tap, a retried mutation).
    """
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(client, spawn, "Graph algorithms")
        lessons = _lessons(path)
        assert len(lessons) > 1, "this test needs a multi-lesson path"

        # Mid-path: complete every lesson but the last one. None of them is the
        # end of the path, so none of them reports a completion.
        for lesson in lessons[:-1]:
            await _lesson(client, spawn, lesson["id"])
            resp = await client.post(f"/api/v1/lessons/{lesson['id']}/complete")
            await spawn.drain()
            assert resp.status_code == 200, resp.text
            assert resp.json()["path_completion"] is None

        last = lessons[-1]
        await _lesson(client, spawn, last["id"])
        resp = await client.post(f"/api/v1/lessons/{last['id']}/complete")
        await spawn.drain()
        assert resp.status_code == 200, resp.text
        completion = resp.json()["path_completion"]
        assert completion is not None
        assert completion["lesson_count"] == len(lessons)
        # The span's ends are both real instants, ordered.
        first_at = datetime.fromisoformat(completion["first_completed_at"])
        last_at = datetime.fromisoformat(completion["completed_at"])
        assert first_at <= last_at

        # Idempotent: a re-complete of the final lesson answers the same way,
        # rather than dropping the learner back to the mid-path screen.
        again = await client.post(f"/api/v1/lessons/{last['id']}/complete")
        await spawn.drain()
        assert again.status_code == 200
        assert again.json()["path_completion"] == completion


@pytest.mark.anyio
async def test_attempt_on_ungenerated_lesson_is_409(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force lesson 1 to FAIL so the available lesson has no quick check yet — an
    # available-but-ungenerated lesson cannot be attempted (409, not 403/422).
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(
            client, spawn, "[force-lesson-failure:1] no content yet"
        )
        first = _lessons(path)[0]
        assert first["unlock_state"] == "available"
        assert first["generation_state"] == "failed"

        resp = await client.post(
            f"/api/v1/lessons/{first['id']}/attempt", json={"selected_index": 0}
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "conflict"


# --------------------------------------------------------------------------- #
# W8: generate retries a failed lesson (chain-head re-claim)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W8")
async def test_generate_retries_failed_lesson(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        # Lesson 2 fails deterministically; the chain stops there (lesson 1 OK).
        path = await _create_and_ready(
            client, spawn, "[force-lesson-failure:2] retry me"
        )
        lessons = _lessons(path)
        second = lessons[1]

        failed_view = await _lesson(client, spawn, second["id"])
        assert failed_view["generation_state"] == "failed"
        assert failed_view["generation_error"]  # a generic, learner-safe message
        # A failed lesson serializes no generated-only content (item (g)3-4).
        assert failed_view["read_passage"] is None
        assert failed_view["quick_check"] is None
        assert failed_view["attempt"] is None
        before = await _lesson_row(second["id"])
        assert before.generation_started_at is not None

        # Generate re-claims the failed chain head (202, non-blocking).
        resp = await client.post(f"/api/v1/lessons/{second['id']}/generate")
        assert resp.status_code == 202
        assert resp.json()["id"] == second["id"]
        await spawn.drain()

        after = await _lesson_row(second["id"])
        # The stub fails the same position deterministically, so it is failed
        # again — but a NEW claim stamp proves the retry re-claimed and re-ran it
        # (W8: generate re-claims a real failure), not a silent no-op.
        assert after.generation_started_at is not None
        assert after.generation_started_at > before.generation_started_at


# --------------------------------------------------------------------------- #
# 429: generate at the daily lesson-generation cap (rate_limited envelope)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_generate_rate_limited_returns_429_envelope(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Creation's prefetch already triggered several lesson generations today, so
    # with the cap at 1 the learner is already over it — an explicit generate is
    # denied with the shared ``rate_limited`` envelope (the route wires
    # ``check_lesson_generation``).
    monkeypatch.setattr(settings, "rate_limit_lesson_generations_per_day", 1)
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(client, spawn, "Rust ownership")
        first = _lessons(path)[0]

        resp = await client.post(f"/api/v1/lessons/{first['id']}/generate")
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["message"]
        assert body["error"]["request_id"] == resp.headers["X-Request-ID"]


@pytest.mark.anyio
async def test_admin_is_exempt_from_lesson_cap(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_lesson_generations_per_day", 1)
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)
        path = await _create_and_ready(client, spawn, "Rust ownership")
        first = _lessons(path)[0]

        resp = await client.post(f"/api/v1/lessons/{first['id']}/generate")
        assert resp.status_code == 202  # admin never capped
        await spawn.drain()


# --------------------------------------------------------------------------- #
# ownership: another learner's lesson reads/acts as 404 on every route
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_non_owner_gets_404_everywhere(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as owner, _client(app) as other:
        await _sign_in(owner, monkeypatch, OWNER)
        path = await _create_and_ready(owner, spawn, "Owner-only path")
        first = _lessons(path)[0]

        await _sign_in(other, monkeypatch, OTHER)
        lesson_id = first["id"]
        assert (await other.get(f"/api/v1/lessons/{lesson_id}")).status_code == 404
        assert (
            await other.post(f"/api/v1/lessons/{lesson_id}/generate")
        ).status_code == 404
        assert (
            await other.post(
                f"/api/v1/lessons/{lesson_id}/attempt", json={"selected_index": 0}
            )
        ).status_code == 404
        assert (
            await other.post(f"/api/v1/lessons/{lesson_id}/complete")
        ).status_code == 404


# --------------------------------------------------------------------------- #
# 401: anonymous requests are rejected through the shared envelope
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_anonymous_requests_get_401(app: FastAPI) -> None:
    random_id = uuid.uuid4()
    async with _client(app) as client:
        assert (await client.get(f"/api/v1/lessons/{random_id}")).status_code == 401
        assert (
            await client.post(f"/api/v1/lessons/{random_id}/generate")
        ).status_code == 401
        assert (
            await client.post(
                f"/api/v1/lessons/{random_id}/attempt", json={"selected_index": 0}
            )
        ).status_code == 401
        assert (
            await client.post(f"/api/v1/lessons/{random_id}/complete")
        ).status_code == 401

        body = (await client.get(f"/api/v1/lessons/{random_id}")).json()
        assert body["error"]["code"] == "unauthenticated"


@pytest.mark.anyio
async def test_unknown_lesson_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        random_id = uuid.uuid4()
        assert (await client.get(f"/api/v1/lessons/{random_id}")).status_code == 404


# --------------------------------------------------------------------------- #
# TN-1: content is gated on effective state, not on quick-check-row existence
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W6")
async def test_generating_lesson_with_quick_check_row_hides_content(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lesson whose row is ``generating`` while a Quick-check row already exists
    (a hand-crafted mid-transition state) must still hide all generated-only
    content on ``GET`` — the api.md invariant ties content to the **effective**
    generation state, not to the mere presence of a Quick-check row (TN-1). Before
    the fix this leaked ``read_passage`` / ``quick_check`` while the state read
    ``generating``."""
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(client, spawn, "Rust ownership")
        first = _lessons(path)[0]
        assert first["generation_state"] == "generated"  # generated, QC row exists

        # Roll the row back to a FRESH ``generating`` (not stale → effective
        # ``generating``), keeping the already-present Quick-check row: exactly the
        # inconsistent snapshot the old code would have served content for.
        async with db.async_session() as session:
            row = await session.get(Lesson, uuid.UUID(first["id"]))
            assert row is not None
            row.generation_state = LessonGenerationState.GENERATING
            row.generation_started_at = datetime.now(UTC)
            await session.commit()

        resp = await client.get(f"/api/v1/lessons/{first['id']}")
        await spawn.drain()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["generation_state"] == "generating"
        # Content is gated on effective state == generated, so all of it is hidden.
        assert body["read_passage"] is None
        assert body["quick_check"] is None
        assert body["attempt"] is None
        # And the keyed answer appears nowhere in the raw payload (W6 too).
        assert "correct_index" not in resp.text
        assert "explanation" not in resp.text


# --------------------------------------------------------------------------- #
# W8 (strengthened, G-1): generate recovers a failed lesson to GENERATED and
# refills the prefetch window (successor generates), not merely re-claims it.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W8")
async def test_generate_recovers_failed_lesson_and_refills_window(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        # Lesson 2 fails deterministically; the chain stops there (lesson 1 OK).
        path = await _create_and_ready(
            client, spawn, "[force-lesson-failure:2] recover me"
        )
        lessons = _lessons(path)
        second, third = lessons[1], lessons[2]

        failed_view = await _lesson(client, spawn, second["id"])
        assert failed_view["generation_state"] == "failed"

        # Clear the failure sentinel from the stored topic so the retry actually
        # succeeds (the stub keys the forced failure off the prompt's topic text).
        async with db.async_session() as session:
            row = await session.get(Path, uuid.UUID(path["id"]))
            assert row is not None
            row.topic = "recover me"
            await session.commit()

        resp = await client.post(f"/api/v1/lessons/{second['id']}/generate")
        assert resp.status_code == 202
        await spawn.drain()

        # The previously-failed chain head now recovers to GENERATED (failed →
        # generated recovery, not just a re-claim).
        recovered = await _lesson(client, spawn, second["id"])
        assert recovered["generation_state"] == "generated"
        assert recovered["read_passage"]
        assert recovered["quick_check"] is not None

        # And the prefetch window refilled past it: the successor is generated too.
        successor = await _lesson(client, spawn, third["id"])
        assert successor["generation_state"] == "generated"


# --------------------------------------------------------------------------- #
# (d) documented dead-end: completing an available-but-failed lesson is 200 but
# strands the successor (chain stops at the failed head).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_complete_past_failed_head_is_a_documented_dead_end(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(
            client, spawn, "[force-lesson-failure:1] dead end"
        )
        lessons = _lessons(path)
        first, second = lessons[0], lessons[1]

        first_view = await _lesson(client, spawn, first["id"])
        assert first_view["unlock_state"] == "available"
        assert first_view["generation_state"] == "failed"
        assert first_view["read_passage"] is None  # no content on a failed lesson
        assert first_view["quick_check"] is None

        # Completion is orthogonal to generation (§6): the available *failed*
        # lesson completes with 200.
        done = await client.post(f"/api/v1/lessons/{first['id']}/complete")
        await spawn.drain()
        assert done.status_code == 200
        assert done.json()["unlock_state"] == "complete"

        # The successor is now available on the unlock axis...
        second_view = await _lesson(client, spawn, second["id"])
        assert second_view["unlock_state"] == "available"
        assert second_view["generation_state"] == "ungenerated"

        # ...but generation is a dead end: the chain stopped at the failed head, so
        # an explicit generate on the successor returns 202 yet no-ops.
        resp = await client.post(f"/api/v1/lessons/{second['id']}/generate")
        assert resp.status_code == 202
        await spawn.drain()
        stranded = await _lesson(client, spawn, second["id"])
        assert stranded["generation_state"] == "ungenerated"
        assert stranded["read_passage"] is None
        assert stranded["quick_check"] is None


# --------------------------------------------------------------------------- #
# TN-3: a completed lesson stays attemptable (attempt gates on *not locked*).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_completed_lesson_stays_attemptable(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path = await _create_and_ready(client, spawn, "SQL indexing")
        first = _lessons(path)[0]

        # Complete the available lesson WITHOUT attempting it first.
        done = await client.post(f"/api/v1/lessons/{first['id']}/complete")
        await spawn.drain()
        assert done.status_code == 200
        assert done.json()["unlock_state"] == "complete"

        # The now-complete lesson is still attemptable — attempt refuses only a
        # LOCKED lesson, never a COMPLETE one.
        qc = await _quick_check_row(first["id"])
        resp = await client.post(
            f"/api/v1/lessons/{first['id']}/attempt",
            json={"selected_index": qc.correct_index},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "correct"
        assert body["correct_index"] == qc.correct_index
        assert body["explanation"] == qc.explanation
