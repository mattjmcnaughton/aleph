"""Contract tests for the Progress API (Phase 5 TDD §11), real Postgres.

Every bullet in the TDD's Integration list, against the real HTTP surface where
the scenario is naturally end-to-end, and directly against
``LessonRepository.completion_days_for_user`` / ``services.progress_read`` for
the timezone sign convention (D3, §14 R1) — that case needs a *fixed* instant,
not "whatever time it is when CI happens to run", so it bypasses the HTTP layer
entirely rather than fighting the real wall clock.

Auth is the real cookie flow with a stubbed OIDC code exchange (mirroring
``test_tutor_api`` / ``test_shaping_api``), so the ``401`` gate and ownership are
genuine. Every HTTP-level scenario seeds completions relative to
``datetime.now(UTC).date()`` and reads the summary at the default
``tz_offset_minutes=0`` — the case where the local day and the UTC day are
always the same day, so none of these tests depends on what hour of day the
suite happens to run at.

Lessons here are always seeded **directly** (never through ``POST /paths``):
none of these scenarios is about generation, and going through the real
outline/lesson pipeline would need a model to answer — the fastest arrange that
produces a real, owned path with real completions is the honest one, exactly
the posture ``test_tutor_api`` / ``test_shaping_api`` already take.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.models import (
    Lesson,
    LessonGenerationState,
    Level,
    Path,
    PathStatus,
    Unit,
)
from aleph.repositories import LessonRepository
from aleph.services.progress_read import load_progress_summary
from aleph.services.shaping import shaping_change_service

from ._shaping_send_harness import _seed_shaping_turn
from .conftest import create_user

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI

SUMMARY_URL = "/api/v1/progress/summary"

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="progress-owner-subject",
    username="progress-owner",
    display_name="Progress Owner",
    email="owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="progress-other-subject",
    username="progress-other",
    display_name="Progress Other",
    email="other@example.com",
)


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


async def _bare_account(*, username: str) -> uuid.UUID:
    """A learner account with no HTTP session — for the direct-repository tests."""
    async with db.async_session() as session:
        user = await create_user(session, username=username)
        await session.commit()
        return user.id


def _midday_utc(day_offset: int) -> datetime:
    """A timestamp safely inside ``today - day_offset`` in UTC (no midnight risk).

    Anchored to ``datetime.now(UTC).date()`` rather than a hardcoded date, so
    every test using this stays correct no matter which real day it runs on —
    the whole point being that ``tz_offset_minutes=0`` reads are then
    insensitive to wall-clock time entirely (D3: offset 0 makes the local day
    and the UTC day the same day, always).
    """
    target_date = datetime.now(UTC).date() - timedelta(days=day_offset)
    return datetime.combine(target_date, time(12, 0), tzinfo=UTC)


async def _seed_path(
    user_id: uuid.UUID, *, topic: str = "Rust ownership"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Commit a bare path + one unit; returns ``(path_id, unit_id)``."""
    async with db.async_session() as session:
        path = Path(
            user_id=user_id,
            topic=topic,
            level=Level.SOME_EXPERIENCE,
            status=PathStatus.READY,
        )
        session.add(path)
        await session.flush()
        unit = Unit(path=path, position=1, title="Foundations", summary="s")
        session.add(unit)
        await session.commit()
        return path.id, unit.id


async def _add_lesson(
    *,
    path_id: uuid.UUID,
    unit_id: uuid.UUID,
    position: int,
    completed_at: datetime | None = None,
) -> uuid.UUID:
    async with db.async_session() as session:
        lesson = Lesson(
            unit_id=unit_id,
            path_id=path_id,
            position_in_path=position,
            position_in_unit=position,
            title=f"Lesson {position}",
            generation_state=LessonGenerationState.GENERATED,
            read_passage="Some content.",
            completed_at=completed_at,
        )
        session.add(lesson)
        await session.commit()
        return lesson.id


