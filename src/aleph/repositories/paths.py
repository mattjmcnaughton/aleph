"""Data access for paths, including the atomic outline claim (TDD §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, delete, exists, func, or_, select, update

from aleph.config import settings
from aleph.models import Lesson, LessonGenerationState, Path, PathStatus
from aleph.repositories._generation import (
    affected_rows,
    claimable_predicate,
    effective_state_case,
)

if TYPE_CHECKING:
    import datetime
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.models import Level

# Statuses an outline claim may transition into ``generating`` (TDD §5.4). A
# fresh ``pending`` path, or a ``generating`` one whose process died (stale).
# ``failed`` is excluded here on purpose: only the explicit learner retry
# re-claims a real failure, so a systematically failing outline never silently
# retry-burns spend. ``ready`` and ``refused`` are terminal.
_CLAIMABLE_STATUSES = (PathStatus.PENDING,)
_RETRY_CLAIMABLE_STATUSES = (PathStatus.PENDING, PathStatus.FAILED)


class PathRepository:
    """Data access for :class:`~aleph.models.Path` rows.

    Constructed per-request with the caller's :class:`AsyncSession` (habagou
    convention); the repository never opens or commits transactions — the
    service layer owns the unit of work. ``stale_after_seconds`` is the
    generation stale window (TDD §5.4); it defaults to the configured value but
    a service may inject a different policy (AL-040 wiring point).
    """

    def __init__(
        self, session: AsyncSession, *, stale_after_seconds: float | None = None
    ) -> None:
        self.session = session
        self._stale_after_seconds = (
            stale_after_seconds
            if stale_after_seconds is not None
            else settings.generation_stale_after_seconds
        )

    def _effective_status_expr(self) -> ColumnElement[str]:
        return effective_state_case(
            state_col=Path.status,
            started_at_col=Path.generation_started_at,
            generating_state=PathStatus.GENERATING,
            failed_state=PathStatus.FAILED,
            stale_after_seconds=self._stale_after_seconds,
        )

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        topic: str,
        level: Level,
        guidance: str | None = None,
        model_outline: str | None = None,
        model_lesson: str | None = None,
    ) -> Path:
        """Insert a ``pending`` path.

        ``guidance`` is the learner's optional free text (CONTEXT.md:
        *Guidance*), stored so the DB-driven resume/reconcile re-runs the
        outline with it (mirrors ``model_outline``/``model_lesson`` below —
        same "persist it on the row" rationale). ``title`` is never a
        ``create`` parameter: every path starts unset, falling back to
        ``topic`` via ``Path.display_title`` until :meth:`set_title` renames it.

        ``model_outline``/``model_lesson`` carry an admin's picker overrides
        (AL-052, §5.3): already validated (admin-only, allowlist-bound) at the
        route, stored so the DB-driven resume/reconcile routes the chosen model.
        ``None`` means "use the configured slot".
        """
        path = Path(
            user_id=user_id,
            topic=topic,
            level=level,
            guidance=guidance,
            model_outline=model_outline,
            model_lesson=model_lesson,
        )
        self.session.add(path)
        await self.session.flush()
        return path

    async def set_title(self, path_id: uuid.UUID, *, title: str) -> None:
        """Rename a path's display label (``PATCH /paths/{id}``).

        A plain, unconditional ``UPDATE`` — renaming is safe at every path
        status (unlike the claim-guarded writes above, there is no fence to
        respect: nothing else ever writes ``title``, so there is no concurrent
        writer to race). ``updated_at`` is bumped explicitly (as every Core
        ``UPDATE`` in this class does): it bypasses the ORM ``onupdate`` hook
        (AL-010 landmine). Like every method here, this does **not** commit;
        the caller (the route) owns the unit of work and must commit it.

        Note for callers holding an ORM instance from before this write: with
        SQLAlchemy's default ``synchronize_session="evaluate"``, an
        ORM-enabled Core ``UPDATE`` like this one *does* patch matching
        objects already in the session's identity map when its ``WHERE``
        criteria are Python-evaluatable (they are here — a literal ``id``
        equality) — so ``path.title`` would already read the new value after
        commit, no refresh needed, in the *current* implementation. The route
        still calls ``session.refresh(path)`` anyway: relying on that
        synchronization detail is fragile (a future non-evaluatable criterion
        silently falls back to no synchronization) and refresh is also how the
        route picks up the DB-side ``updated_at``.
        """
        await self.session.execute(
            update(Path)
            .where(Path.id == path_id)
            .values(title=title, updated_at=func.now())
        )

    async def get(self, path_id: uuid.UUID) -> Path | None:
        return await self.session.get(Path, path_id)

    async def get_for_user(
        self, *, path_id: uuid.UUID, user_id: uuid.UUID
    ) -> Path | None:
        """Fetch a path only if it belongs to ``user_id`` (ownership guard)."""
        result = await self.session.execute(
            select(Path).where(Path.id == path_id, Path.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, *, user_id: uuid.UUID) -> list[Path]:
        result = await self.session.execute(
            select(Path)
            .where(Path.user_id == user_id)
            .order_by(Path.created_at.desc(), Path.id)
        )
        return list(result.scalars())

    async def list_for_user_with_effective_status(
        self, *, user_id: uuid.UUID
    ) -> list[tuple[Path, PathStatus]]:
        """A learner's paths (**most recently worked first**) with effective status.

        The switcher list (``GET /paths``, §6) reports the same effective status
        the detail poll does (a stale ``generating`` reads as ``failed``), so the
        two surfaces never disagree on a crashed outline — computed in SQL here
        (one query) rather than per-row.

        **Ordering is last activity, not creation.** ``created_at`` desc — what
        this used to sort by, and what :meth:`list_for_user` still sorts by —
        ranks paths by when the learner had the *idea*, which after a few weeks
        is precisely not the order they want to resume in: the path they worked
        yesterday sinks under every idea they have had since. The key is the
        path's most recent lesson completion, descending, with paths that have
        never been touched sorted **after** every path that has (``NULLS LAST``,
        spelled explicitly rather than relying on Postgres' desc default, which
        is the opposite for ascending) — a brand-new path still lands at the top
        of that group via the ``created_at`` desc tiebreak, so "I just made
        this" and "I just did this" both stay near the top.
        """
        last_activity = (
            select(func.max(Lesson.completed_at))
            .where(Lesson.path_id == Path.id)
            .correlate(Path)
            .scalar_subquery()
        )
        result = await self.session.execute(
            select(Path, self._effective_status_expr().label("effective_status"))
            .where(Path.user_id == user_id)
            .order_by(
                last_activity.desc().nullslast(),
                Path.created_at.desc(),
                Path.id,
            )
        )
        return [(path, PathStatus(status)) for path, status in result.all()]

    async def effective_status(self, path_id: uuid.UUID) -> PathStatus | None:
        """The path's **effective** status: a stale ``generating`` reads as failed.

        The §6 poll target (``GET /paths/{id}``) reports status; a path whose
        outline run crashed mid-flight (stale ``generating``) must read as
        ``failed`` so the learner sees the retry affordance rather than a dead
        spinner — the same stale rule readers apply to lessons (§5.4/§6), sharing
        the one clock.
        """
        result = await self.session.execute(
            select(self._effective_status_expr()).where(Path.id == path_id)
        )
        value = result.scalar_one_or_none()
        return PathStatus(value) if value is not None else None

    async def ids_needing_reconciliation(self) -> list[uuid.UUID]:
        """Path ids the reconciler should re-drive this tick (TDD §5.4 D6).

        A single scan for **claimable work**, the over-approximation the
        idempotent driver then makes precise:

        * **outline-level** — a ``pending`` path (its spawn may have been lost to
          a crash/deploy and no live task holds it) or a **stale** ``generating``
          one (a crashed outline run), via the shared claim predicate.
        * **lesson-level** — a ``ready`` path that still has an ``ungenerated``
          lesson (an unfilled prefetch window) or a **stale** ``generating`` one
          (a crashed lesson run).

        This is a deliberate over-approximation: it may select a ready path whose
        window is already filled (ungenerated lessons all sit *beyond* the
        window) or one blocked by a real ``failed`` lesson. ``resume_path`` /
        ``ensure_prefetch_window`` handle both precisely and cheaply — the window
        computation stops at the window edge, and the serial walk stops at a real
        ``failed`` (never retry-burning it, §5.4). A row in a **real** ``failed``
        state is never selected on its own (``failed`` is absent from the auto
        claim predicate), so a systematically failing generation is not
        retry-burned by the reconciler — only the learner's explicit retry loops.
        """
        stale = self._stale_after_seconds
        outline_claimable = claimable_predicate(
            state_col=Path.status,
            started_at_col=Path.generation_started_at,
            claimable_states=_CLAIMABLE_STATUSES,
            generating_state=PathStatus.GENERATING,
            stale_after_seconds=stale,
        )
        lesson_work = claimable_predicate(
            state_col=Lesson.generation_state,
            started_at_col=Lesson.generation_started_at,
            claimable_states=(LessonGenerationState.UNGENERATED,),
            generating_state=LessonGenerationState.GENERATING,
            stale_after_seconds=stale,
        )
        ready_with_work = (Path.status == PathStatus.READY) & exists().where(
            Lesson.path_id == Path.id, lesson_work
        )
        result = await self.session.execute(
            select(Path.id)
            .where(or_(outline_claimable, ready_with_work))
            .order_by(Path.created_at, Path.id)
        )
        return list(result.scalars())

    async def delete(self, path_id: uuid.UUID) -> bool:
        """Hard-delete a path; ON DELETE CASCADE tears down its whole tree.

        Returns whether a row was removed.
        """
        result = await self.session.execute(delete(Path).where(Path.id == path_id))
        return affected_rows(result) > 0

    async def claim_outline(self, path_id: uuid.UUID) -> datetime.datetime | None:
        """Atomically claim a path's outline generation (auto path).

        Wins iff the row is currently ``pending`` or a stale ``generating`` (a
        crashed run). The ``UPDATE ... WHERE ... RETURNING`` is the whole
        concurrency control: exactly one caller matches the predicate and flips
        the row to ``generating`` under Postgres' row lock; every other caller
        sees the already-claimed fresh row and matches nothing.

        Returns the **fencing token** (the ``generation_started_at`` stamp this
        claim wrote) on a win, or ``None`` if another caller already holds it —
        pass it back to ``mark_*`` so a stalled worker cannot overwrite a fresh
        re-claim.

        **Commit immediately.** ``generation_started_at`` is ``func.now()`` = the
        transaction start timestamp and the row lock is held until commit, so a
        claim left open in a long transaction blocks competitors and freezes the
        stale clock. A short claim-then-commit transaction is load-bearing
        (TDD §5.4).
        """
        return await self._claim(path_id, _CLAIMABLE_STATUSES)

    async def claim_outline_for_retry(
        self, path_id: uuid.UUID
    ) -> datetime.datetime | None:
        """Atomically claim for an explicit learner retry (POST .../retry).

        Same as :meth:`claim_outline` (including the fencing-token return and the
        commit-immediately requirement) but additionally re-claims a ``failed``
        row — the learner's retry is the only loop that re-runs a real failure.
        """
        return await self._claim(path_id, _RETRY_CLAIMABLE_STATUSES)

    async def _claim(
        self, path_id: uuid.UUID, statuses: tuple[PathStatus, ...]
    ) -> datetime.datetime | None:
        result = await self.session.execute(
            update(Path)
            .where(
                Path.id == path_id,
                claimable_predicate(
                    state_col=Path.status,
                    started_at_col=Path.generation_started_at,
                    claimable_states=statuses,
                    generating_state=PathStatus.GENERATING,
                    stale_after_seconds=self._stale_after_seconds,
                ),
            )
            # updated_at is bumped explicitly: a Core UPDATE bypasses the ORM
            # ``onupdate`` hook (AL-010 landmine). ``refusal_message=None`` is
            # defensive symmetry with the lesson claim's ``generation_error``
            # clear: no claimable status currently carries a refusal_message
            # (only terminal ``refused`` does), but clearing keeps the claim the
            # single writer that resets stale generation fields.
            .values(
                status=PathStatus.GENERATING,
                generation_started_at=func.now(),
                refusal_message=None,
                updated_at=func.now(),
            )
            .returning(Path.generation_started_at)
        )
        return result.scalar_one_or_none()

    async def mark_ready(self, path_id: uuid.UUID, *, fence: datetime.datetime) -> bool:
        """Mark the outline ready (terminal-ish; retryable only via ``failed``).

        Guarded by the claim fence: writes only while the row is still
        ``generating`` with ``generation_started_at == fence``. Returns whether
        this caller still owned the claim (``False`` = lost, no-op).
        """
        return await self._guarded_set_status(path_id, PathStatus.READY, fence)

    async def mark_failed(
        self, path_id: uuid.UUID, *, fence: datetime.datetime
    ) -> bool:
        """Record an outline failure (retryable). Fenced like :meth:`mark_ready`."""
        return await self._guarded_set_status(path_id, PathStatus.FAILED, fence)

    async def mark_refused(
        self, *, path_id: uuid.UUID, message: str, fence: datetime.datetime
    ) -> bool:
        """Record a refusal (terminal, W7). Fenced like :meth:`mark_ready`."""
        result = await self.session.execute(
            update(Path)
            .where(
                Path.id == path_id,
                Path.status == PathStatus.GENERATING,
                Path.generation_started_at == fence,
            )
            .values(
                status=PathStatus.REFUSED,
                refusal_message=message,
                updated_at=func.now(),
            )
        )
        return affected_rows(result) > 0

    async def _guarded_set_status(
        self, path_id: uuid.UUID, status: PathStatus, fence: datetime.datetime
    ) -> bool:
        result = await self.session.execute(
            update(Path)
            .where(
                Path.id == path_id,
                Path.status == PathStatus.GENERATING,
                Path.generation_started_at == fence,
            )
            .values(status=status, updated_at=func.now())
        )
        return affected_rows(result) > 0
