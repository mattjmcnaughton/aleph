"""Data access for the tutor's conversations and messages (Phase 2 TDD §4/D2).

The one interesting primitive here is :meth:`ConversationRepository.insert_turn`:
a turn (CONTEXT.md — a learner Message and the tutor Message it produced) is
written as a pair at ``max + 1`` / ``max + 2``, so a turn exists whole or not at
all (D2). There is no state machine and no stale recovery to support: a tutor
reply is request-scoped, so a stream that dies persists nothing and there is
nothing to reclaim.

**Every conversation query names a kind** (Phase 2B TDD D3). A path carries two
threads — its in-lesson thread and its **Shaping conversation** — and a query
that did not say which would quietly serve one rail the other's turns, the one
thing PRD §5.8 forbids. So ``kind`` is *required* and keyword-only on every
thread query: a default would demote the rule to a docstring, letting a shaping
caller that forgot it read or clear the lesson thread instead — silently, and
with a clean type-check. Phase 2A's call sites pass
:attr:`~aleph.models.ConversationKind.LESSON` explicitly and the in-lesson tutor
stays bit-identical (W21).

:meth:`ConversationRepository.insert_turn` takes no kind: it is handed a
``conversation_id``, which already names one thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from aleph.models import (
    Conversation,
    ConversationKind,
    Lesson,
    Message,
    MessageRole,
    Path,
)
from aleph.repositories._generation import affected_rows

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.models import MessageSource


@dataclass(frozen=True)
class ThreadMessage:
    """A message paired with the title of the lesson it was asked in.

    The conversation response (§6) reports a lesson title alongside every
    message; resolving it in the thread query keeps that a single join rather
    than a per-message lookup at the service layer.
    """

    message: Message
    lesson_title: str


@dataclass(frozen=True)
class LocatedMessage:
    """A message plus where it sits: its path and its lesson's position.

    The product events are stamped with account, path, lesson and position (PRD
    §5.7), but a ``Message`` row carries only its ``lesson_id`` — the path hangs
    off its conversation and the position off its lesson. The ownership lookup
    walks message -> conversation -> path anyway, so it returns the locator in
    the same row: the check-answer route can stamp ``tutor_check_answered``
    without a second query and without lazy-loading relationships on an async
    session.
    """

    message: Message
    path_id: uuid.UUID
    position_in_path: int


class ConversationRepository:
    """Data access for :class:`~aleph.models.Conversation` / ``Message`` rows.

    Constructed per-request with the caller's :class:`AsyncSession` (repository
    convention); it never opens or commits transactions — the service layer owns
    the unit of work, which is exactly what makes a turn atomic (D2).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_path(
        self,
        path_id: uuid.UUID,
        *,
        kind: ConversationKind,
    ) -> Conversation | None:
        """The path's conversation of ``kind``, or ``None`` before its first turn.

        Scoped to one kind, never "whichever thread exists": the other thread's
        presence must be invisible from here (D3).
        """
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.path_id == path_id, Conversation.kind == kind
            )
        )
        return result.scalar_one_or_none()

    async def upsert_for_path(
        self,
        path_id: uuid.UUID,
        *,
        kind: ConversationKind,
    ) -> tuple[Conversation, bool]:
        """Get the path's conversation of ``kind``, creating it on the first turn.

        Returns ``(conversation, created)``. ``created`` is what lets the caller
        emit ``tutor_conversation_started`` / ``shaping_conversation_started``
        exactly once (§5.5) — including when two sends race: the ``ON CONFLICT
        DO NOTHING`` means the loser inserts nothing, reports ``created=False``
        and still receives the winner's row, so ``UNIQUE (path_id, kind)`` is
        never violated by the normal path (it stays a loud backstop for a
        genuinely duplicated insert).

        The loser seeing the winner's row assumes READ COMMITTED (the app's
        isolation level): under REPEATABLE READ the re-read would run against a
        snapshot older than the winner's commit and find nothing.
        """
        result = await self.session.execute(
            insert(Conversation)
            .values(path_id=path_id, kind=kind)
            .on_conflict_do_nothing(constraint="uq_conversations_path_kind")
            .returning(Conversation.id)
        )
        created = result.scalar_one_or_none() is not None

        conversation = await self.get_for_path(path_id, kind=kind)
        if conversation is None:  # pragma: no cover - unreachable under READ COMMITTED
            raise RuntimeError(
                f"{kind.value} conversation for path {path_id} disappeared"
            )
        return conversation, created

    async def load_thread(
        self,
        path_id: uuid.UUID,
        *,
        kind: ConversationKind,
    ) -> list[ThreadMessage]:
        """The thread's messages in ``position`` order, each with its lesson title.

        An empty list when the path has no conversation of this kind yet — the
        read endpoint answers ``200`` with an empty thread rather than ``404``
        (§6), for either rail.
        """
        result = await self.session.execute(
            select(Message, Lesson.title)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(Lesson, Message.lesson_id == Lesson.id)
            .where(Conversation.path_id == path_id, Conversation.kind == kind)
            .order_by(Message.position)
        )
        return [
            ThreadMessage(message=message, lesson_title=title)
            for message, title in result.all()
        ]

    async def insert_turn(
        self,
        *,
        conversation_id: uuid.UUID,
        lesson_id: uuid.UUID,
        learner_content: str,
        source: MessageSource,
        tutor_content: str,
        tutor_check: dict[str, Any] | None = None,
        proposal: dict[str, Any] | None = None,
    ) -> tuple[Message, Message]:
        """Append a whole turn: the learner message and the tutor reply.

        Positions are assigned here, at persist time: ``max + 1`` for the
        learner row and ``max + 2`` for the tutor row (§4). The two rows are
        flushed together, so the caller's transaction is what makes the turn
        atomic (D2) — a stream that fails before the caller commits leaves the
        thread exactly as it was.

        The per-conversation in-flight lock (D9) is what keeps two sends from
        computing the same ``max``; if it is ever bypassed,
        ``uq_messages_conversation_position`` raises an ``IntegrityError`` here
        rather than silently interleaving two turns.

        ``proposal`` rides on the tutor row exactly as ``tutor_check`` does
        (Phase 2B TDD §4): it is the observed, already-validated payload of a
        **Proposal**. Persisting it is not applying it — a stored proposal
        changes no path structure until the learner taps **Apply** (D5), which
        is what keeps consent structural.
        """
        highest = await self.session.scalar(
            select(func.max(Message.position)).where(
                Message.conversation_id == conversation_id
            )
        )
        base = highest or 0

        learner_message = Message(
            conversation_id=conversation_id,
            lesson_id=lesson_id,
            position=base + 1,
            role=MessageRole.LEARNER,
            content=learner_content,
            source=source,
        )
        tutor_message = Message(
            conversation_id=conversation_id,
            lesson_id=lesson_id,
            position=base + 2,
            role=MessageRole.TUTOR,
            content=tutor_content,
            tutor_check=tutor_check,
            proposal=proposal,
        )
        self.session.add_all([learner_message, tutor_message])
        await self.session.flush()
        return learner_message, tutor_message

    async def delete_for_path(
        self,
        path_id: uuid.UUID,
        *,
        kind: ConversationKind,
    ) -> bool:
        """Drop one of the path's threads ("new conversation", PRD §5.8).

        The ``ON DELETE CASCADE`` removes the messages; nothing Phase 1 owns is
        touched, and neither is the *other* rail's thread — clearing the shaping
        conversation must not clear the in-lesson one, or vice versa. Returns
        whether a row was removed, so the endpoint can stay idempotent (``204``
        either way) without a pre-read.

        The path's **Change history** survives this: ``path_changes`` hangs off
        the path and its ``message_id`` is ``ON DELETE SET NULL`` (D3), so the
        cascade nulls the reference and keeps every row.
        """
        result = await self.session.execute(
            delete(Conversation).where(
                Conversation.path_id == path_id, Conversation.kind == kind
            )
        )
        return affected_rows(result) > 0

    async def set_tutor_check_answer(
        self, *, message: Message, selected_index: int
    ) -> None:
        """Record the learner's choice on a message's Tutor check (§6).

        **Reassigns** the payload rather than editing it in place, and that is
        the whole point: ``Message.tutor_check`` is plain ``JSONB`` with no ORM
        mutation tracking, so ``message.tutor_check["answered_index"] = i`` is
        invisible to the session and is never flushed — a silent no-op that
        looks correct in the same request and loses the answer on the next read.

        The rest of the payload is carried through untouched, so a re-answer
        overwrites only ``answered_index``. A Tutor check is non-scoring and
        outside progress (PRD §5.5): this writes no Attempt and touches no
        Phase 1 table. The caller (which owns the unit of work) commits.
        """
        check = message.tutor_check
        if check is None:  # pragma: no cover - the router 409s before reaching here
            raise ValueError(f"message {message.id} has no tutor check")
        message.tutor_check = {**check, "answered_index": selected_index}
        await self.session.flush()

    async def get_message_for_user(
        self, *, message_id: uuid.UUID, user_id: uuid.UUID
    ) -> LocatedMessage | None:
        """Fetch a message only if it belongs to ``user_id`` (ownership guard).

        The join walks message -> conversation -> path -> user, so the
        check-answer endpoint (§6) sees ``None`` — indistinguishable from a
        missing row — for someone else's message, which is what the
        404-never-403 rule needs.

        It returns the message's locator alongside it (TDD §9): the ownership
        walk already visits the conversation for its path, and one more join to
        the lesson yields the position, so the caller has everything the product
        event is stamped with from the single query that proved ownership. The
        lesson join is inner on purpose — a message cannot exist without one —
        which is also why there is no "the locator went missing" branch to
        handle.
        """
        result = await self.session.execute(
            select(Message, Conversation.path_id, Lesson.position_in_path)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(Path, Conversation.path_id == Path.id)
            .join(Lesson, Lesson.id == Message.lesson_id)
            .where(Message.id == message_id, Path.user_id == user_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        message, path_id, position_in_path = row
        return LocatedMessage(
            message=message, path_id=path_id, position_in_path=position_in_path
        )
