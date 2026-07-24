"""Unit tests for the per-account daily rate limiter (AL-042, TDD §10).

Drives the limiter against a small in-memory fake counter (fakes over mocks) so
the cap boundary, the UTC day rollover, and the admin exemption are all tested
without a database — the real Postgres row counting is covered separately in
``tests/integration/test_rate_limit.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, status

from aleph.services.rate_limit import DailyRateLimiter


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


USER = uuid.uuid4()
OTHER = uuid.uuid4()

# A fixed instant and the next UTC day, for the rollover test.
DAY_ONE = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
DAY_TWO = DAY_ONE + timedelta(days=1)


class _FakeUsage:
    """Records billable events per user and counts those at/after ``since``."""

    def __init__(self) -> None:
        self.paths: dict[uuid.UUID, list[datetime]] = {}
        self.outlines: dict[uuid.UUID, list[datetime]] = {}
        self.lessons: dict[uuid.UUID, list[datetime]] = {}

    def add_path(self, user_id: uuid.UUID, when: datetime) -> None:
        self.paths.setdefault(user_id, []).append(when)

    def add_outline(self, user_id: uuid.UUID, when: datetime) -> None:
        self.outlines.setdefault(user_id, []).append(when)

    def add_lesson(self, user_id: uuid.UUID, when: datetime) -> None:
        self.lessons.setdefault(user_id, []).append(when)

    async def count_paths_created_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int:
        return sum(1 for t in self.paths.get(user_id, []) if t >= since)

    async def count_path_outline_generations_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int:
        return sum(1 for t in self.outlines.get(user_id, []) if t >= since)

    async def count_lesson_generations_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int:
        return sum(1 for t in self.lessons.get(user_id, []) if t >= since)


def _limiter(
    usage: _FakeUsage,
    *,
    paths: int = 10,
    lessons: int = 100,
    now: datetime = DAY_ONE,
) -> DailyRateLimiter:
    return DailyRateLimiter(
        usage,
        paths_per_day=paths,
        lesson_generations_per_day=lessons,
        now=lambda: now,
    )


@pytest.mark.anyio
async def test_path_creation_allows_up_to_cap_then_denies() -> None:
    usage = _FakeUsage()
    limiter = _limiter(usage, paths=10)

    # Creations 1..10: each check passes, then the row lands.
    for _ in range(10):
        await limiter.check_path_creation(user_id=USER, is_admin=False)
        usage.add_path(USER, DAY_ONE)

    # The 11th check sees 10 rows already today and denies.
    with pytest.raises(HTTPException) as excinfo:
        await limiter.check_path_creation(user_id=USER, is_admin=False)
    assert excinfo.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.anyio
async def test_outline_generation_reuses_the_paths_cap() -> None:
    # The retry cap counts paths-with-an-outline-attempt-today and reuses the
    # daily paths cap: cap of 3, three attempts today → the next is denied.
    usage = _FakeUsage()
    limiter = _limiter(usage, paths=3)
    for _ in range(3):
        usage.add_outline(USER, DAY_ONE)

    with pytest.raises(HTTPException) as excinfo:
        await limiter.check_outline_generation(user_id=USER, is_admin=False)
    assert excinfo.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    # Admin exempt, and a fresh UTC day resets the window.
    await limiter.check_outline_generation(user_id=USER, is_admin=True)
    await _limiter(usage, paths=3, now=DAY_TWO).check_outline_generation(
        user_id=USER, is_admin=False
    )


@pytest.mark.anyio
async def test_lesson_generation_allows_up_to_cap_then_denies() -> None:
    usage = _FakeUsage()
    limiter = _limiter(usage, lessons=100)

    for _ in range(100):
        await limiter.check_lesson_generation(user_id=USER, is_admin=False)
        usage.add_lesson(USER, DAY_ONE)

    with pytest.raises(HTTPException) as excinfo:
        await limiter.check_lesson_generation(user_id=USER, is_admin=False)
    assert excinfo.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.anyio
async def test_day_rollover_resets_the_cap() -> None:
    usage = _FakeUsage()
    for _ in range(10):
        usage.add_path(USER, DAY_ONE)

    # Same day: over the cap, denied.
    with pytest.raises(HTTPException):
        await _limiter(usage, paths=10, now=DAY_ONE).check_path_creation(
            user_id=USER, is_admin=False
        )

    # Next UTC day: yesterday's rows fall outside the window, so a fresh
    # allowance begins even though the rows still exist.
    await _limiter(usage, paths=10, now=DAY_TWO).check_path_creation(
        user_id=USER, is_admin=False
    )


@pytest.mark.anyio
async def test_admin_is_exempt_from_the_cap() -> None:
    usage = _FakeUsage()
    for _ in range(50):  # far over any cap
        usage.add_path(USER, DAY_ONE)
        usage.add_lesson(USER, DAY_ONE)

    limiter = _limiter(usage, paths=10, lessons=100)
    # No raise despite being well over both caps.
    await limiter.check_path_creation(user_id=USER, is_admin=True)
    await limiter.check_lesson_generation(user_id=USER, is_admin=True)


@pytest.mark.anyio
async def test_caps_are_per_account() -> None:
    usage = _FakeUsage()
    for _ in range(10):
        usage.add_path(USER, DAY_ONE)

    limiter = _limiter(usage, paths=10)
    # USER is capped...
    with pytest.raises(HTTPException):
        await limiter.check_path_creation(user_id=USER, is_admin=False)
    # ...but a different account has its own allowance.
    await limiter.check_path_creation(user_id=OTHER, is_admin=False)


@pytest.mark.anyio
@pytest.mark.parametrize("cap", [0, -1])
async def test_non_positive_cap_disables_the_limit(cap: int) -> None:
    usage = _FakeUsage()
    for _ in range(20):
        usage.add_path(USER, DAY_ONE)
        usage.add_lesson(USER, DAY_ONE)

    limiter = _limiter(usage, paths=cap, lessons=cap)
    await limiter.check_path_creation(user_id=USER, is_admin=False)
    await limiter.check_lesson_generation(user_id=USER, is_admin=False)


@pytest.mark.anyio
async def test_friendly_429_message_names_the_cap() -> None:
    usage = _FakeUsage()
    for _ in range(10):
        usage.add_path(USER, DAY_ONE)

    with pytest.raises(HTTPException) as excinfo:
        await _limiter(usage, paths=10).check_path_creation(
            user_id=USER, is_admin=False
        )
    detail = excinfo.value.detail
    assert isinstance(detail, str)
    assert "10" in detail
    assert "tomorrow" in detail.lower()
