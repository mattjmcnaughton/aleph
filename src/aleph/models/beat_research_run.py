"""BeatResearchRun ORM model — an append-only research-claim log.

**Why this table exists (code-review FIX 2 on AL-521, epic #163's correctness
review — not part of the original Phase 6 TDD schema).** The daily research
cap (``services/rate_limit.py::DailyRateLimiter.brief_research_capacity_
available``, TDD D14/D14a) must bound how many research RUNS a learner can
trigger in a day (``RATE_LIMIT_BRIEF_RESEARCH_PER_DAY = 5``). The counter it
used before this table (``UsageRepository.count_brief_research_runs_since``)
counted ``beats`` ROWS whose ``research_started_at`` fell today — but a claim
*overwrites* that single stamp on every (re-)claim, so the count could never
exceed the learner's *Beat* count, which ``MAX_BEATS_PER_LEARNER`` bounds at
3. `3 < 5` unconditionally, so the cap could never fire at production
settings: capacity was available forever, for every learner, once AL-522's
``POST /beats/{id}/retry`` existed to drive repeated claims on the same three
Beats.

One row is inserted every time ``BeatRepository._claim`` **wins** a claim —
both the auto path (``claim_research``) and the explicit retry
(``claim_research_for_retry``), TDD D3 — in the SAME transaction as the
claim's own ``UPDATE`` (``beat_id``/``user_id``/``started_at`` all come off
that statement's own ``RETURNING`` clause, so no second query is needed and
the two writes commit or roll back together). Never updated, and never
deleted by application code — ``ON DELETE CASCADE`` on both foreign keys is
the only way a row disappears (its Beat or its user going away).

**This is NOT TDD D2a's rejected ``beat_runs`` table, revived — it is
narrower on purpose.** D2a considered and rejected a table recording every
research run's *outcome* (published/skipped/failed) as a REPLACEMENT for the
``briefs`` rail read and for Skip-rate metrics; it lost because that would
turn the Beat rail's one indexed ``ORDER BY`` into a join, for a capability
(Skip-rate-as-a-DB-query) nothing would actually use over the Logfire event
the codebase already reads every other rate from. This table serves a
different, narrower purpose: it carries no ``outcome``, no ``kind``, nothing
about what a run *produced* — only that a claim happened, for whom, and when.
Nothing reads it except ``UsageRepository.count_brief_research_runs_since``.
It cannot become a second Beat-rail source because it has none of the columns
a rail read (or Skip rate) would need, and nothing routes it there.
"""

from __future__ import annotations

import uuid  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from datetime import (
    datetime,  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
)

from sqlalchemy import DateTime, ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aleph.db import Base
from aleph.models.base import UUIDAuditMixin


class BeatResearchRun(Base, UUIDAuditMixin):
    """One WON research claim (auto or retry) — the daily cap's own counter.

    Deliberately carries **no** ``relationship()`` attributes (the
    ``models/flashcard.py`` precedent every other model in this phase
    follows): the only read this table serves is a scoped ``COUNT`` in
    ``repositories/usage.py``.
    """

    __tablename__ = "beat_research_runs"
    __table_args__ = (
        # The count query's own index: `WHERE user_id = ? AND started_at >= ?`.
        Index("ix_beat_research_runs_user_id_started_at", "user_id", "started_at"),
        # Supports the cascade delete's own lookup when a Beat is removed.
        Index("ix_beat_research_runs_beat_id", "beat_id"),
    )

    beat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("beats.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The claim's own fencing stamp (`beats.research_started_at` at the
    # moment this claim won) — never re-derived, so this table and the Beat's
    # own fence always agree by construction.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
