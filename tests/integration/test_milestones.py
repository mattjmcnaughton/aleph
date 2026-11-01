"""Derived milestone recognition through the real HTTP/Postgres contract."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from aleph import db
from aleph.app import create_app
from aleph.models import Lesson

from .test_progress_api import (
    OWNER,
    _add_completed_lessons,
    _add_lesson,
    _client,
    _seed_path,
    _sign_in,
)


@pytest.mark.anyio
@pytest.mark.parametrize("threshold", [100, 500, 1000])
async def test_completion_returns_both_but_retry_read_and_same_day_do_not(
    monkeypatch: pytest.MonkeyPatch, threshold: int
) -> None:
    async with _client(create_app()) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, unit_id = await _seed_path(user_id)
        await _add_completed_lessons(
            path_id=path_id, unit_id=unit_id, day_offsets=list(range(1, threshold))
        )
        lesson_id = await _add_lesson(
            path_id=path_id, unit_id=unit_id, position=threshold
        )
        url = f"/api/v1/lessons/{lesson_id}/complete"
        response = await client.post(url)
        assert response.status_code == 200, response.text
        assert response.json()["milestones"] == [
            {"metric": "daily_streak", "days": threshold},
            {"metric": "active_days", "days": threshold},
        ]
        assert (await client.post(url)).json()["milestones"] == []
        assert (
            "milestones"
            not in (await client.get(f"/api/v1/lessons/{lesson_id}")).json()
        )
        next_id = await _add_lesson(
            path_id=path_id, unit_id=unit_id, position=threshold + 1
        )
        assert (await client.post(f"/api/v1/lessons/{next_id}/complete")).json()[
            "milestones"
        ] == []


@pytest.mark.anyio
@pytest.mark.parametrize("delete_history", [False, True])
async def test_rebuilt_streak_and_deletion_recross_can_celebrate(
    monkeypatch: pytest.MonkeyPatch, delete_history: bool
) -> None:
    async with _client(create_app()) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        history, unit = await _seed_path(user_id)
        await _add_completed_lessons(
            path_id=history, unit_id=unit, day_offsets=list(range(200, 300))
        )
        if delete_history:
            assert (await client.delete(f"/api/v1/paths/{history}")).status_code == 204
        path_id, unit_id = await _seed_path(user_id, topic="Rebuilt streak")
        await _add_completed_lessons(
            path_id=path_id, unit_id=unit_id, day_offsets=list(range(1, 100))
        )
        lesson_id = await _add_lesson(path_id=path_id, unit_id=unit_id, position=100)
        response = await client.post(f"/api/v1/lessons/{lesson_id}/complete")
        assert response.status_code == 200, response.text
        expected = [{"metric": "daily_streak", "days": 100}]
        if delete_history:
            expected.append({"metric": "active_days", "days": 100})
        assert response.json()["milestones"] == expected


@pytest.mark.anyio
async def test_concurrent_cross_path_http_completions_celebrate_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _client(create_app()) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, unit_id = await _seed_path(user_id)
        await _add_completed_lessons(
            path_id=path_id, unit_id=unit_id, day_offsets=list(range(1, 100))
        )
        first = await _add_lesson(path_id=path_id, unit_id=unit_id, position=100)
        second_path, second_unit = await _seed_path(user_id, topic="Second path")
        second = await _add_lesson(path_id=second_path, unit_id=second_unit, position=1)
        responses = await asyncio.gather(
            client.post(f"/api/v1/lessons/{first}/complete"),
            client.post(f"/api/v1/lessons/{second}/complete"),
        )
        assert [r.status_code for r in responses] == [200, 200]
        assert sorted(len(r.json()["milestones"]) for r in responses) == [0, 2]
        for lesson_id in (first, second):
            assert (await client.post(f"/api/v1/lessons/{lesson_id}/complete")).json()[
                "milestones"
            ] == []


@pytest.mark.anyio
@pytest.mark.parametrize("offset", [-840, 840])
async def test_completion_uses_learner_local_day(
    monkeypatch: pytest.MonkeyPatch, offset: int
) -> None:
    async with _client(create_app()) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, unit_id = await _seed_path(user_id)
        await _add_completed_lessons(
            path_id=path_id, unit_id=unit_id, day_offsets=list(range(1, 100))
        )
        local_today = (datetime.now(UTC) - timedelta(minutes=offset)).date()
        async with db.async_session() as session:
            await session.execute(
                text(
                    "UPDATE lessons SET completed_at = completed_at + "
                    "make_interval(mins => :offset, days => :shift) "
                    "WHERE path_id = :path"
                ),
                {
                    "offset": offset,
                    "shift": (local_today - datetime.now(UTC).date()).days,
                    "path": path_id,
                },
            )
            await session.commit()
        lesson_id = await _add_lesson(path_id=path_id, unit_id=unit_id, position=100)
        response = await client.post(
            f"/api/v1/lessons/{lesson_id}/complete?tz_offset_minutes={offset}"
        )
        assert response.status_code == 200, response.text
        assert response.json()["milestones"] == [
            {"metric": "daily_streak", "days": 100},
            {"metric": "active_days", "days": 100},
        ]


@pytest.mark.anyio
async def test_invalid_offset_does_not_complete_lesson(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _client(create_app()) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, unit_id = await _seed_path(user_id)
        lesson_id = await _add_lesson(path_id=path_id, unit_id=unit_id, position=1)
        response = await client.post(
            f"/api/v1/lessons/{lesson_id}/complete?tz_offset_minutes=901"
        )
        assert response.status_code == 422
        async with db.async_session() as session:
            assert (
                await session.scalar(
                    select(Lesson.completed_at).where(Lesson.id == lesson_id)
                )
                is None
            )