async def _add_completed_lessons(
    *, path_id: uuid.UUID, unit_id: uuid.UUID, day_offsets: Sequence[int]
) -> list[uuid.UUID]:
    """Add one completed lesson per entry of ``day_offsets`` (0 = today, UTC).

    Positions are assigned sequentially starting after whatever already exists
    on the path, so this can be called more than once per path.
    """
    async with db.async_session() as session:
        existing = await session.scalar(
            text("SELECT count(*) FROM lessons WHERE path_id = :path_id"),
            {"path_id": path_id},
        )
        start = (existing or 0) + 1
        lesson_ids: list[uuid.UUID] = []
        for offset_index, day_offset in enumerate(day_offsets):
            position = start + offset_index
            lesson = Lesson(
                unit_id=unit_id,
                path_id=path_id,
                position_in_path=position,
                position_in_unit=position,
                title=f"Lesson {position}",
                generation_state=LessonGenerationState.GENERATED,
                read_passage="Some content.",
                completed_at=_midday_utc(day_offset),
            )
            session.add(lesson)
            await session.flush()
            lesson_ids.append(lesson.id)
        await session.commit()
        return lesson_ids


async def _summary(
    client: AsyncClient, *, tz_offset_minutes: int | None = None
) -> dict:
    params: dict[str, int] = (
        {} if tz_offset_minutes is None else {"tz_offset_minutes": tz_offset_minutes}
    )
    response = await client.get(SUMMARY_URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# The flag gate (D7)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_off_hides_the_summary_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, streaks_flag_disabled: None
) -> None:
    """Off by default (D7): the surface answers ``404``, not ``403``."""
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        response = await client.get(SUMMARY_URL)

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_flag_on_serves_the_summary(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, streaks_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        body = await _summary(client)

    assert body["current_streak"] == 0
    assert body["paths"] == []


@pytest.mark.anyio
async def test_out_of_range_offset_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, streaks_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        too_high = await client.get(SUMMARY_URL, params={"tz_offset_minutes": 901})
        too_low = await client.get(SUMMARY_URL, params={"tz_offset_minutes": -901})

    for response in (too_high, too_low):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- #
# The endpoint against seeded completions across several days and two paths
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W22")
async def test_the_summary_folds_two_paths_into_one_global_streak(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, streaks_flag_enabled: None
) -> None:
    """Two paths, several days: the global fold, the per-path breakdown, and
    ``best_streak`` exceeding ``current_streak`` at both levels.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)

        path_a, unit_a = await _seed_path(user_id, topic="Path A")
        # A live 2-day run (today, yesterday) plus an older 4-day run — best (4)
        # exceeds current (2), at both the path level and (since path B adds no
        # new days) the global level.
        await _add_completed_lessons(
            path_id=path_a, unit_id=unit_a, day_offsets=[0, 1, 10, 11, 12, 13]
        )

        path_b, unit_b = await _seed_path(user_id, topic="Path B")
        # Only today — a 1-day streak of its own, and no new day for the global
        # union (today is already active via path A).
        await _add_completed_lessons(path_id=path_b, unit_id=unit_b, day_offsets=[0])

        body = await _summary(client, tz_offset_minutes=0)

    assert body["today"] == datetime.now(UTC).date().isoformat()
    assert body["current_streak"] == 2
    assert body["best_streak"] == 4
    assert body["completed_today"] == 2  # one lesson completed today on each path
    assert len(body["activity"]) > 0

    by_path = {row["path_id"]: row for row in body["paths"]}
    assert by_path[str(path_a)]["current_streak"] == 2
    assert by_path[str(path_a)]["best_streak"] == 4
    assert by_path[str(path_b)]["current_streak"] == 1
    assert by_path[str(path_b)]["best_streak"] == 1
    assert by_path[str(path_b)]["completed_today"] == 1


@pytest.mark.anyio
async def test_a_path_with_no_completions_is_absent_from_paths(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, streaks_flag_enabled: None
) -> None:
    """D5/§14 R2: an untouched path never appears, not even with zeros."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, _unit_id = await _seed_path(user_id)

        body = await _summary(client)

    assert body["paths"] == []
    assert body["current_streak"] == 0
    assert str(path_id) not in {row["path_id"] for row in body["paths"]}


# --------------------------------------------------------------------------- #
# The day expression's sign convention (D3, §14 R1) — fixed instants only
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_day_expression_pins_a_completion_to_the_correct_local_day() -> None:
    """``completion_days_for_user`` directly, against a fixed instant.

    The TDD's own words: a completion stamped at 23:30 UTC is *tomorrow* for a
    learner at UTC+2 (``tz_offset_minutes=-120``) and *today* for one at UTC-5
    (``tz_offset_minutes=+300``). This is the Postgres expression itself
    (``AT TIME ZONE 'UTC' - make_interval(...)`` then ``::date``) — the unit
    tests already prove the pure-Python formula (``test_progress_read.py``);
    this proves the SQL that actually ships is the same formula, which a unit
    test structurally cannot.
    """
    user_id = await _bare_account(username="progress-sign-convention")
    path_id, unit_id = await _seed_path(user_id)
    await _add_lesson(
        path_id=path_id,
        unit_id=unit_id,
        position=1,
        completed_at=datetime(2026, 1, 1, 23, 30, tzinfo=UTC),
    )

    async with db.async_session() as session:
        rows_east = await LessonRepository(session).completion_days_for_user(
            user_id=user_id, tz_offset_minutes=-120
        )
        rows_west = await LessonRepository(session).completion_days_for_user(
            user_id=user_id, tz_offset_minutes=300
        )

    assert [row.day.isoformat() for row in rows_east] == ["2026-01-02"]
    assert [row.day.isoformat() for row in rows_west] == ["2026-01-01"]


@pytest.mark.anyio
async def test_the_timezone_case_matches_under_a_non_utc_session_guc() -> None:
    """D3/§14 R1's whole reason to exist: ``SET TIME ZONE`` must not move the answer.

    Casting a bare ``timestamptz`` to ``date`` resolves in the session's
    ``TimeZone`` GUC; the shipped expression pins to UTC first so it does not.
    Run the same query under ``America/Chicago`` and under the default (UTC in
    CI) and assert byte-identical results — this is the test that distinguishes
    the shipped expression from the combined doc's original one (§14 R1).
    """
    user_id = await _bare_account(username="progress-tz-guc")
    path_id, unit_id = await _seed_path(user_id)
    await _add_lesson(
        path_id=path_id,
        unit_id=unit_id,
        position=1,
        completed_at=datetime(2026, 1, 1, 23, 30, tzinfo=UTC),
    )

    async with db.async_session() as session:
        under_utc = await LessonRepository(session).completion_days_for_user(
            user_id=user_id, tz_offset_minutes=0
        )
    async with db.async_session() as session:
        await session.execute(text("SET TIME ZONE 'America/Chicago'"))
        under_chicago = await LessonRepository(session).completion_days_for_user(
            user_id=user_id, tz_offset_minutes=0
        )

    assert [row.day.isoformat() for row in under_utc] == ["2026-01-01"]
    assert [row.day.isoformat() for row in under_chicago] == ["2026-01-01"]

    # And via the full service seam too, with a fixed ``now`` so the assertion
    # does not depend on when the suite happens to run.
    fixed_now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    async with db.async_session() as session:
        view_utc = await load_progress_summary(
            session, user_id=user_id, tz_offset_minutes=0, now=fixed_now
        )
    async with db.async_session() as session:
        await session.execute(text("SET TIME ZONE 'America/Chicago'"))
        view_chicago = await load_progress_summary(
            session, user_id=user_id, tz_offset_minutes=0, now=fixed_now
        )

    assert view_utc == view_chicago


# --------------------------------------------------------------------------- #
# Ownership: another learner's completions never appear
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_another_learners_completions_never_appear(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, streaks_flag_enabled: None
) -> None:
    async with _client(app) as owner, _client(app) as other:
        owner_id = await _sign_in(owner, monkeypatch, OWNER)
        other_id = await _sign_in(other, monkeypatch, OTHER)

        owner_path, owner_unit = await _seed_path(owner_id, topic="Owner's path")
        await _add_completed_lessons(
            path_id=owner_path, unit_id=owner_unit, day_offsets=[0]
        )
        other_path, other_unit = await _seed_path(other_id, topic="Other's path")
        await _add_completed_lessons(
            path_id=other_path, unit_id=other_unit, day_offsets=[0, 1, 2, 3, 4]
        )

        owner_body = await _summary(owner)

    # The owner's streak reflects only their own single completion day, never
    # the other learner's much longer run.
    assert owner_body["current_streak"] == 1
    assert owner_body["best_streak"] == 1
    assert [row["path_id"] for row in owner_body["paths"]] == [str(owner_path)]


# --------------------------------------------------------------------------- #
# D8: inherited idempotence (both halves)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_recompleting_a_lesson_does_not_change_the_payload(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, streaks_flag_enabled: None
) -> None:
    """The ``completed_at IS NULL`` guard means a second complete is a no-op."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, unit_id = await _seed_path(user_id, topic="Idempotence check")
        # Available (the first, uncompleted lesson) — no generation involved.
        lesson_id = await _add_lesson(path_id=path_id, unit_id=unit_id, position=1)

        first = await client.post(f"/api/v1/lessons/{lesson_id}/complete")
        assert first.status_code == 200, first.text
        before = await _summary(client)

        second = await client.post(f"/api/v1/lessons/{lesson_id}/complete")
        assert second.status_code == 200, second.text
        after = await _summary(client)

    assert before == after
    assert before["completed_today"] == 1


@pytest.mark.anyio
async def test_apply_and_undo_a_shaping_change_leaves_the_payload_untouched(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, streaks_flag_enabled: None
) -> None:
    """D8's other half: Undo never touches progress (Phase 2B §5.7).

    Applying an Addition inserts an ``ungenerated`` lesson with no
    ``completed_at``; undoing it removes exactly that row. Neither step reaches
    a completion, so the streak payload is byte-identical before, after apply,
    and after undo.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, unit_id = await _seed_path(user_id, topic="Shaping + streak")
        await _add_lesson(
            path_id=path_id,
            unit_id=unit_id,
            position=1,
            completed_at=_midday_utc(0),
        )
        await _add_lesson(path_id=path_id, unit_id=unit_id, position=2)
        await _add_lesson(path_id=path_id, unit_id=unit_id, position=3)

        before = await _summary(client)

        _learner_id, tutor_id = await _seed_shaping_turn(
            path_id=path_id, proposal=_addition_proposal()
        )
        change_id = await shaping_change_service.apply_change(
            account_id=user_id, path_id=path_id, message_id=tutor_id
        )
        after_apply = await _summary(client)

        await shaping_change_service.undo_change(
            account_id=user_id, path_id=path_id, change_id=change_id
        )
        after_undo = await _summary(client)

    assert before == after_apply == after_undo


