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

**Tutor messages (``check_tutor_message``, AL-220 / Phase 2 §7, D8).** The tutor
cap is the same shape one level down: ``RATE_LIMIT_TUTOR_MESSAGES_PER_DAY``
counts the learner's **live** learner-message rows created today
(``UsageRepository.count_tutor_messages_since``). It ships at **0 — disabled** —
so on the default configuration the count is never queried. It inherits this
module's stateless-counting quirks and adds the one PRD §5.7 names: "new
conversation" deletes the rows, so clearing a thread refunds quota. Recorded,
not fixed — the refund-proof append-only usage table is the precondition for
ever raising the cap above 0, not draft-1 work.

**Shaping messages (``check_shaping_message``, AL-320 / Phase 2B §7).** The same
shape again, one rail across: ``RATE_LIMIT_SHAPING_MESSAGES_PER_DAY`` counts the
learner's live **shaping** learner-message rows created today
(``UsageRepository.count_shaping_messages_since``) and ships at **0 — disabled**,
the 2A posture verbatim. Its own cap rather than a share of the tutor's, because
the two rails are separately flag-gated and separately killable; the tutor's
counter gained a conversation-kind filter in the same change so that a shaping
turn cannot spend the tutor's budget. Applied **Additions** need no limiter of
their own — the lessons they add are ordinary generations under
``RATE_LIMIT_LESSON_GENERATIONS_PER_DAY``, and ``MAX_LESSONS_PER_PATH`` bounds
path size at proposal *and* apply time.

