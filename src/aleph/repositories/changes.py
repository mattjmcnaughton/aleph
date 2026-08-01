"""Data access for the path's **Change** history (Phase 2B TDD §4/D3).

A :class:`~aleph.models.PathChange` row exists only because **Apply** committed
it, so this module has no state machine and no claim: create, read one, read the
path's history. Undo does not delete — it flips ``status`` — which is why
:meth:`ChangeRepository.list_for_path` returns undone rows too: the history is
the record of what happened, not of what is still in force.

The history is owned by the **path**, never by a conversation: clearing a thread
nulls ``message_id`` and leaves every row (D3), so every read here is scoped by
``path_id`` and none of them join a conversation.

Constructed per-request with the caller's session (repository convention); it
never opens or commits transactions — the service layer owns the unit of work,
which is what lets ``apply_change``/``undo_change`` write the change row and the
structure it describes in one transaction (D5/D8).

**One payload-aware read** (AL-321): :meth:`ChangeRepository.revision_snapshot`
parses a stored inverse through :class:`~aleph.domains.changes.ChangeInverse`.
That widens this module's imports past "models and ``db`` only" to include the
pure :mod:`aleph.domains` package, and it is deliberate: apply clears a revised
lesson's content (D7), so the old passage the *lesson prompt* needs lives only
in the Change's payload — and ``services/generation.py`` cannot import
``services/shaping.py`` to read it (that is a cycle, through
``services/tutor_context.py``). One shared pure definition below both services
is the smallest thing that works; :meth:`ChangeRepository.create` still stores
the payload opaquely and validates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update

from aleph.domains.changes import ChangeInverse
from aleph.models import Path, PathChange, PathChangeStatus
from aleph.repositories._generation import affected_rows

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.domains.changes import RevisionSnapshot
    from aleph.models import PathChangeKind


@dataclass(frozen=True)
class OwnedChange:
    """A Change plus the path that owns it — one query's worth of ownership.

    Undo (§5.7) needs both: the path to prove the caller owns the Change, and
    then to scope the engagement re-check and every write. Returning the pair
    keeps that a single join rather than a second read the router would have to
    remember to do.
    """

    change: PathChange
    path: Path


class ChangeRepository:
    """Data access for :class:`~aleph.models.PathChange` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        path_id: uuid.UUID,
        message_id: uuid.UUID | None,
        kind: PathChangeKind,
        payload: dict[str, Any],
    ) -> PathChange:
        """Record an applied Change.

        ``status`` and ``applied_at`` are **not** parameters: a Change is born
        applied (there is no other way for a row to exist), and the apply stamp
        is the database's clock. ``payload`` carries the applied operations plus
        their inverses, which is what makes the row self-sufficient for undo
        (D8) — this layer stores it opaquely and validates nothing; the shaping
        service owns its shape.

        ``message_id`` may be ``None``: a Change can outlive the proposal
        message that produced it (D3), and the column is nullable for exactly
        that reason.
        """
        change = PathChange(
            path_id=path_id,
            message_id=message_id,
            kind=kind,
            payload=payload,
        )
        self.session.add(change)
        await self.session.flush()
        return change

    async def get(self, change_id: uuid.UUID) -> PathChange | None:
        """One Change by id, or ``None``."""
        return await self.session.get(PathChange, change_id)

    async def get_for_user(
        self, *, change_id: uuid.UUID, user_id: uuid.UUID
    ) -> OwnedChange | None:
        """A Change and its path, only if the path belongs to ``user_id``.

        The **Undo** endpoint's ownership walk (§5.7 step 1): change → path →
        account, so another learner's change is ``None`` — indistinguishable
        from a missing row, which is what 404-never-403 needs. The path row rides
        back because undo needs it anyway (the engagement re-check and every
        write are scoped to it), so ownership costs one query, not two.
        """
        result = await self.session.execute(
            select(PathChange, Path)
            .join(Path, PathChange.path_id == Path.id)
            .where(PathChange.id == change_id, Path.user_id == user_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        change, path = row
        return OwnedChange(change=change, path=path)

    async def list_applied_for_path(self, path_id: uuid.UUID) -> list[PathChange]:
        """The path's **live** Changes, newest first — undone rows excluded.

        :meth:`list_for_path` is the *history* (everything that happened);
        this is the set that is still in force, which is the question apply-time
        freshness (D5) and the revision-snapshot lookup both ask.
        """
        result = await self.session.execute(
            select(PathChange)
            .where(
                PathChange.path_id == path_id,
                PathChange.status == PathChangeStatus.APPLIED,
            )
            .order_by(PathChange.applied_at.desc(), PathChange.id.desc())
        )
        return list(result.scalars())

    async def resolution_of_message(
        self, message_id: uuid.UUID
    ) -> PathChangeStatus | None:
        """Whether this Proposal already produced a Change, and in what state.

        Apply's "at most once" pre-check (§5.6): ``applied`` and ``undone`` are
        both terminal for a proposal card, and ``None`` means it is still
        unresolved. Indexed by ``message_id`` rather than scanned out of the
        path's history — the question is about one message, and a path's history
        grows without bound while the answer stays one row.

        ``applied`` wins when both exist. A proposal is applied at most once (a
        partial unique index enforces it), but the *pair* apply→undo→… is a real
        sequence, and "this one is in force" is the answer that must not be lost
        behind an older undone row.
        """
        result = await self.session.execute(
            select(PathChange.status)
            .where(PathChange.message_id == message_id)
            .order_by(
                (PathChange.status == PathChangeStatus.APPLIED).desc(),
                PathChange.applied_at.desc(),
            )
            .limit(1)
        )
        return result.scalars().first()

    async def revision_snapshot(
        self, *, path_id: uuid.UUID, lesson_id: uuid.UUID
    ) -> RevisionSnapshot | None:
        """The newest live Change's snapshot of ``lesson_id``, if it revised it.

        The seam ``services/generation.py`` reads to put the **old passage** in a
        revised lesson's prompt (D7). Apply clears the row's content, so this is
        the only copy — and it is deliberately reached through the repository
        rather than by that service learning the payload's shape: the parse is
        :class:`~aleph.domains.changes.ChangeInverse`'s, one definition shared
        with the shaping service that writes it.

        Newest-first and applied-only: a lesson revised twice is being taught
        against its most recent instruction, and an undone Change's snapshot has
        already been restored onto the row itself.
        """
        target = str(lesson_id)
        for change in await self.list_applied_for_path(path_id):
            snapshot = ChangeInverse.from_payload(change.payload).revision_for(target)
            if snapshot is not None:
                return snapshot
        return None

    async def mark_undone(self, change_id: uuid.UUID) -> bool:
        """Flip a Change to ``undone``; ``True`` iff this call performed it.

        Guarded on ``status = 'applied'`` so undo is durably idempotent under
        concurrency the same way completion is: two undos serialize on the row
        lock and exactly one matches. Undo is a **status**, never a delete — the
        history is the record of what happened (CONTEXT.md: *Change history*).
        """
        result = await self.session.execute(
            update(PathChange)
            .where(
                PathChange.id == change_id,
                PathChange.status == PathChangeStatus.APPLIED,
            )
            .values(
                status=PathChangeStatus.UNDONE,
                undone_at=func.now(),
                updated_at=func.now(),
            )
        )
        return affected_rows(result) > 0

    async def list_for_path(self, path_id: uuid.UUID) -> list[PathChange]:
        """The path's Change history, **newest first** (§6).

        Ordered by ``applied_at`` — when structure landed — rather than by
        ``created_at``, so the history reads in the order the learner made it
        happen. ``id`` breaks ties so the order is stable when two changes share
        a timestamp (both stamped from the same transaction clock); the tie is
        arbitrary but never shuffles between reads.

        Undone changes are included: undo is a status, not a delete, and the
        history is the record of what happened (CONTEXT.md: **Change history**).
        """
        result = await self.session.execute(
            select(PathChange)
            .where(PathChange.path_id == path_id)
            .order_by(PathChange.applied_at.desc(), PathChange.id.desc())
        )
        return list(result.scalars())
