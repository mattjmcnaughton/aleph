"""Data access for Beats, including the atomic research claim (TDD §5.4/D3).

Every write here is either a plain CRUD op or the two-method claim split
(:meth:`BeatRepository.claim_research` /
:meth:`BeatRepository.claim_research_for_retry`) copied from
``repositories/paths.py``'s ``claim_outline``/``claim_outline_for_retry`` —
the single biggest piece of reuse in the phase (TDD §2). The split matters
*more* here than for paths: under D15 the trigger is arrival, so an
auto-claimable ``failed`` state would mean a retrieval outage bills a fresh
research run on every page load of the beats list. Keeping ``failed`` out of
the auto predicate (:data:`_CLAIMABLE_STATES`) is how that holds — the same
"a systematically failing generation is not retry-burned" rule
``repositories/paths.py`` states, one column over.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, delete, func, select, update

from aleph.config import settings
from aleph.models import Beat, BeatResearchRun, BeatResearchState
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

# States a research claim may transition into ``researching`` (TDD D3). A
# fresh ``idle`` Beat, or a ``researching`` one whose process died (stale).
# ``failed`` is excluded from the auto predicate on purpose (D3's whole
# argument): only the explicit learner retry re-claims a real failure, so a
# retrieval outage never silently bills a fresh research run on every arrival.
# ``refused`` is terminal and never appears in either tuple.
_CLAIMABLE_STATES = (BeatResearchState.IDLE,)
_RETRY_CLAIMABLE_STATES = (BeatResearchState.IDLE, BeatResearchState.FAILED)


class BeatRepository:
    """Data access for :class:`~aleph.models.Beat` rows.

    Constructed per-request with the caller's :class:`AsyncSession` (habagou
    convention); the repository never opens or commits transactions — the
    service layer owns the unit of work. ``stale_after_seconds`` is the
    research stale window (AL-501's ``brief_research_stale_after_seconds``);
    it defaults to the configured value but a service may inject a different
    policy, mirroring ``PathRepository``.
    """

    def __init__(
        self, session: AsyncSession, *, stale_after_seconds: float | None = None
    ) -> None:
        self.session = session
        self._stale_after_seconds = (
            stale_after_seconds
            if stale_after_seconds is not None
            else settings.brief_research_stale_after_seconds
        )

    def _effective_research_state_expr(self) -> ColumnElement[str]:
        return effective_state_case(
            state_col=Beat.research_state,
            started_at_col=Beat.research_started_at,
            generating_state=BeatResearchState.RESEARCHING,
            failed_state=BeatResearchState.FAILED,
            stale_after_seconds=self._stale_after_seconds,
        )

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        topic: str,
        level: Level,
        anchor_weekday: int,
        guidance: str | None = None,
        model_research: str | None = None,
        model_brief: str | None = None,
    ) -> Beat:
        """Insert an ``idle`` Beat.

        ``topic``/``guidance``/``level``/``anchor_weekday`` are the standing
        orders (CONTEXT.md: Beat), frozen at deployment exactly as a path's
        generation inputs are — no route ever writes them after ``create``.
        ``model_research``/``model_brief`` carry an admin's picker overrides
        (TDD D7, §5.3), already validated at the route; ``None`` means "use
        the configured slot".
        """
        beat = Beat(
            user_id=user_id,
            topic=topic,
            level=level,
            anchor_weekday=anchor_weekday,
            guidance=guidance,
            model_research=model_research,
            model_brief=model_brief,
        )
        self.session.add(beat)
        await self.session.flush()
        return beat

    async def get(self, beat_id: uuid.UUID) -> Beat | None:
        return await self.session.get(Beat, beat_id)

    async def list_for_user(
        self, *, user_id: uuid.UUID, limit: int | None = None
    ) -> list[Beat]:
        """A learner's Beats, oldest first (deployment order).

        The arrival drain's read (TDD §5.6/D15): ``limit`` is the service's
        own bound (``MAX_BEATS_PER_LEARNER``, D14) applied here rather than
        trusted to already hold — a stock cap enforced at creation time can
        still be raced or hand-edited, and this is the one query the drain
        iterates per arrival, so it stays bounded regardless.
        """
        query = (
            select(Beat)
            .where(Beat.user_id == user_id)
            .order_by(Beat.created_at, Beat.id)
        )
        if limit is not None:
            query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars())

    async def list_claim_eligible_for_user(
        self, *, user_id: uuid.UUID, limit: int | None = None
    ) -> list[Beat]:
        """A learner's Beats whose research state could possibly be claimed
        right now — code-review FIX 3 on AL-521.

        Filters **at the query level** to :data:`_CLAIMABLE_STATES` (``idle``)
        or a stale ``researching`` row — the identical predicate
        :meth:`claim_research`'s own ``UPDATE`` enforces — so a Beat already
        ``researching`` (a run genuinely in flight), ``failed``, or
        ``refused`` is excluded from the arrival drain's cadence evaluation
        **entirely**, never merely rejected after a wasted claim attempt.

        This is what stops two failure modes the drain would otherwise hit on
        every arrival: a run in flight being re-evaluated (and, before FIX 1,
        re-spawned) on every poll while `lib/polling.ts` checks back every
        2-5s over a run that can take minutes, and a permanently
        ``failed``/``refused`` Beat being re-evaluated on every beats-list
        ``GET`` forever, since neither state's cadence floor ever stops being
        satisfied.

        The arrival drain (``services/briefing.py::BriefingService.
        drain_claimable``) is this method's only caller; :meth:`list_for_user`
        (unfiltered, every state) remains what a Beat *list/detail* read uses
        to render the learner's actual Beats regardless of research state.
        """
        query = (
            select(Beat)
            .where(
                Beat.user_id == user_id,
                claimable_predicate(
                    state_col=Beat.research_state,
                    started_at_col=Beat.research_started_at,
                    claimable_states=_CLAIMABLE_STATES,
                    generating_state=BeatResearchState.RESEARCHING,
                    stale_after_seconds=self._stale_after_seconds,
                ),
            )
            .order_by(Beat.created_at, Beat.id)
        )
        if limit is not None:
            query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars())

    async def effective_research_state(
        self, beat_id: uuid.UUID
    ) -> BeatResearchState | None:
        """The Beat's **effective** research state: a stale ``researching``
        reads as ``failed`` — the ``PathRepository.effective_status`` shape,
        so the detail poll reports the retry affordance rather than a dead
        spinner when a run crashed mid-flight (D5's stale recovery).
        """
        result = await self.session.execute(
            select(self._effective_research_state_expr()).where(Beat.id == beat_id)
        )
        value = result.scalar_one_or_none()
        return BeatResearchState(value) if value is not None else None

    async def delete(self, beat_id: uuid.UUID) -> bool:
        """Hard-delete a Beat; ON DELETE CASCADE tears down Briefs -> Sources.

        This is also how standing orders change (CONTEXT.md: Beat — delete
        and redeploy). Returns whether a row was removed.
        """
        result = await self.session.execute(delete(Beat).where(Beat.id == beat_id))
        return affected_rows(result) > 0

    async def claim_research(self, beat_id: uuid.UUID) -> datetime.datetime | None:
        """Atomically claim a Beat's research run (auto/arrival path, D15).

        Wins iff the row is currently ``idle`` or a stale ``researching`` (a
        crashed run) — never a real ``failed`` (D3's whole point: an auto
        claim must not retry-burn a systematically failing Beat on every
        arrival). The ``UPDATE ... WHERE ... RETURNING`` is the whole
        concurrency control, exactly as ``PathRepository.claim_outline``:
        exactly one caller matches the predicate and flips the row under
        Postgres' row lock; every other caller matches nothing.

        Returns the **fencing token** (the ``research_started_at`` stamp this
        claim wrote) on a win, or ``None`` if another caller already holds it.

        **Commit immediately** — the same requirement as
        ``PathRepository.claim_outline``: the row lock is held to commit, and
        a claim left open in a long transaction both blocks competitors and
        freezes the stale clock.

        Callers: ``services/briefing.py::BriefingService.drain_claimable``
        calls this directly on the request's own session, synchronously,
        **before** spawning the research task (code-review FIX 1 on AL-521) —
        so a claim is committed and visible to the request's own subsequent
        read before any background work has even started. ``run_research``
        also self-claims through this method when called with no pre-claimed
        fence (a direct call, e.g. a future retry-driven pipeline entry).
        """
        return await self._claim(beat_id, _CLAIMABLE_STATES)

    async def claim_research_for_retry(
        self, beat_id: uuid.UUID
    ) -> datetime.datetime | None:
        """Atomically claim for an explicit learner retry (POST .../retry).

        Same as :meth:`claim_research` (fencing token, commit-immediately) but
        additionally re-claims a ``failed`` row — the learner's retry is the
        only loop that re-runs a real failure.
        """
        return await self._claim(beat_id, _RETRY_CLAIMABLE_STATES)

    async def _claim(
        self, beat_id: uuid.UUID, states: tuple[BeatResearchState, ...]
    ) -> datetime.datetime | None:
        result = await self.session.execute(
            update(Beat)
            .where(
                Beat.id == beat_id,
                claimable_predicate(
                    state_col=Beat.research_state,
                    started_at_col=Beat.research_started_at,
                    claimable_states=states,
                    generating_state=BeatResearchState.RESEARCHING,
                    stale_after_seconds=self._stale_after_seconds,
                ),
            )
            # updated_at bumped explicitly: a Core UPDATE bypasses the ORM
            # onupdate hook (AL-010 landmine). research_error/refusal_message
            # cleared defensively, mirroring PathRepository._claim's
            # refusal_message reset: no claimable state currently carries
            # either, but clearing keeps the claim the single writer that
            # resets stale research fields.
            .values(
                research_state=BeatResearchState.RESEARCHING,
                research_started_at=func.now(),
                research_error=None,
                refusal_message=None,
                updated_at=func.now(),
            )
            .returning(Beat.research_started_at, Beat.user_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        fence, user_id = row
        # Code-review FIX 2 on AL-521: one row per WON claim, in the SAME
        # transaction as the UPDATE above — the daily research cap
        # (``UsageRepository.count_brief_research_runs_since``) counts THIS
        # table, never ``beats`` rows, because a claim overwrites
        # ``beats.research_started_at`` on every (re-)claim and a learner's
        # Beat count sits well below the daily cap (see
        # ``models/beat_research_run.py``).
        self.session.add(
            BeatResearchRun(beat_id=beat_id, user_id=user_id, started_at=fence)
        )
        await self.session.flush()
        return fence

    # -- transitions out of a won claim (TDD §5.6/§5.7; ticket AL-521) -------- #
    #
    # Mirrors ``PathRepository.mark_ready``/``mark_failed``/``mark_refused``
    # exactly: every write here is guarded by the claim fence (``research_state
    # == RESEARCHING AND research_started_at == fence``), so a stalled worker
    # that lost its claim to a fresh re-claim cannot overwrite it. Each returns
    # whether *this* call still owned the claim.

    async def mark_idle(self, beat_id: uuid.UUID, *, fence: datetime.datetime) -> bool:
        """Return the Beat to ``idle`` after a resolved run (published or
        skipped) — ready to report again next Anchor day (D3's asymmetry with
        ``PathStatus``: a Beat's success is not terminal)."""
        return await self._guarded_set_state(
            beat_id, BeatResearchState.IDLE, fence, research_error=None
        )

    async def mark_failed(
        self, beat_id: uuid.UUID, *, fence: datetime.datetime, error: str
    ) -> bool:
        """Record a research failure (retryable). Fenced like :meth:`mark_idle`."""
        return await self._guarded_set_state(
            beat_id, BeatResearchState.FAILED, fence, research_error=error
        )

    async def mark_refused(
        self, beat_id: uuid.UUID, *, fence: datetime.datetime, message: str
    ) -> bool:
        """Record a refusal (terminal, PRD §2's safety branch). Fenced like
        :meth:`mark_idle`."""
        result = await self.session.execute(
            update(Beat)
            .where(
                Beat.id == beat_id,
                Beat.research_state == BeatResearchState.RESEARCHING,
                Beat.research_started_at == fence,
            )
            .values(
                research_state=BeatResearchState.REFUSED,
                refusal_message=message,
                updated_at=func.now(),
            )
        )
        return affected_rows(result) > 0

    async def _guarded_set_state(
        self,
        beat_id: uuid.UUID,
        state: BeatResearchState,
        fence: datetime.datetime,
        *,
        research_error: str | None,
    ) -> bool:
        result = await self.session.execute(
            update(Beat)
            .where(
                Beat.id == beat_id,
                Beat.research_state == BeatResearchState.RESEARCHING,
                Beat.research_started_at == fence,
            )
            .values(
                research_state=state,
                research_error=research_error,
                updated_at=func.now(),
            )
        )
        return affected_rows(result) > 0
