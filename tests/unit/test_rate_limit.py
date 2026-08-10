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
        self.tutor_messages: dict[uuid.UUID, list[datetime]] = {}
        self.shaping_messages: dict[uuid.UUID, list[datetime]] = {}
        self.flashcard_draft_runs: dict[uuid.UUID, list[datetime]] = {}
        self.brief_research_runs: dict[uuid.UUID, list[datetime]] = {}
        self.beat_counts: dict[uuid.UUID, int] = {}

    def add_path(self, user_id: uuid.UUID, when: datetime) -> None:
        self.paths.setdefault(user_id, []).append(when)

    def add_tutor_message(self, user_id: uuid.UUID, when: datetime) -> None:
        self.tutor_messages.setdefault(user_id, []).append(when)

    def add_shaping_message(self, user_id: uuid.UUID, when: datetime) -> None:
        self.shaping_messages.setdefault(user_id, []).append(when)

    def add_outline(self, user_id: uuid.UUID, when: datetime) -> None:
        self.outlines.setdefault(user_id, []).append(when)

    def add_lesson(self, user_id: uuid.UUID, when: datetime) -> None:
        self.lessons.setdefault(user_id, []).append(when)

    def add_flashcard_draft_run(self, user_id: uuid.UUID, when: datetime) -> None:
        self.flashcard_draft_runs.setdefault(user_id, []).append(when)

    def add_brief_research_run(self, user_id: uuid.UUID, when: datetime) -> None:
        self.brief_research_runs.setdefault(user_id, []).append(when)

    def set_beat_count(self, user_id: uuid.UUID, count: int) -> None:
        """The **stock** counter's own setter — no timestamp, unlike every
        other ``add_*`` above: ``check_beat_creation`` counts current rows,
        never a window (TDD §7)."""
        self.beat_counts[user_id] = count

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

    async def count_tutor_messages_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int:
        return sum(1 for t in self.tutor_messages.get(user_id, []) if t >= since)

    async def count_shaping_messages_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int:
        return sum(1 for t in self.shaping_messages.get(user_id, []) if t >= since)

    async def count_flashcard_draft_runs_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int:
        return sum(1 for t in self.flashcard_draft_runs.get(user_id, []) if t >= since)

    async def count_brief_research_runs_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int:
        return sum(1 for t in self.brief_research_runs.get(user_id, []) if t >= since)

    async def count_beats_for_user(self, *, user_id: uuid.UUID) -> int:
        return self.beat_counts.get(user_id, 0)


