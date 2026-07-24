"""Per-account daily rate limiting for billed generation (TDD §10 / §14 D13).

Cheap insurance on the §7 cost guardrail: a learner may create at most
``RATE_LIMIT_PATHS_PER_DAY`` paths and trigger at most
``RATE_LIMIT_LESSON_GENERATIONS_PER_DAY`` lesson generations per calendar day.
The limiter is *stateless* — it counts the learner's real rows created since the
start of the current day rather than keeping an in-memory counter (see
``repositories.usage``). Counting persisted rows is why the cap survives process
restarts and needs no shared store across processes, unlike an in-memory window.

**Day boundary is UTC.** ``created_at`` / ``generation_started_at`` are stored
timezone-aware in UTC, so "today" is the span from the most recent UTC midnight
to now. CONTEXT.md's *Day* (the learner's local calendar day) is a *metrics*
notion; a spend guardrail wants a single unambiguous server-side boundary, so
UTC is deliberate here.

Accepted limitations of stateless counting (fine for cheap insurance, not a
tight quota): the check-then-insert window means concurrent requests can
overshoot the cap by the caller's own in-flight concurrency; deleting paths
frees quota (counts derive from live rows, and cascade deletes also erase
lesson stamps); and only each lesson's *latest* claim stamp is counted, so
same-day retries of one lesson consume a single quota unit.

**Outline retries (``check_outline_generation``, AL-050).** ``POST
/paths/{id}/retry`` is a billed trigger that inserts no row, so
``check_path_creation`` cannot bound it (its created-rows count never moves on a
retry). The retry cap instead counts *paths with an outline attempt today*
(``UsageRepository.count_path_outline_generations_since``, keyed on the
re-stamped ``generation_started_at``), reusing ``RATE_LIMIT_PATHS_PER_DAY`` — an
outline attempt is the same billed unit as path creation and §14 defines no
separate retry cap, so one daily outline-attempt budget covers both create and
retry symmetrically. This bounds *cross-path* retry storms at distinct paths per
day. It deliberately does **not** bound a *same-path* retry loop: repeated
retries of one path overwrite its single stamp, so the row counts once. That
loop is accepted for MVP — it is bounded only by claim serialization and client
patience (one outline call runs at a time under the concurrency permit, and the
reconciler never auto-retries a real ``failed`` outline, so nothing re-drives it
without a fresh learner-initiated request).

The check is called *before* the billed work, and admins are exempt via an
injected ``is_admin`` flag (decoupled from ``authz`` on purpose — AL-050 wires
the two together). On refusal it raises ``HTTPException(429, ...)`` with a
friendly message; the app-wide error envelope (``errors.py``) renders it as
``{"error": {code: "rate_limited", ...}}``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from fastapi import HTTPException, status

from aleph.config import settings
from aleph.repositories import UsageRepository

if TYPE_CHECKING:
    import uuid
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession


class UsageCounter(Protocol):
    """The row-counting seam the limiter needs (satisfied by ``UsageRepository``).

    Declared here, next to its only consumer, so tests can supply a small
    in-memory fake instead of a database (fakes over mocks).
    """

    async def count_paths_created_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int: ...

    async def count_path_outline_generations_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int: ...

    async def count_lesson_generations_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _start_of_utc_day(now: datetime) -> datetime:
    """Most recent UTC midnight at/relative to ``now``."""
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class DailyRateLimiter:
    """Enforces per-account daily caps by counting rows since UTC midnight.

    ``now`` is injected (defaults to ``datetime.now(UTC)``) so tests can drive
    the day boundary deterministically. A cap of 0 or negative disables that
    cap (``enabled``-style toggle, habagou parity); admins are always exempt.
    """

    def __init__(
        self,
        usage: UsageCounter,
        *,
        paths_per_day: int,
        lesson_generations_per_day: int,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._usage = usage
        self._paths_per_day = paths_per_day
        self._lesson_generations_per_day = lesson_generations_per_day
        self._now = now

    async def check_path_creation(self, *, user_id: uuid.UUID, is_admin: bool) -> None:
        """Raise ``HTTPException(429)`` if ``user_id`` is at the daily path cap.

        Call before creating a path. Counting existing rows means the check
        passes for creations 1..cap and denies the ``(cap + 1)``-th.
        """
        await self._check(
            self._usage.count_paths_created_since,
            cap=self._paths_per_day,
            user_id=user_id,
            is_admin=is_admin,
            message=(
                f"You've reached today's limit of {self._paths_per_day} new "
                "paths. Please try again tomorrow."
            ),
        )

    async def check_outline_generation(
        self, *, user_id: uuid.UUID, is_admin: bool
    ) -> None:
        """Raise ``HTTPException(429)`` if ``user_id`` is at the daily outline cap.

        Call before triggering an outline retry (``POST /paths/{id}/retry``).
        Reuses the daily path cap (``paths_per_day``): an outline attempt is the
        same billed unit as a path creation, and it counts *paths with an outline
        attempt today* — see this module's docstring for what that bounds (and
        does not).
        """
        await self._check(
            self._usage.count_path_outline_generations_since,
            cap=self._paths_per_day,
            user_id=user_id,
            is_admin=is_admin,
            message=(
                f"You've reached today's limit of {self._paths_per_day} path "
                "generations. Please try again tomorrow."
            ),
        )

    async def check_lesson_generation(
        self, *, user_id: uuid.UUID, is_admin: bool
    ) -> None:
        """Raise ``HTTPException(429)`` if ``user_id`` is at the daily lesson cap.

        Call before triggering a lesson generation.
        """
        await self._check(
            self._usage.count_lesson_generations_since,
            cap=self._lesson_generations_per_day,
            user_id=user_id,
            is_admin=is_admin,
            message=(
                f"You've reached today's limit of "
                f"{self._lesson_generations_per_day} lesson generations. "
                "Please try again tomorrow."
            ),
        )

    async def _check(
        self,
        count_since: Callable[..., Awaitable[int]],
        *,
        cap: int,
        user_id: uuid.UUID,
        is_admin: bool,
        message: str,
    ) -> None:
        if self._exempt(cap, is_admin=is_admin):
            return
        used = await count_since(user_id=user_id, since=_start_of_utc_day(self._now()))
        if used >= cap:
            raise _rate_limited(message)

    @staticmethod
    def _exempt(cap: int, *, is_admin: bool) -> bool:
        """A cap is skipped when disabled (``cap <= 0``) or the caller is admin."""
        return cap <= 0 or is_admin


def _rate_limited(message: str) -> HTTPException:
    """A 429 the error envelope maps to code ``rate_limited`` (see ``app.py``)."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=message,
    )


def build_daily_rate_limiter(session: AsyncSession) -> DailyRateLimiter:
    """Construct a limiter from settings, wired to the Postgres row counter.

    The single entry point AL-050/051 call from their route handlers::

        limiter = build_daily_rate_limiter(session)
        await limiter.check_path_creation(user_id=user.id, is_admin=is_admin)

    Kept here (not in a router) so the caps come from one place and the DB
    seam is not re-derived per call site.
    """
    return DailyRateLimiter(
        UsageRepository(session),
        paths_per_day=settings.rate_limit_paths_per_day,
        lesson_generations_per_day=settings.rate_limit_lesson_generations_per_day,
    )
