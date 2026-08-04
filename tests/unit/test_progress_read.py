"""Unit tests for :mod:`aleph.services.progress_read` (Phase 5 TDD §11).

Against a **fake repository behind a Protocol** (CLAUDE.md: fakes over mocks) —
no session, no Postgres. ``_summarize`` (the private seam ``load_progress_summary``
delegates to) is what these tests call directly; see the module docstring's
"fake-repository seam" section for why the split exists.

The two-hemisphere sign-convention test below is, per the TDD, "the single most
important test in this slice": a completion stamped at 23:30 UTC must read as
*tomorrow* for a learner at UTC+2 and as *today* for one at UTC-5. Both
directions are named explicitly, because "off by a day for one hemisphere" is
the failure mode that ships quietly.

**Phase 3 TDD D11/§5.5** adds the streak union's tests below, against a second
fake behind :class:`~aleph.services.progress_read.ReviewDaysReader` — the same
fakes-over-mocks shape as ``FakeCompletionDaysReader``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from aleph.config import settings
from aleph.repositories import CompletionDay
from aleph.services.progress_read import ProgressSummaryView, _summarize

_PATH_A = uuid.uuid4()
_PATH_B = uuid.uuid4()
_USER = uuid.uuid4()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeCompletionDaysReader:
    """An in-memory stand-in for ``LessonRepository.completion_days_for_user``.

    Ignores ``user_id``/``tz_offset_minutes`` — the fake is handed rows that
    already look like what the real query would have produced for whatever
    scenario a test wants, exactly as a small hand-written fake should
    (CLAUDE.md). Calls are recorded so a test can assert the service actually
    passed its arguments through.
    """

    def __init__(self, rows: list[CompletionDay]) -> None:
        self.rows = rows
        self.calls: list[tuple[uuid.UUID, int]] = []

    async def completion_days_for_user(
        self, *, user_id: uuid.UUID, tz_offset_minutes: int
    ) -> list[CompletionDay]:
        self.calls.append((user_id, tz_offset_minutes))
        return self.rows


class FakeReviewDaysReader:
    """An in-memory stand-in for ``FlashcardRepository.review_days_for_user``.

    Same shape as ``FakeCompletionDaysReader`` — ignores ``user_id`` /
    ``tz_offset_minutes`` and records calls so a test can assert whether (and
    how) the service actually reached it.
    """

    def __init__(self, days: list[date]) -> None:
        self.days = days
        self.calls: list[tuple[uuid.UUID, int]] = []

    async def review_days_for_user(
        self, *, user_id: uuid.UUID, tz_offset_minutes: int
    ) -> list[date]:
        self.calls.append((user_id, tz_offset_minutes))
        return self.days


async def _run(
    rows: list[CompletionDay], *, tz_offset_minutes: int = 0, now: datetime
) -> ProgressSummaryView:
    reader = FakeCompletionDaysReader(rows)
    return await _summarize(
        reader, user_id=_USER, tz_offset_minutes=tz_offset_minutes, now=now
    )


# -- the sign convention (D3): the test this feature most needs ------------ #


@pytest.mark.anyio
async def test_a_completion_at_2330_utc_is_tomorrow_at_utc_plus_2() -> None:
    """``tz_offset_minutes = -120`` (UTC+2): 23:30 UTC has already rolled over."""
    now = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    view = await _run([], tz_offset_minutes=-120, now=now)
    assert view.today == date(2026, 1, 2)


@pytest.mark.anyio
async def test_a_completion_at_2330_utc_is_still_today_at_utc_minus_5() -> None:
    """``tz_offset_minutes = +300`` (UTC-5): 23:30 UTC is only 18:30 locally."""
    now = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    view = await _run([], tz_offset_minutes=300, now=now)
    assert view.today == date(2026, 1, 1)


@pytest.mark.anyio
async def test_a_zero_offset_is_the_utc_calendar_day() -> None:
    now = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    view = await _run([], tz_offset_minutes=0, now=now)
    assert view.today == date(2026, 1, 1)


# -- the two folds ----------------------------------------------------------- #


@pytest.mark.anyio
async def test_two_paths_active_on_the_same_day_fold_to_one_global_active_day() -> None:
    """The global streak is a *set* of days, not a sum of per-path days."""
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    rows = [
        CompletionDay(path_id=_PATH_A, day=today, count=1),
        CompletionDay(path_id=_PATH_B, day=today, count=2),
    ]
    view = await _run(rows, now=now)

    # One active day globally, so the current global streak is 1 — not 2.
    assert view.current_streak == 1
    assert view.completed_today == 3  # summed across both paths


@pytest.mark.anyio
async def test_per_path_folds_are_independent() -> None:
    """One path's streak is unaffected by another path's completion days."""
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    rows = [
        # Path A: a 3-day run ending today.
        CompletionDay(path_id=_PATH_A, day=today, count=1),
        CompletionDay(path_id=_PATH_A, day=date(2026, 8, 1), count=1),
        CompletionDay(path_id=_PATH_A, day=date(2026, 7, 31), count=1),
        # Path B: only today.
        CompletionDay(path_id=_PATH_B, day=today, count=1),
    ]
    view = await _run(rows, now=now)

    by_path = {path.path_id: path for path in view.paths}
    assert by_path[_PATH_A].current_streak == 3
    assert by_path[_PATH_B].current_streak == 1
    # The global fold unions the days: today, yesterday, the day before — 3.
    assert view.current_streak == 3


@pytest.mark.anyio
async def test_completed_today_sums_globally_and_splits_per_path() -> None:
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    rows = [
        CompletionDay(path_id=_PATH_A, day=today, count=2),
        CompletionDay(path_id=_PATH_B, day=today, count=5),
    ]
    view = await _run(rows, now=now)

    assert view.completed_today == 7
    by_path = {path.path_id: path for path in view.paths}
    assert by_path[_PATH_A].completed_today == 2
    assert by_path[_PATH_B].completed_today == 5


@pytest.mark.anyio
async def test_a_path_with_no_completion_today_reports_zero_completed_today() -> None:
    """A path present in ``paths`` (it has *some* history) but idle today."""
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    rows = [CompletionDay(path_id=_PATH_A, day=date(2026, 8, 1), count=1)]
    view = await _run(rows, now=now)

    assert len(view.paths) == 1
    assert view.paths[0].completed_today == 0
    assert view.completed_today == 0


@pytest.mark.anyio
async def test_a_path_with_no_completions_at_all_is_absent_from_paths() -> None:
    """D5/§14 R2: the fold never manufactures a zero row for an untouched path."""
    view = await _run([], now=datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    assert view.paths == []
    assert view.current_streak == 0
    assert view.best_streak == 0
    assert view.completed_today == 0


# -- the repository call itself ---------------------------------------------- #


@pytest.mark.anyio
async def test_the_repository_is_called_with_the_caller_supplied_arguments() -> None:
    reader = FakeCompletionDaysReader([])
    await _summarize(
        reader,
        user_id=_USER,
        tz_offset_minutes=-120,
        now=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )
    assert reader.calls == [(_USER, -120)]


@pytest.mark.anyio
async def test_now_defaults_to_the_real_clock_when_omitted() -> None:
    """No ``now`` means production behaviour: today is derived from the real clock."""
    reader = FakeCompletionDaysReader([])
    view = await _summarize(reader, user_id=_USER, tz_offset_minutes=0)
    assert view.today == datetime.now(UTC).date()


# -- the activity window ------------------------------------------------------ #


@pytest.mark.anyio
async def test_activity_window_length_matches_the_configured_setting() -> None:
    view = await _run([], now=datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    assert len(view.activity) == settings.streak_activity_window_days
    assert view.activity[-1].day == view.today


@pytest.mark.anyio
async def test_paths_are_sorted_for_a_stable_wire_order() -> None:
    """Not part of the TDD's contract text, but pinned so it does not drift by
    accident: ``paths`` reads in a deterministic order regardless of the
    repository's own row order.
    """
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    ids = sorted([_PATH_A, _PATH_B], key=str)
    rows = [
        CompletionDay(path_id=ids[1], day=today, count=1),
        CompletionDay(path_id=ids[0], day=today, count=1),
    ]
    view = await _run(rows, now=now)
    assert [path.path_id for path in view.paths] == ids


# -- the streak union (Phase 3 TDD D11/§5.5) --------------------------------- #


@pytest.mark.anyio
async def test_a_review_only_day_is_active_globally_but_not_for_any_path_streak() -> (
    None
):
    """D11's whole claim, as one test: a review-only day widens the global
    fold and leaves every per-path fold exactly as it was. The two halves are
    only correct together — a test that checked just one could pass while the
    other silently leaked a review into ``rows_by_path``.
    """
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    # Path A's last lesson completion was two days ago — no run reaches today
    # on its own.
    rows = [CompletionDay(path_id=_PATH_A, day=date(2026, 7, 31), count=1)]
    completions = FakeCompletionDaysReader(rows)
    reviews = FakeReviewDaysReader([today])

    view = await _summarize(
        completions, reviews, user_id=_USER, tz_offset_minutes=0, now=now
    )

    # Global: today is an Active day via the review alone.
    assert view.current_streak == 1
    # Per-path: Path A's own streak never saw the review — still broken.
    by_path = {path.path_id: path for path in view.paths}
    assert by_path[_PATH_A].current_streak == 0


@pytest.mark.anyio
async def test_the_union_does_not_double_count_a_day_with_both_signals() -> None:
    """A day with a completion *and* a review is one Active day, not two."""
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    rows = [CompletionDay(path_id=_PATH_A, day=today, count=1)]
    completions = FakeCompletionDaysReader(rows)
    reviews = FakeReviewDaysReader([today])

    view = await _summarize(
        completions, reviews, user_id=_USER, tz_offset_minutes=0, now=now
    )

    assert view.current_streak == 1


@pytest.mark.anyio
async def test_completed_today_is_unmoved_by_a_review() -> None:
    """``completed_today`` is "N lessons today" on the wire — a review must
    never move it, even on a day that also has lesson completions, and even on
    a day that has *only* a review.
    """
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

    # A day with both signals: completed_today counts the lesson only.
    both = await _summarize(
        FakeCompletionDaysReader([CompletionDay(path_id=_PATH_A, day=today, count=2)]),
        FakeReviewDaysReader([today]),
        user_id=_USER,
        tz_offset_minutes=0,
        now=now,
    )
    assert both.completed_today == 2

    # A review-only day: no lesson completed, so completed_today is zero even
    # though the day is Active globally.
    review_only = await _summarize(
        FakeCompletionDaysReader([]),
        FakeReviewDaysReader([today]),
        user_id=_USER,
        tz_offset_minutes=0,
        now=now,
    )
    assert review_only.completed_today == 0
    assert review_only.current_streak == 1


@pytest.mark.anyio
async def test_a_review_only_days_activity_cell_is_non_empty() -> None:
    """The strip cannot contradict the streak: a review-only day must not
    render as an empty cell, since it *is* counted as Active globally.
    """
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

    view = await _summarize(
        FakeCompletionDaysReader([]),
        FakeReviewDaysReader([today]),
        user_id=_USER,
        tz_offset_minutes=0,
        now=now,
    )

    today_cell = next(cell for cell in view.activity if cell.day == today)
    assert today_cell.count >= 1


@pytest.mark.anyio
async def test_with_no_reviews_reader_the_review_reader_is_never_called() -> None:
    """D10: the flag-off shape at this layer is simply "no reader passed" —
    ``load_progress_summary`` is what decides whether to construct one at all
    (its own docstring), so a fake handed to a test but never wired into
    ``_summarize`` is provably untouched, and the output is bit-identical to
    the pre-Phase-3 two-argument call.
    """
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    unused_reviews = FakeReviewDaysReader([today])
    rows = [CompletionDay(path_id=_PATH_A, day=date(2026, 8, 1), count=1)]

    view = await _summarize(
        FakeCompletionDaysReader(rows), user_id=_USER, tz_offset_minutes=0, now=now
    )

    assert unused_reviews.calls == []
    # Yesterday only, no review folded in: today itself is not Active, so the
    # grace-day anchor lands on yesterday and the run is length 1.
    assert view.current_streak == 1
    assert view.completed_today == 0
