"""Contract tests for About you and the Learning summary, real Postgres.

Auth is the real cookie flow with a stubbed OIDC code exchange (the
``test_progress_api`` pattern), so ownership is genuine. The summary run is
driven through a collecting spawn and the deterministic stub model, so the
trigger + poll contract is exercised without a provider.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.models import (
    Attempt,
    Beat,
    LearningSummary,
    Lesson,
    LessonGenerationState,
    Level,
    Path,
    PathStatus,
    QuickCheck,
    Unit,
)
from aleph.services.learning_summary import learning_summary_service

from .conftest import CollectingSpawn, stub_resolver

if TYPE_CHECKING:
    from fastapi import FastAPI

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="account-owner-subject",
    username="account-owner",
    display_name="Account Owner",
    email="owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="account-other-subject",
    username="account-other",
    display_name="Account Other",
    email="other@example.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def summary_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from .conftest import _enable_flag_globally

    _enable_flag_globally(monkeypatch, "learning_summary")


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    collecting = CollectingSpawn(hold=True)
    monkeypatch.setattr(learning_summary_service, "_spawn", collecting)
    monkeypatch.setattr(learning_summary_service, "_resolve_model", stub_resolver())
    return collecting


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, identity: AuthIdentity
) -> uuid.UUID:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: identity)
    response = await client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 303, response.text
    session = await client.get("/api/v1/auth/session")
    return uuid.UUID(session.json()["user"]["id"])


async def _seed(user_id: uuid.UUID, *, completed: int, missed_at: int | None = None):
    """A ready Rust path of 5 lessons with ``completed`` done, plus one Beat."""
    async with db.async_session() as session:
        path = Path(
            user_id=user_id,
            topic="Rust ownership",
            level=Level.SOME_EXPERIENCE,
            status=PathStatus.READY,
        )
        session.add(path)
        await session.flush()
        unit = Unit(path=path, position=1, title="Foundations", summary="s")
        session.add(unit)
        await session.flush()
        for position in range(1, 6):
            lesson = Lesson(
                unit_id=unit.id,
                path_id=path.id,
                position_in_path=position,
                position_in_unit=position,
                title=f"Lesson {position}",
                generation_state=LessonGenerationState.GENERATED,
                read_passage="SECRET PASSAGE BODY",
                completed_at=datetime.now(UTC) if position <= completed else None,
            )
            session.add(lesson)
            await session.flush()
            if position == missed_at:
                check = QuickCheck(
                    lesson_id=lesson.id,
                    stem="?",
                    options=["a", "b", "c"],
                    correct_index=0,
                    explanation="e",
                )
                session.add(check)
                await session.flush()
                session.add(
                    Attempt(
                        quick_check_id=check.id,
                        user_id=user_id,
                        selected_index=1,
                        is_correct=False,
                    )
                )
        session.add(
            Beat(
                user_id=user_id,
                topic="AI infrastructure",
                level=Level.WORK_IN_IT,
                anchor_weekday=0,
            )
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# About you
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_about_you_round_trips_per_account_and_clears(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as owner, _client(app) as other:
        await _sign_in(owner, monkeypatch, OWNER)
        await _sign_in(other, monkeypatch, OTHER)

        assert (await owner.get("/api/v1/about-you")).json() == {"about_you": None}
        put = await owner.put(
            "/api/v1/about-you", json={"about_you": "  I'm a nurse.  "}
        )
        assert put.status_code == 200, put.text
        assert put.json() == {"about_you": "I'm a nurse."}

        # Another account never sees it.
        assert (await other.get("/api/v1/about-you")).json() == {"about_you": None}
        assert (await owner.get("/api/v1/about-you")).json() == {
            "about_you": "I'm a nurse."
        }

        cleared = await owner.put("/api/v1/about-you", json={"about_you": None})
        assert cleared.json() == {"about_you": None}
        too_long = await owner.put("/api/v1/about-you", json={"about_you": "x" * 2001})
        assert too_long.status_code == 422


@pytest.mark.anyio
async def test_about_you_requires_sign_in(app: FastAPI) -> None:
    async with _client(app) as client:
        assert (await client.get("/api/v1/about-you")).status_code == 401


# --------------------------------------------------------------------------- #
# Learning summary
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_summary_is_hidden_when_the_flag_is_off(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        assert (await client.get("/api/v1/learning-summary")).status_code == 404
        assert (await client.post("/api/v1/learning-summary")).status_code == 404


@pytest.mark.anyio
async def test_too_early_is_a_state_and_a_trigger_is_refused(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    summary_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        await _seed(user_id, completed=2)

        body = (await client.get("/api/v1/learning-summary")).json()
        assert body["state"] == "too_early"
        assert body["facts"] == {"lessons_complete": 2, "paths": 1, "beats": 1}

        refused = await client.post("/api/v1/learning-summary")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "not_enough_progress"
    assert spawn.tasks == []


@pytest.mark.anyio
async def test_trigger_then_poll_writes_a_summary_from_titles_only(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    summary_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        await _seed(user_id, completed=3, missed_at=2)

        assert (await client.get("/api/v1/learning-summary")).json()["state"] == "none"
        started = await client.post("/api/v1/learning-summary")
        assert started.status_code == 202, started.text
        assert started.json()["state"] == "generating"

        # A second trigger while one is in flight starts nothing new.
        again = await client.post("/api/v1/learning-summary")
        assert again.json()["state"] == "generating"
        assert len(spawn.tasks) == 1

        await spawn.drain()
        body = (await client.get("/api/v1/learning-summary")).json()

    assert body["state"] == "ready"
    assert body["written_at"] is not None
    suggestion = body["summary"]["suggestions"][0]
    assert suggestion == {
        "kind": "path",
        "topic": "Rust ownership: going further",
        "level": "some_experience",
        "why": "You have 3 lessons on Rust ownership behind you.",
        "guidance": "Assume the basics are known. Go one level deeper.",
        "teaching_style": "",
    }
    titles = [area["title"] for area in body["summary"]["areas"]]
    assert titles == ["Rust ownership", "Following"]
    assert (
        body["summary"]["areas"][0]["note"] == "3 of 5 lessons complete (in progress)."
    )


@pytest.mark.anyio
async def test_the_digest_never_carries_lesson_bodies() -> None:
    from aleph.agents.learning_summary import build_learning_summary_prompt
    from aleph.repositories import UserRepository
    from aleph.repositories.learning_summaries import LearningSummaryRepository
    from aleph.services.learning_summary import build_summary_deps

    from .conftest import create_user

    async with db.async_session() as session:
        user = await create_user(session)
        await session.commit()
    await _seed(user.id, completed=3, missed_at=2)
    async with db.async_session() as session:
        repo = LearningSummaryRepository(session)
        deps = build_summary_deps(
            await repo.digest_paths(user.id),
            await repo.digest_lessons(user.id),
            await repo.digest_beats(user.id),
            await UserRepository(session).about_you(user.id),
        )
    prompt = build_learning_summary_prompt(deps)

    assert "SECRET PASSAGE BODY" not in prompt
    assert "3 of 5 lessons complete" in prompt
    assert "Quick check missed: Lesson 2" in prompt
    assert "Beat: AI infrastructure" in prompt


@pytest.mark.anyio
async def test_a_failed_run_is_failed_and_keeps_the_earlier_summary(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    summary_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        await _seed(user_id, completed=3)
        await client.post("/api/v1/learning-summary")
        await spawn.drain()
        first = (await client.get("/api/v1/learning-summary")).json()

        # Too soon after a write: refused, nothing spawned.
        too_soon = await client.post("/api/v1/learning-summary")
        assert too_soon.status_code == 429

        async with db.async_session() as session:
            await session.execute(
                update(LearningSummary)
                .where(LearningSummary.user_id == user_id)
                .values(written_at=datetime.now(UTC) - timedelta(hours=1))
            )
            await session.commit()

        def broken(_model_id: str):
            raise RuntimeError("provider down")

        monkeypatch.setattr(learning_summary_service, "_resolve_model", broken)
        await client.post("/api/v1/learning-summary")
        await spawn.drain()
        body = (await client.get("/api/v1/learning-summary")).json()

    assert body["state"] == "failed"
    assert body["summary"] == first["summary"]


@pytest.mark.anyio
async def test_a_stalled_run_reads_as_failed_and_can_be_reclaimed(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    summary_flag_enabled: None,
    spawn: CollectingSpawn,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        await _seed(user_id, completed=3)
        await client.post("/api/v1/learning-summary")
        await spawn.cancel_pending()
        async with db.async_session() as session:
            await session.execute(
                update(LearningSummary)
                .where(LearningSummary.user_id == user_id)
                .values(started_at=datetime.now(UTC) - timedelta(days=1))
            )
            await session.commit()

        assert (await client.get("/api/v1/learning-summary")).json()["state"] == (
            "failed"
        )
        retried = await client.post("/api/v1/learning-summary")
        assert retried.json()["state"] == "generating"
        await spawn.drain()
        assert (await client.get("/api/v1/learning-summary")).json()["state"] == (
            "ready"
        )