**Flashcard drafting (``check_flashcard_draft_generation``, Phase 3 TDD §5.2/
D13).** ``FLASHCARD_DRAFTS_PER_DAY`` counts ``user_id``'s **drafting attempts**
today — ``UsageRepository.count_flashcard_draft_runs_since``, keyed on
``flashcard_draft_runs.started_at``, the stamp a claim (re-)writes. This is the
same shape as ``check_outline_generation``'s cap, not
``count_lesson_generations_since``'s: drafting inserts no new row on a retry (a
sparse, one-row-per-lesson claim, TDD D7), so only the *stamp* moves, and only
the row's **latest** claim is counted — a same-lesson `failed` -> retry loop
still consumes one quota unit, the same accepted MVP shape
``count_path_outline_generations_since``'s docstring already names. Call before
:meth:`~aleph.services.flashcard_drafting.FlashcardDraftingService.trigger_draft_run`
(``routers/v1/flashcards.py``'s `POST .../flashcard-drafts`), **after** the
ownership/`409 lesson_not_generated` checks and **before** the claim (§5.6: a
breach must not spend a claim attempt). Ships **enabled** (default 50, not 0) —
unlike the tutor/shaping caps, drafting is this phase's one learner-triggered
model call (D13), so there is no "cap is 0 so this is never queried" posture
here; the family's own off-switch (``cap <= 0``) is still honoured, per
:class:`DailyRateLimiter`'s own docstring.

**Beat research (``brief_research_capacity_available``, Phase 6 TDD D14).**
``RATE_LIMIT_BRIEF_RESEARCH_PER_DAY`` counts the learner's Beats with a
research attempt today (``UsageRepository.count_brief_research_runs_since``,
keyed on ``beats.research_started_at``, the stamp a claim (re-)writes) — the
same shape as ``check_flashcard_draft_generation``'s cap, not
``check_lesson_generation``'s: a retry re-stamps rather than inserting, so a
same-Beat retry loop still counts once. **Ships enabled** (default 5).
Unlike every other check in this module, this one is **non-raising**: the
arrival drain (``services/briefing.py::BriefingService.drain_claimable``,
TDD §5.6/§7) runs inside a ``GET`` the learner did not ask to be billed for,
so hitting the cap must degrade to "no research this time", never a ``429``
on the beats list. Admins are exempt, matching every other cap here.

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

    from aleph.config import Settings


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

    async def count_tutor_messages_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int: ...

    async def count_shaping_messages_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int: ...

    async def count_flashcard_draft_runs_since(
        self, *, user_id: uuid.UUID, since: datetime
    ) -> int: ...

    async def count_brief_research_runs_since(
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
        tutor_messages_per_day: int,
        shaping_messages_per_day: int = 0,
        flashcard_drafts_per_day: int = 0,
        brief_research_per_day: int = 0,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._usage = usage
        self._paths_per_day = paths_per_day
        self._lesson_generations_per_day = lesson_generations_per_day
        self._tutor_messages_per_day = tutor_messages_per_day
        self._shaping_messages_per_day = shaping_messages_per_day
        self._flashcard_drafts_per_day = flashcard_drafts_per_day
        self._brief_research_per_day = brief_research_per_day
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

    async def check_tutor_message(self, *, user_id: uuid.UUID, is_admin: bool) -> None:
        """Raise ``HTTPException(429)`` if ``user_id`` is at the daily tutor cap.

        Call before admitting a tutor turn (Phase 2 §7 / D8). **Ships disabled**:
        ``RATE_LIMIT_TUTOR_MESSAGES_PER_DAY`` defaults to 0, which
        :meth:`_exempt` already reads as "no cap", so the count is never even
        queried on the default configuration. The knob exists; the behaviour
        does not.

        The count is over **live learner-message rows** — the exact Phase 1
        pattern, quirks included. The known one is the PRD §5.7 quirk: "new
        conversation" deletes those rows, so clearing a thread refunds quota.
        That is **recorded, not fixed** (D8): while the cap is 0 the count is
        never consulted, so a refund-proof append-only usage table would be
        machinery for a disabled feature. Building it is the *precondition* for
        ever raising this cap above 0.
        """
        await self._check(
            self._usage.count_tutor_messages_since,
            cap=self._tutor_messages_per_day,
            user_id=user_id,
            is_admin=is_admin,
            message=(
                f"You've reached today's limit of {self._tutor_messages_per_day} "
                "tutor questions. Please try again tomorrow."
            ),
        )

    async def check_shaping_message(
        self, *, user_id: uuid.UUID, is_admin: bool
    ) -> None:
        """Raise ``HTTPException(429)`` if ``user_id`` is at the daily shaping cap.

        Call before admitting a shaping turn (Phase 2B §7). **Ships disabled**,
        the 2A posture verbatim: ``RATE_LIMIT_SHAPING_MESSAGES_PER_DAY`` defaults
        to 0, which :meth:`_exempt` reads as "no cap", so the count is never
        queried on the default configuration. The knob exists; the behaviour does
        not.

        Its own cap and its own count, deliberately not a share of the tutor's:
        the two rails are separately flag-gated and separately killable, so a
        shaping burst must not be able to close the in-lesson tutor. Both counts
        are over **live** learner-message rows and both carry the same recorded
        quirk — "new conversation" deletes those rows, so clearing a thread
        refunds quota, which is the precondition for ever raising either cap.
        """
        await self._check(
            self._usage.count_shaping_messages_since,
            cap=self._shaping_messages_per_day,
            user_id=user_id,
            is_admin=is_admin,
            message=(
                f"You've reached today's limit of {self._shaping_messages_per_day} "
                "shaping messages. Please try again tomorrow."
            ),
        )

    async def check_flashcard_draft_generation(
        self, *, user_id: uuid.UUID, is_admin: bool
    ) -> None:
        """Raise ``HTTPException(429)`` if ``user_id`` is at the daily drafting cap.

        Call before triggering a drafting run (``POST
        /lessons/{id}/flashcard-drafts``, TDD §5.2/§5.6), after the
        ownership/generation checks and before the claim — see this module's
        docstring for what the count actually bounds (distinct lessons with a
        drafting attempt today, same-lesson retries counted once).
        """
        await self._check(
            self._usage.count_flashcard_draft_runs_since,
            cap=self._flashcard_drafts_per_day,
            user_id=user_id,
            is_admin=is_admin,
            message=(
                f"You've reached today's limit of "
                f"{self._flashcard_drafts_per_day} flashcard drafting requests. "
                "Please try again tomorrow."
            ),
        )

    async def brief_research_capacity_available(
        self, *, user_id: uuid.UUID, is_admin: bool
    ) -> bool:
        """Whether ``user_id`` may claim another Beat's research run today (D14).

        **Non-raising**, unlike every other check in this class — see the
        module docstring's "Beat research" section for why: the arrival drain
        runs inside a read the learner did not ask to be billed for, so
        hitting this cap must degrade to "no research this time" rather than
        a ``429`` on the beats list. Admins are exempt, matching every other
        cap; a cap of 0 or negative disables it (returns ``True`` always),
        the same ``_exempt`` convention every other check here uses.
        """
        if self._exempt(self._brief_research_per_day, is_admin=is_admin):
            return True
        used = await self._usage.count_brief_research_runs_since(
            user_id=user_id, since=_start_of_utc_day(self._now())
        )
        return used < self._brief_research_per_day

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


def build_daily_rate_limiter(
    session: AsyncSession, *, config: Settings = settings
) -> DailyRateLimiter:
    """Construct a limiter from settings, wired to the Postgres row counter.

    The single entry point AL-050/051 call from their route handlers::

        limiter = build_daily_rate_limiter(session)
        await limiter.check_path_creation(user_id=user.id, is_admin=is_admin)

    Kept here (not in a router) so the caps come from one place and the DB
    seam is not re-derived per call site. ``config`` defaults to the global
    settings (every existing call site's behaviour, unchanged); a caller that
    injects its own ``Settings`` (``services/briefing.py``'s ``BriefingService``,
    Phase 6 TDD §7 — the drain must honour a test's/admin's overridden caps,
    not read the module-global ones behind the injected config's back) gets a
    limiter built from that instead.
    """
    return DailyRateLimiter(
        UsageRepository(session),
        paths_per_day=config.rate_limit_paths_per_day,
        lesson_generations_per_day=config.rate_limit_lesson_generations_per_day,
        tutor_messages_per_day=config.rate_limit_tutor_messages_per_day,
        shaping_messages_per_day=config.rate_limit_shaping_messages_per_day,
        flashcard_drafts_per_day=config.flashcard_drafts_per_day,
        brief_research_per_day=config.rate_limit_brief_research_per_day,
    )
