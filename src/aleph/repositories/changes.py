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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from aleph.models import PathChange

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.models import PathChangeKind


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