def _addition_proposal() -> dict:
    """A minimal, valid **Addition** payload (mirrors ``test_shaping_apply.py``).

    ``insert_at_position=2``: lesson 1 is completed (engaged), so the first
    shapeable position is 2 — the same default ``test_shaping_apply.py`` uses
    for exactly this reason.
    """
    return {
        "operations": [
            {
                "insert_at_position": 2,
                "lessons": [{"title": "A brand new lesson"}],
                "rationale": "The path does not cover this yet.",
                "estimated_minutes": 5,
                "new_unit": None,
            }
        ],
        "summary": "Adds 1 lesson at position 2, about 5 minutes.",
    }


# --------------------------------------------------------------------------- #
# PRD §4.6: deleting a path erases its days from the global streak
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_deleting_a_path_erases_its_days_from_the_global_streak(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, streaks_flag_enabled: None
) -> None:
    """The accepted wart (PRD §4.6), pinned so it is a decision with a test."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)

        # Path A carries today + yesterday: a live 2-day streak.
        path_a, unit_a = await _seed_path(user_id, topic="Carries the streak")
        await _add_completed_lessons(path_id=path_a, unit_id=unit_a, day_offsets=[0, 1])
        # Path B carries a day with no bearing on the current run.
        path_b, unit_b = await _seed_path(user_id, topic="Unrelated history")
        await _add_completed_lessons(path_id=path_b, unit_id=unit_b, day_offsets=[10])

        before = await _summary(client)
        assert before["current_streak"] == 2

        delete = await client.delete(f"/api/v1/paths/{path_a}")
        assert delete.status_code == 204, delete.text

        after = await _summary(client)

    # Today and yesterday are no longer active anywhere — the streak the
    # deleted path was carrying is simply gone, exactly as PRD §4.6 accepts.
    assert after["current_streak"] == 0
    assert all(row["path_id"] != str(path_a) for row in after["paths"])
