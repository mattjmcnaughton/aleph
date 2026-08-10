"""Data access for Briefs and their Sources: append-only writes, the rail
read, the read-ping guards, and the two continuity reads (TDD §4/§5.4/§6).

Every write here is append-only (TDD §2's storage summary: "``briefs`` is a
table of nothing but immutable artifacts") — nothing in this module ever
``UPDATE``s ``title``/``body_markdown``/``skip_line``/``claims`` once a row
exists, and the only two columns any write touches after insertion
(``read_at``, ``sources_seen_at``) are guarded first-write-wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from aleph.models import Beat, Brief, BriefKind, BriefSource
from aleph.repositories._generation import affected_rows

if TYPE_CHECKING:
    import datetime
    import uuid
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class NewSource:
    """One Source to materialize alongside a published Brief (TDD §5.5).

    A Source's metadata is never model-written: the writing agent emits URLs
    only, and ``publisher``/``title``/``published_on`` are joined from the
    retriever's own ``RetrievedDocument`` before reaching this repository
    (``services/briefing.py``, a later ticket) — this dataclass is the
    already-resolved shape :meth:`BriefRepository.create_published` accepts,
    never a model output.
    """

    url: str
    publisher: str
    title: str
    published_on: date


class BriefRepository:
    """Data access for :class:`~aleph.models.Brief` and its Sources.

    Constructed per-request with the caller's :class:`AsyncSession`
    (repository convention); nothing here commits — the service layer owns
    the unit of work.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- append-only writes (TDD §5.6/§5.7) ---------------------------------- #

    async def create_published(
        self,
        *,
        beat_id: uuid.UUID,
        number: int,
        published_at: datetime.datetime,
        published_on: date,
        title: str,
        body_markdown: str,
        claims: Sequence[str],
        sources: Sequence[NewSource],
    ) -> Brief:
        """Insert a **published** Brief plus its Sources, in one flush.

        ``number`` is the caller's to compute (this layer decides no
        numbering policy) and is what the partial ``uq_briefs_beat_id_number``
        index polices against a concurrent duplicate. ``sources`` becomes
        ``brief_sources`` rows numbered by their position in the sequence
        (1-based, matching the Brief's own rendering order) — **"a Brief with
        no Sources is not publishable"** (TDD §5.5) is enforced by the caller
        (the writer's validator), not here: this method will happily insert a
        published Brief with zero sources if asked, because a repository
        records what it is told, it does not re-derive the rule.
        """
        brief = Brief(
            beat_id=beat_id,
            kind=BriefKind.PUBLISHED,
            number=number,
            published_at=published_at,
            published_on=published_on,
            title=title,
            body_markdown=body_markdown,
            skip_line=None,
            claims=list(claims),
        )
        self.session.add(brief)
        await self.session.flush()
        self.session.add_all(
            [
                BriefSource(
                    brief_id=brief.id,
                    position=position,
                    url=source.url,
                    publisher=source.publisher,
                    title=source.title,
                    published_on=source.published_on,
                )
                for position, source in enumerate(sources, start=1)
            ]
        )
        await self.session.flush()
        return brief

    async def create_skipped(
        self,
        *,
        beat_id: uuid.UUID,
        published_at: datetime.datetime,
        published_on: date,
        skip_line: str,
    ) -> Brief:
        """Insert a **Skipped** entry (D2): unnumbered, no body, no Sources.

        The novelty gate finding no survivors (``domains/novelty.py``, a
        sibling ticket) resolves here, never as a failure — "the feature
        working correctly" (TDD §5.7).
        """
        brief = Brief(
            beat_id=beat_id,
            kind=BriefKind.SKIPPED,
            number=None,
            published_at=published_at,
            published_on=published_on,
            title=None,
            body_markdown=None,
            skip_line=skip_line,
            claims=[],
        )
        self.session.add(brief)
        await self.session.flush()
        return brief

    # -- reads (TDD §4/§6) ---------------------------------------------------- #

    async def get(self, brief_id: uuid.UUID) -> Brief | None:
        return await self.session.get(Brief, brief_id)

    async def get_for_user(
        self, *, brief_id: uuid.UUID, user_id: uuid.UUID
    ) -> Brief | None:
        """Fetch a Brief only if its Beat belongs to ``user_id`` (ownership).

        Restored (AL-522, issue #172) after AL-511's review dropped it for
        having no caller and no test — this ticket is that caller:
        ``GET /briefs/{id}`` and ``POST /briefs/{id}/read`` in
        ``routers/v1/beats.py``. A Brief carries no ``user_id`` of its own
        (D1: it belongs to a Beat, which belongs to a learner, and neither
        model declares a ``relationship()``), so ownership is an explicit
        join — the ``LessonRepository.get_for_user`` shape one level down
        (``Brief -> Beat.user_id``, in place of ``Lesson -> Path.user_id``).
        Another learner's Brief resolves to ``None`` here, 404-never-403
        (TDD §6).
        """
        result = await self.session.execute(
            select(Brief)
            .join(Beat, Brief.beat_id == Beat.id)
            .where(Brief.id == brief_id, Beat.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def last_published_on_by_beat(
        self, beat_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, date]:
        """``MAX(published_on)`` per Beat, of **either** kind (D4/D2).

        The Cadence floor's whole input (``domains/cadence.py::is_claimable``):
        "the last entry" is the highest ``published_on`` across every row —
        published or Skipped — for a Beat, which is exactly what a Skipped
        entry resetting the floor (PRD §4.6) means. Batched over every id in
        one query (the arrival drain's own bound is
        ``MAX_BEATS_PER_LEARNER``, so this never becomes a hot query), rather
        than one round trip per Beat. A Beat absent from the returned mapping
        has no entries at all (PRD §3's "claimable immediately" case).
        """
        if not beat_ids:
            return {}
        result = await self.session.execute(
            select(Brief.beat_id, func.max(Brief.published_on))
            .where(Brief.beat_id.in_(beat_ids))
            .group_by(Brief.beat_id)
        )
        return {beat_id: last_on for beat_id, last_on in result.all()}

    async def latest_published(self, beat_id: uuid.UUID) -> Brief | None:
        """The highest-numbered **published** Brief for a Beat, or ``None``.

        Two callers (``services/briefing.py``, ticket AL-521): the templated
        Skipped clause's "Nothing material since Brief #N" (never the last
        *entry* — a Skipped row carries no number to name, D2) and the next
        Brief's own number (``N + 1``, or ``1`` on a Beat's first-ever
        publish). Also the query shape AL-522's "Builds on Brief #N" read
        will specialize with ``number < :n`` — unspecialized here since this
        method answers a different question (the *overall* latest, not
        "the latest below this one").
        """
        result = await self.session.execute(
            select(Brief)
            .where(Brief.beat_id == beat_id, Brief.kind == BriefKind.PUBLISHED)
            .order_by(Brief.number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def previous_published(
        self, *, beat_id: uuid.UUID, number: int
    ) -> Brief | None:
        """The highest-numbered **published** Brief strictly below ``number``
        — "Builds on Brief #N" (TDD §4/§6), new work this ticket adds (not
        one of the four restored methods): ``WHERE number < :n ORDER BY
        number DESC LIMIT 1``, never a stored edge (D1's "no
        ``builds_on_brief_id``"). The caller (``routers/v1/beats.py``) only
        calls this for a ``kind == PUBLISHED`` Brief with a real ``number``
        — a Skipped entry's ``builds_on`` is ``None`` by construction at the
        call site, never by this query returning nothing for a ``None``
        input.
        """
        result = await self.session.execute(
            select(Brief)
            .where(
                Brief.beat_id == beat_id,
                Brief.kind == BriefKind.PUBLISHED,
                Brief.number < number,
            )
            .order_by(Brief.number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def sources_for_brief(self, brief_id: uuid.UUID) -> list[BriefSource]:
        """A Brief's Sources, in rendering order (§6's Sources block).

        Restored (AL-522, issue #172) after AL-511's review dropped it for
        having no caller and no test — this ticket is that caller:
        ``GET /briefs/{id}``. Ordered by ``position`` (1-based,
        ``create_published``'s own insertion order), never re-derived from
        ``published_on`` or insertion time. Empty for a Skipped entry (no
        ``brief_sources`` rows exist for one, D2) and for an id that does not
        resolve at all.
        """
        result = await self.session.execute(
            select(BriefSource)
            .where(BriefSource.brief_id == brief_id)
            .order_by(BriefSource.position)
        )
        return list(result.scalars())

    async def unread_counts_by_beat(
        self, beat_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """Count of **published** Briefs with ``read_at IS NULL``, per Beat.

        New work this ticket adds (not one of the four restored methods) —
        ``GET /beats``'s "unread counts" (TDD §6's endpoint description).
        Batched over every id in one query, the ``last_published_on_by_beat``
        shape, so the learner's Beats list never becomes one query per row.

        **Published only (code-review FIX 3, AL-522).** A Skipped row's
        ``read_at`` can never be stamped: ``SkippedEntryDTO`` carries no
        ``read_at`` field at all, a Skipped rail row links nowhere in the
        shipped frontend (``docs/api.md``), and — since code-review FIX 2
        (AL-531) — :meth:`mark_read` itself now refuses to stamp one even if
        a ping somehow targeted it, so "no read ping is ever sent for one" is
        an enforced invariant here, not merely an observation about what the
        shipped client happens to do. Without the filter here, a Skipped
        entry counted in this query would be permanently unread, on a Beat
        that may have zero unread *Briefs*. A quiet Beat that produces three
        Skipped weeks and one read Brief would otherwise show "3 new briefs"
        forever (PRD §4.10's home-card copy), monotone in skips and never
        returning to zero — destroying the exact signal that copy exists to
        carry. Filtering to ``BriefKind.PUBLISHED`` is what keeps this count
        meaning "Briefs this learner has not yet opened", never "rows
        nothing can ever clear".
        """
        if not beat_ids:
            return {}
        result = await self.session.execute(
            select(Brief.beat_id, func.count())
            .where(
                Brief.beat_id.in_(beat_ids),
                Brief.kind == BriefKind.PUBLISHED,
                Brief.read_at.is_(None),
            )
            .group_by(Brief.beat_id)
        )
        return {beat_id: count for beat_id, count in result.all()}

    async def list_for_beat(self, beat_id: uuid.UUID) -> list[Brief]:
        """The Beat rail: newest first, **both kinds interleaved** (D2) —
        served by ``ix_briefs_beat_id_published_on``. Ties (same
        ``published_on``) break on ``published_at``, the event timestamp
        (TDD §4), so two same-day entries still order by when they actually
        happened; ``id`` is a final tiebreak only for the (practically
        unreachable) case both timestamps also match, keeping the order
        total.
        """
        result = await self.session.execute(
            select(Brief)
            .where(Brief.beat_id == beat_id)
            .order_by(
                Brief.published_on.desc(),
                Brief.published_at.desc(),
                Brief.id.desc(),
            )
        )
        return list(result.scalars())

    # -- continuity (D9's inputs; TDD §5.4/§5.6) ------------------------------ #

    async def prior_claims_for_beat(self, beat_id: uuid.UUID) -> list[str]:
        """Every claim ever published on this Beat, flattened — D9's dedup
        material (``briefs.claims``, "read only as a whole set for one Beat").
        Order is not meaningful; the novelty gate does token-overlap matching,
        not sequence comparison.
        """
        result = await self.session.execute(
            select(Brief.claims).where(Brief.beat_id == beat_id)
        )
        claims: list[str] = []
        for row_claims in result.scalars():
            claims.extend(row_claims)
        return claims

    async def prior_source_urls_for_beat(self, beat_id: uuid.UUID) -> set[str]:
        """Every Source URL ever cited by this Beat's Briefs — Brief
        continuity's mechanical check (CONTEXT.md: Brief continuity; D9's
        Source-URL-overlap arm). A plain join over one Beat's bounded history
        (TDD §4's growth note), never a second table walked independently.
        """
        result = await self.session.execute(
            select(BriefSource.url)
            .join(Brief, Brief.id == BriefSource.brief_id)
            .where(Brief.beat_id == beat_id)
        )
        return set(result.scalars())

    # -- the read ping (D11, first-write-wins) -------------------------------- #

    async def mark_read(self, brief_id: uuid.UUID) -> bool:
        """Stamp ``read_at`` on first open only — **published Briefs only**
        (code-review FIX 2, AL-531).

        ``UPDATE briefs SET read_at = now() WHERE id = :id AND kind =
        'published' AND read_at IS NULL`` — the same first-write-wins guard
        ``LessonRepository.mark_completed`` / ``mark_completed_and_finalize``
        use (``read_at IS NULL``), and for the same reason: the north-star
        metric (§9) asks when a learner *first* opened a Brief, so a re-read
        must never move the timestamp. Two concurrent pings serialize on the
        row lock; exactly one sees ``read_at IS NULL`` and wins.

        The ``kind == PUBLISHED`` clause is what makes
        :meth:`unread_counts_by_beat`'s own docstring true rather than
        aspirational: that method has always *assumed* "a Skipped row's
        ``read_at`` can never be stamped" and filtered to published Briefs on
        that basis, but nothing enforced it here — a client bug (or a
        deliberately crafted request) hitting ``POST /briefs/{skipped_id}/read``
        got a ``204`` and a stamped row with no guard at all before this fix.
        A Skipped id now no-ops here exactly like a genuine repeat ping
        does — ``affected_rows(result) == 0``, still a well-formed
        idempotent call, never an error — so PRD §4.6's "a Skipped period is
        not a read" holds all the way down to the row, and
        ``brief_read_rate.sql`` (opened ÷ published) can no longer see a
        Skipped id enter the numerator with no denominator row to match it.
        Returns whether *this* call performed the transition.
        """
        result = await self.session.execute(
            update(Brief)
            .where(
                Brief.id == brief_id,
                Brief.kind == BriefKind.PUBLISHED,
                Brief.read_at.is_(None),
            )
            .values(read_at=func.now(), updated_at=func.now())
        )
        return affected_rows(result) > 0

    async def mark_sources_seen(self, brief_id: uuid.UUID) -> bool:
        """Stamp ``sources_seen_at`` on first visibility only — the same
        first-write-wins shape as :meth:`mark_read`, for PRD §5's **Depth of
        read** (§9).

        Also published-Briefs-only (code-review FIX 2, AL-531), for the
        identical reason :meth:`mark_read` now carries the guard: a Skipped
        entry has no Sources block to ever become visible (``sources_for_brief``
        returns ``[]`` for one, D2), so a ping claiming otherwise no-ops here
        rather than stamping a timestamp for an event that cannot have
        happened.
        """
        result = await self.session.execute(
            update(Brief)
            .where(
                Brief.id == brief_id,
                Brief.kind == BriefKind.PUBLISHED,
                Brief.sources_seen_at.is_(None),
            )
            .values(sources_seen_at=func.now(), updated_at=func.now())
        )
        return affected_rows(result) > 0