def _limiter(
    usage: _FakeUsage,
    *,
    paths: int = 10,
    lessons: int = 100,
    tutor_messages: int = 0,
    shaping_messages: int = 0,
    brief_research: int = 0,
    beats: int = 0,
    now: datetime = DAY_ONE,
) -> DailyRateLimiter:
    return DailyRateLimiter(
        usage,
        paths_per_day=paths,
        lesson_generations_per_day=lessons,
        tutor_messages_per_day=tutor_messages,
        shaping_messages_per_day=shaping_messages,
        brief_research_per_day=brief_research,
        beats_per_learner=beats,
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


# --------------------------------------------------------------------------- #
# The tutor message cap (AL-220, Phase 2 TDD §7 / D8)
#
# The knob exists and the behaviour does not: ``RATE_LIMIT_TUTOR_MESSAGES_PER_DAY``
# ships at 0, which ``_exempt`` already reads as disabled. These pin that the
# default is genuinely inert, that the cap bites when an operator raises it, and
# that admins stay exempt — the three things that have to hold before the knob
# could ever be turned up.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_tutor_message_cap_is_disabled_at_the_default_of_zero() -> None:
    """Cap 0 (the shipped default, D8) never consults the count at all."""
    usage = _FakeUsage()
    for _ in range(50):
        usage.add_tutor_message(USER, DAY_ONE)

    await _limiter(usage, tutor_messages=0).check_tutor_message(
        user_id=USER, is_admin=False
    )


@pytest.mark.anyio
async def test_tutor_message_cap_allows_up_to_cap_then_denies() -> None:
    usage = _FakeUsage()
    limiter = _limiter(usage, tutor_messages=3)

    for _ in range(3):
        await limiter.check_tutor_message(user_id=USER, is_admin=False)
        usage.add_tutor_message(USER, DAY_ONE)

    with pytest.raises(HTTPException) as excinfo:
        await limiter.check_tutor_message(user_id=USER, is_admin=False)
    assert excinfo.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    detail = excinfo.value.detail
    assert isinstance(detail, str)
    assert "3" in detail
    assert "tomorrow" in detail.lower()


@pytest.mark.anyio
async def test_tutor_message_cap_exempts_admins_and_rolls_over() -> None:
    usage = _FakeUsage()
    for _ in range(20):
        usage.add_tutor_message(USER, DAY_ONE)

    await _limiter(usage, tutor_messages=3).check_tutor_message(
        user_id=USER, is_admin=True
    )
    await _limiter(usage, tutor_messages=3, now=DAY_TWO).check_tutor_message(
        user_id=USER, is_admin=False
    )


@pytest.mark.anyio
async def test_tutor_message_cap_is_per_account() -> None:
    usage = _FakeUsage()
    for _ in range(3):
        usage.add_tutor_message(USER, DAY_ONE)

    limiter = _limiter(usage, tutor_messages=3)
    with pytest.raises(HTTPException):
        await limiter.check_tutor_message(user_id=USER, is_admin=False)
    await limiter.check_tutor_message(user_id=OTHER, is_admin=False)


# --------------------------------------------------------------------------- #
# The shaping message cap (AL-320, Phase 2B TDD §7)
#
# The 2A posture verbatim — the knob exists, the behaviour does not — plus the
# one property that is genuinely new: the two rails' budgets are separate, so a
# shaping burst can never close the in-lesson tutor (or the reverse).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_shaping_message_cap_is_disabled_at_the_default_of_zero() -> None:
    """Cap 0 (the shipped default, §7) never consults the count at all."""
    usage = _FakeUsage()
    for _ in range(50):
        usage.add_shaping_message(USER, DAY_ONE)

    await _limiter(usage, shaping_messages=0).check_shaping_message(
        user_id=USER, is_admin=False
    )


@pytest.mark.anyio
async def test_shaping_message_cap_allows_up_to_cap_then_denies() -> None:
    usage = _FakeUsage()
    limiter = _limiter(usage, shaping_messages=3)

    for _ in range(3):
        await limiter.check_shaping_message(user_id=USER, is_admin=False)
        usage.add_shaping_message(USER, DAY_ONE)

    with pytest.raises(HTTPException) as excinfo:
        await limiter.check_shaping_message(user_id=USER, is_admin=False)
    assert excinfo.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    detail = excinfo.value.detail
    assert isinstance(detail, str)
    assert "3" in detail
    assert "tomorrow" in detail.lower()


@pytest.mark.anyio
async def test_shaping_message_cap_exempts_admins_and_rolls_over() -> None:
    usage = _FakeUsage()
    for _ in range(20):
        usage.add_shaping_message(USER, DAY_ONE)

    await _limiter(usage, shaping_messages=3).check_shaping_message(
        user_id=USER, is_admin=True
    )
    await _limiter(usage, shaping_messages=3, now=DAY_TWO).check_shaping_message(
        user_id=USER, is_admin=False
    )


@pytest.mark.anyio
async def test_the_two_reply_caps_have_separate_budgets() -> None:
    """A shaping burst must not spend the tutor's quota, or the reverse (§7).

    The rails are separately flag-gated and separately killable, so one filling
    the other's budget would be a kill switch nobody chose to throw.
    """
    usage = _FakeUsage()
    limiter = _limiter(usage, tutor_messages=2, shaping_messages=2)
    for _ in range(5):
        usage.add_shaping_message(USER, DAY_ONE)

    with pytest.raises(HTTPException):
        await limiter.check_shaping_message(user_id=USER, is_admin=False)
    await limiter.check_tutor_message(user_id=USER, is_admin=False)


# --------------------------------------------------------------------------- #
# The Beat research cap (AL-521, Phase 6 TDD D14) —
# ``brief_research_capacity_available``. Unlike every cap above, this one is
# **non-raising**: the arrival drain runs inside a GET the learner did not
# ask to be billed for, so hitting the cap must degrade to "no research this
# time" (a plain ``False``), never an ``HTTPException``.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_brief_research_capacity_available_up_to_cap_then_denies() -> None:
    usage = _FakeUsage()
    limiter = _limiter(usage, brief_research=3)

    for _ in range(3):
        assert (
            await limiter.brief_research_capacity_available(
                user_id=USER, is_admin=False
            )
            is True
        )
        usage.add_brief_research_run(USER, DAY_ONE)

    # The 4th check sees 3 runs already today and returns False — never raises.
    assert (
        await limiter.brief_research_capacity_available(user_id=USER, is_admin=False)
        is False
    )


@pytest.mark.anyio
async def test_brief_research_capacity_never_raises_even_over_cap() -> None:
    """The load-bearing property (TDD §7): a cap hit is a boolean, not a 429."""
    usage = _FakeUsage()
    for _ in range(50):  # far over any cap
        usage.add_brief_research_run(USER, DAY_ONE)

    limiter = _limiter(usage, brief_research=1)
    result = await limiter.brief_research_capacity_available(
        user_id=USER, is_admin=False
    )
    assert result is False  # a plain value, no exception raised to get here


@pytest.mark.anyio
async def test_brief_research_cap_is_disabled_at_the_default_of_zero() -> None:
    usage = _FakeUsage()
    for _ in range(50):
        usage.add_brief_research_run(USER, DAY_ONE)

    assert (
        await _limiter(usage, brief_research=0).brief_research_capacity_available(
            user_id=USER, is_admin=False
        )
        is True
    )


@pytest.mark.anyio
async def test_brief_research_cap_exempts_admins_and_rolls_over() -> None:
    usage = _FakeUsage()
    for _ in range(20):
        usage.add_brief_research_run(USER, DAY_ONE)

    assert (
        await _limiter(usage, brief_research=3).brief_research_capacity_available(
            user_id=USER, is_admin=True
        )
        is True
    )
    assert (
        await _limiter(
            usage, brief_research=3, now=DAY_TWO
        ).brief_research_capacity_available(user_id=USER, is_admin=False)
        is True
    )


@pytest.mark.anyio
async def test_brief_research_cap_is_per_account() -> None:
    usage = _FakeUsage()
    for _ in range(3):
        usage.add_brief_research_run(USER, DAY_ONE)

    limiter = _limiter(usage, brief_research=3)
    assert (
        await limiter.brief_research_capacity_available(user_id=USER, is_admin=False)
        is False
    )
    assert (
        await limiter.brief_research_capacity_available(user_id=OTHER, is_admin=False)
        is True
    )


# --------------------------------------------------------------------------- #
# The Beat cap (AL-522, Phase 6 TDD §7/D14) — ``check_beat_creation``. Unlike
# every counter above, it is a **stock** cap (the count of live Beats a
# learner currently holds), not a daily flow: no ``since`` window, and it
# DOES raise (unlike the non-raising research cap right above it) — it is
# the one 429 ``POST /beats`` can produce.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_beat_creation_allows_up_to_cap_then_denies() -> None:
    usage = _FakeUsage()
    limiter = _limiter(usage, beats=3)

    for count in range(3):
        await limiter.check_beat_creation(user_id=USER, is_admin=False)
        usage.set_beat_count(USER, count + 1)

    with pytest.raises(HTTPException) as excinfo:
        await limiter.check_beat_creation(user_id=USER, is_admin=False)
    assert excinfo.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.anyio
async def test_beat_cap_is_disabled_at_the_default_of_zero() -> None:
    usage = _FakeUsage()
    usage.set_beat_count(USER, 50)  # far over any real cap

    await _limiter(usage, beats=0).check_beat_creation(user_id=USER, is_admin=False)


@pytest.mark.anyio
async def test_beat_cap_exempts_admins() -> None:
    usage = _FakeUsage()
    usage.set_beat_count(USER, 50)

    await _limiter(usage, beats=3).check_beat_creation(user_id=USER, is_admin=True)


@pytest.mark.anyio
async def test_beat_cap_is_per_account() -> None:
    usage = _FakeUsage()
    usage.set_beat_count(USER, 3)

    limiter = _limiter(usage, beats=3)
    with pytest.raises(HTTPException):
        await limiter.check_beat_creation(user_id=USER, is_admin=False)
    # A different account has its own allowance.
    await limiter.check_beat_creation(user_id=OTHER, is_admin=False)


@pytest.mark.anyio
async def test_beat_cap_never_windows_by_day() -> None:
    """The load-bearing difference from every other cap in this file: a
    **stock** count is never reset by the UTC-day rollover ``_limiter``'s
    ``now`` drives everywhere else, because it has no ``since`` at all —
    only deleting a Beat frees quota."""
    usage = _FakeUsage()
    usage.set_beat_count(USER, 3)

    with pytest.raises(HTTPException):
        await _limiter(usage, beats=3, now=DAY_ONE).check_beat_creation(
            user_id=USER, is_admin=False
        )
    # A "new day" changes nothing: the count is still 3 live Beats.
    with pytest.raises(HTTPException):
        await _limiter(usage, beats=3, now=DAY_TWO).check_beat_creation(
            user_id=USER, is_admin=False
        )

    # Only "deleting" a Beat (the count going down) frees quota.
    usage.set_beat_count(USER, 2)
    await _limiter(usage, beats=3, now=DAY_TWO).check_beat_creation(
        user_id=USER, is_admin=False
    )


@pytest.mark.anyio
async def test_beat_cap_friendly_429_message_names_the_cap() -> None:
    usage = _FakeUsage()
    usage.set_beat_count(USER, 3)

    with pytest.raises(HTTPException) as excinfo:
        await _limiter(usage, beats=3).check_beat_creation(user_id=USER, is_admin=False)
    detail = excinfo.value.detail
    assert isinstance(detail, str)
    assert "3" in detail
