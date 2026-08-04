"""D1's replay property (Phase 3 TDD §11, ADR 0008): folding `apply_grade`
over the review log reproduces `flashcards.rung`/`due_on` exactly.

`flashcard_reviews` is append-only and authoritative; `Flashcard.rung`/`due_on`
is a projection over it, written in the same transaction as the review row
(`services.reviews._grade`, TDD §5.4). This is the test that makes "the
scheduler shipped a bug, now what — drop the projection, replay" a true
statement rather than an aspiration (D1): it drives a sequence of grades
through the *real* service function against a small in-memory `GradeStore`
fake (CLAUDE.md: fakes over mocks, no database), then folds
`domains.scheduling.apply_grade` over the resulting log and asserts the fold
reproduces the fake's live projection byte-for-byte.

The fake's `due_candidates` always reports the one card under test as due and
unsatisfied — the full queue-selection machinery (§5.3) is
`test_reviews_service.py`'s concern; this file's only concern is that
whatever `_grade` writes to the log is exactly what replaying the log
reproduces.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from aleph.config import settings
from aleph.domains.scheduling import CardState, Grade, apply_grade
from aleph.models import Flashcard, FlashcardGrade
from aleph.repositories.flashcards import DueCandidate
from aleph.services.reviews import _grade

if TYPE_CHECKING:
    import uuid as _uuid

    from aleph.models import FlashcardReview

_USER = uuid.uuid4()
_LESSON = uuid.uuid4()
_PATH = uuid.uuid4()
_GENERATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_LADDER = settings.flashcard_ladder


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class _LoggedReview:
    """The subset of a `flashcard_reviews` row the replay fold needs."""

    grade: FlashcardGrade
    local_day: date
    rung_before: int
    due_on_before: date
    rung_after: int
    due_on_after: date


@dataclass
class _PermissiveGradeStore:
    """A `GradeStore` fake that always reports its one card as due and
    unsatisfied, so an arbitrary sequence of grades can be driven through
    `_grade` without reconstructing §5.3's queue-selection population —
    `select_daily_queue` selects the sole candidate every time (one candidate
    is never more than any `cap`), so every call in the sequence is legal.
    """

    card: Flashcard
    log: list[_LoggedReview] = field(default_factory=list)

    async def get_card_for_update(
        self, *, user_id: _uuid.UUID, card_id: _uuid.UUID
    ) -> Flashcard | None:
        if card_id != self.card.id or user_id != self.card.user_id:
            return None
        return self.card

    async def due_candidates(
        self, *, user_id: _uuid.UUID, today: date
    ) -> list[DueCandidate]:
        assert self.card.due_on is not None
        return [
            DueCandidate(
                card_id=self.card.id,
                due_on=self.card.due_on,
                satisfied=False,
                last_reviewed_at=None,
            )
        ]

    async def append_review_and_project(
        self,
        *,
        card_id: _uuid.UUID,
        user_id: _uuid.UUID,
        grade: FlashcardGrade,
        reviewed_at: datetime,
        local_day: date,
        rung_before: int,
        rung_after: int,
        due_on_before: date,
        due_on_after: date,
    ) -> FlashcardReview:
        self.log.append(
            _LoggedReview(
                grade=grade,
                local_day=local_day,
                rung_before=rung_before,
                due_on_before=due_on_before,
                rung_after=rung_after,
                due_on_after=due_on_after,
            )
        )
        self.card.rung = rung_after
        self.card.due_on = due_on_after
        # The review row's own identity is never inspected by anything under
        # test here (only the log above is) — a cast stand-in is honest about
        # that, rather than constructing a real (unpersisted) ORM instance.
        return cast("FlashcardReview", object())


def _new_card(*, rung: int = 0, due_on: date) -> Flashcard:
    return Flashcard(
        id=uuid.uuid4(),
        user_id=_USER,
        front="front",
        back="back",
        kept_at=datetime.now(UTC),
        rung=rung,
        due_on=due_on,
        source_lesson_id=_LESSON,
        source_path_id=_PATH,
        source_lesson_title="A lesson",
        source_path_title="A path",
        source_generated_at=_GENERATED_AT,
    )


async def _drive(
    store: _PermissiveGradeStore, grades: list[tuple[FlashcardGrade, date]]
) -> None:
    """Grade `store.card` once per `(grade, today)` pair, in order, always
    passing the fake's *current* `rung` as `rung_before` — exactly what a
    client that just read the previous response would send."""
    for grade, today in grades:
        now = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
        assert store.card.rung is not None
        await _grade(
            store,
            user_id=_USER,
            card_id=store.card.id,
            grade=grade,
            rung_before=store.card.rung,
            tz_offset_minutes=0,
            now=now,
        )


def _replay(log: list[_LoggedReview]) -> CardState:
    """Fold `apply_grade` over the log, starting from its first entry's
    `(rung_before, due_on_before)` — the D1 replay property itself."""
    assert log, "an empty log has nothing to replay"
    state = CardState(rung=log[0].rung_before, due_on=log[0].due_on_before)
    for entry in log:
        state = apply_grade(
            state, Grade(entry.grade.value), today=entry.local_day, ladder=_LADDER
        )
    return state


# --------------------------------------------------------------------------- #
# A hand-written sequence covering promotion, demotion, the floor and the
# ceiling explicitly.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_replay_reproduces_the_projection_for_a_deterministic_sequence() -> None:
    day0 = date(2026, 1, 5)
    card = _new_card(rung=0, due_on=day0)
    store = _PermissiveGradeStore(card=card)

    sequence = [
        (FlashcardGrade.GOT_IT, day0),  # 0 -> 1
        (FlashcardGrade.GOT_IT, day0 + timedelta(days=3)),  # 1 -> 2
        (FlashcardGrade.AGAIN, day0 + timedelta(days=10)),  # 2 -> 1, due today
        (FlashcardGrade.AGAIN, day0 + timedelta(days=10)),  # a same-day lapse: 1 -> 0
        (FlashcardGrade.GOT_IT, day0 + timedelta(days=10)),  # 0 -> 1
        (FlashcardGrade.GOT_IT, day0 + timedelta(days=13)),  # 1 -> 2
        (FlashcardGrade.GOT_IT, day0 + timedelta(days=20)),  # 2 -> 3
        (FlashcardGrade.GOT_IT, day0 + timedelta(days=34)),  # 3 -> 4 (top rung)
        (FlashcardGrade.GOT_IT, day0 + timedelta(days=64)),  # top rung is a fixed point
        (FlashcardGrade.AGAIN, day0 + timedelta(days=94)),  # 4 -> 3
    ]
    await _drive(store, sequence)

    replayed = _replay(store.log)

    assert replayed.rung == card.rung
    assert replayed.due_on == card.due_on
    # Pinned independently, so a bug in *both* the service and the replay
    # fold in the same way could not hide behind this test.
    assert card.rung == 3
    assert len(_LADDER) == 5  # the assertion above assumes the shipped ladder


# --------------------------------------------------------------------------- #
# Arbitrary (seeded, so failures are reproducible) sequences.
# --------------------------------------------------------------------------- #


def _random_sequence(
    rng: random.Random, *, start: date, length: int
) -> list[tuple[FlashcardGrade, date]]:
    """A realistic-shaped random sequence: `today` never moves backwards, and
    sometimes repeats (D8's same-day lapse re-show)."""
    sequence: list[tuple[FlashcardGrade, date]] = []
    today = start
    for _ in range(length):
        grade = rng.choice([FlashcardGrade.AGAIN, FlashcardGrade.GOT_IT])
        sequence.append((grade, today))
        today = today + timedelta(days=rng.choice([0, 0, 1, 2, 5]))
    return sequence


@pytest.mark.anyio
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
async def test_replay_reproduces_the_projection_for_random_sequences(
    seed: int,
) -> None:
    rng = random.Random(seed)
    day0 = date(2026, 2, 1)
    card = _new_card(rung=0, due_on=day0)
    store = _PermissiveGradeStore(card=card)

    sequence = _random_sequence(rng, start=day0, length=40)
    await _drive(store, sequence)

    replayed = _replay(store.log)

    assert replayed.rung == card.rung
    assert replayed.due_on == card.due_on
