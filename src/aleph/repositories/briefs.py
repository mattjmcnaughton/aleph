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

from aleph.models import Brief, BriefKind, BriefSource
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

    async def latest_entry_claims_for_beat(self, beat_id: uuid.UUID) -> list[str]:
        """The MOST RECENT entry's claims — code-review FIX 6 on AL-521.

        This, not :meth:`prior_claims_for_beat`, is what
        ``AnalystDeps.open_threads`` is built from. The two must diverge:
        ``prior_claims_for_beat`` is D9's own novelty-gate input and stays
        the Beat's *whole* history (unbounded, on purpose — the gate needs
        every prior claim to dedupe against); ``open_threads`` is "carried
        forward from prior Briefs" (TDD §5.4), which most plainly reads as
        *the last thing reported*, not the Beat's entire archive. Bounding it
        here also keeps the analyst prompt's size independent of a Beat's
        age — a two-year-old weekly Beat has ~104 entries's worth of claims
        under the old (unbounded) reading; this always contributes at most
        one entry's worth, however old the Beat is.

        Same "most recent" ordering as :meth:`list_for_beat`'s rail read
        (``published_on`` desc, then ``published_at`` desc, then ``id`` desc
        as a final tiebreak) — one definition of "most recent" shared by both.

        The most recent entry may be **Skipped**, whose ``claims`` is always
        ``[]`` (``create_skipped``'s own shape) — that is read as "nothing to
        carry forward", an empty list, never a fallback to an OLDER entry:
        "nothing was said last time" is itself the honest continuity signal,
        not a gap to paper over by reaching further back.
        """
        result = await self.session.execute(
            select(Brief.claims)
            .where(Brief.beat_id == beat_id)
            .order_by(
                Brief.published_on.desc(),
                Brief.published_at.desc(),
                Brief.id.desc(),
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return list(row) if row is not None else []

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
        """Stamp ``read_at`` on first open only.

        ``UPDATE briefs SET read_at = now() WHERE id = :id AND read_at IS
        NULL`` — the same guard ``LessonRepository.mark_completed`` /
        ``mark_completed_and_finalize`` use, and for the same reason: the
        north-star metric (§9) asks when a learner *first* opened a Brief, so
        a re-read must never move the timestamp. Two concurrent pings
        serialize on the row lock; exactly one sees ``read_at IS NULL`` and
        wins. Returns whether *this* call performed the transition.
        """
        result = await self.session.execute(
            update(Brief)
            .where(Brief.id == brief_id, Brief.read_at.is_(None))
            .values(read_at=func.now(), updated_at=func.now())
        )
        return affected_rows(result) > 0

    async def mark_sources_seen(self, brief_id: uuid.UUID) -> bool:
        """Stamp ``sources_seen_at`` on first visibility only — the same
        first-write-wins shape as :meth:`mark_read`, for PRD §5's **Depth of
        read** (§9).
        """
        result = await self.session.execute(
            update(Brief)
            .where(Brief.id == brief_id, Brief.sources_seen_at.is_(None))
            .values(sources_seen_at=func.now(), updated_at=func.now())
        )
        return affected_rows(result) > 0
