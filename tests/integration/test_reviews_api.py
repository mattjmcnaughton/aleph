"""Contract tests for the Reviews API (Phase 3 TDD §11), real Postgres.

The queue/summary/grade half of ticket 5's surface — `GET /reviews/summary`,
`GET /reviews/queue`, `POST /reviews` — against the real HTTP surface, real
cookie auth (mirroring `test_progress_api.py`), and a real database. Ticket 4's
three drafting routes are not this file's concern; cards are seeded directly as
already-**kept** rows (D6: a kept card is just a row with `kept_at` set), the
same posture `test_progress_api.py` takes toward completions and
`test_flashcards_schema.py` already takes toward cards.

Every scenario anchors `due_on` relative to `datetime.now(UTC).date()` (never a
hardcoded date) and reads at the default `tz_offset_minutes=0` — the case
where the learner's local day and the UTC day are always the same day, so
nothing here depends on what hour of day the suite happens to run at
(`test_progress_api.py`'s same posture).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.models import (
    Flashcard,
    FlashcardReview,
    Lesson,
    LessonGenerationState,
    Level,
    Path,
    PathStatus,
    Unit,
)

from .conftest import _disable_flag_globally, _enable_flag_globally

if TYPE_CHECKING:
    from fastapi import FastAPI

SUMMARY_URL = "/api/v1/reviews/summary"
QUEUE_URL = "/api/v1/reviews/queue"
GRADE_URL = "/api/v1/reviews"

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="reviews-owner-subject",
    username="reviews-owner",
    display_name="Reviews Owner",
    email="reviews-owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="reviews-other-subject",
    username="reviews-other",
    display_name="Reviews Other",
    email="reviews-other@example.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def flashcards_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the `flashcards` flag on globally for one test (TDD D10).

    Local to this file rather than `conftest.py` (out of this ticket's scope,
    and every other flag fixture already lives there) — the same
    `_enable_flag_globally` lever `tutor_flag_enabled`/`streaks_flag_enabled`
    pull, reused rather than respelled.
    """
    _enable_flag_globally(monkeypatch, "flashcards")


@pytest.fixture
def flashcards_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror fixture: `flashcards` starts dark (D10), but a test that
    wants to prove the `404` gate closes it explicitly rather than assuming a
    default that could change out from under this file."""
    _disable_flag_globally(monkeypatch, "flashcards")


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, identity: AuthIdentity
) -> uuid.UUID:
    """Complete the stubbed OIDC callback; returns the local account id."""
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: identity)
    response = await client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 303, response.text
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200, session.text
    return uuid.UUID(session.json()["user"]["id"])


async def _seed_path_and_lesson(
    user_id: uuid.UUID,
    *,
    topic: str = "Rust ownership",
    generated_at: datetime | None = None,
) -> tuple[uuid.UUID, uuid.UUID, str, str, datetime]:
    """Commit a bare, `ready` path + one generated lesson.

    Returns `(path_id, lesson_id, lesson_title, path_title, generated_at)` —
    everything a kept card's four `source_*` columns need (D12).
    """
    stamp = generated_at or datetime.now(UTC)
    async with db.async_session() as session:
        path = Path(
            user_id=user_id,
            topic=topic,
            level=Level.SOME_EXPERIENCE,
            status=PathStatus.READY,
        )
        unit = Unit(path=path, position=1, title="Foundations", summary="The basics.")
        lesson = Lesson(
            unit=unit,
            path=path,
            position_in_path=1,
            position_in_unit=1,
            title=f"{topic} — lesson 1",
            generation_state=LessonGenerationState.GENERATED,
            read_passage="Some content.",
            generated_at=stamp,
        )
        session.add_all([path, unit, lesson])
        await session.commit()
        return path.id, lesson.id, lesson.title, topic, stamp


async def _seed_kept_card(
    *,
    user_id: uuid.UUID,
    due_on: date,
    rung: int = 0,
    front: str = "front",
    back: str = "back",
    source_path_id: uuid.UUID | None,
    source_lesson_id: uuid.UUID | None,
    source_lesson_title: str = "A lesson",
    source_path_title: str = "A path",
    source_generated_at: datetime | None = None,
) -> uuid.UUID:
    """A **kept** card row (D6), seeded directly — ticket 4's drafting routes
    are not this file's concern (mirrors `test_flashcards_schema.py`'s
    `_kept_card`)."""
    async with db.async_session() as session:
        card = Flashcard(
            user_id=user_id,
            front=front,
            back=back,
            kept_at=datetime.now(UTC),
            rung=rung,
            due_on=due_on,
            source_lesson_id=source_lesson_id,
            source_path_id=source_path_id,
            source_lesson_title=source_lesson_title,
            source_path_title=source_path_title,
            source_generated_at=source_generated_at or datetime.now(UTC),
        )
        session.add(card)
        await session.commit()
        return card.id


async def _review_count(card_id: uuid.UUID) -> int:
    async with db.async_session() as session:
        result = await session.scalar(
            select(func.count())
            .select_from(FlashcardReview)
            .where(FlashcardReview.card_id == card_id)
        )
        return result or 0


async def _queue(client: AsyncClient, *, path_id: uuid.UUID | None = None) -> dict:
    params: dict[str, str] = {}
    if path_id is not None:
        params["path_id"] = str(path_id)
    response = await client.get(QUEUE_URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def _summary(client: AsyncClient) -> dict:
    response = await client.get(SUMMARY_URL)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# The flag gate (D10)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_off_hides_every_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_disabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        summary = await client.get(SUMMARY_URL)
        queue = await client.get(QUEUE_URL)
        grade = await client.post(
            GRADE_URL,
            json={
                "card_id": str(uuid.uuid4()),
                "grade": "got_it",
                "rung_before": 0,
                "tz_offset_minutes": 0,
            },
        )

    for response in (summary, queue, grade):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_flag_on_serves_every_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        summary = await _summary(client)
        queue = await _queue(client)

    assert summary == {
        "today": datetime.now(UTC).date().isoformat(),
        "due_count": 0,
        "estimated_minutes": 0,
        "paths": [],
    }
    assert queue["cards"] == []
    assert queue["total"] == 0


@pytest.mark.anyio
async def test_out_of_range_offset_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        too_high = await client.get(QUEUE_URL, params={"tz_offset_minutes": 901})

    assert too_high.status_code == 422, too_high.text
    assert too_high.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- #
# Ticket 5, finding #1: `summary.due_count` must count only unsatisfied cards
# — the invariant `queue.total - queue.completed == summary.due_count` — so it
# can actually reach zero through work (§8: the pill "hidden entirely at
# zero"; §15: the *Due today* card disappearing when the set is done).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_summary_due_count_drops_as_cards_are_graded_and_reaches_zero(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        (
            path_id,
            lesson_id,
            lesson_title,
            path_title,
            generated_at,
        ) = await _seed_path_and_lesson(user_id)

        for i in range(3):
            await _seed_kept_card(
                user_id=user_id,
                due_on=today,
                source_path_id=path_id,
                source_lesson_id=lesson_id,
                source_lesson_title=lesson_title,
                source_path_title=path_title,
                source_generated_at=generated_at,
                front=f"card {i}",
            )

        before_queue = await _queue(client)
        before_summary = await _summary(client)
        assert before_summary["due_count"] == 3
        assert (
            before_queue["total"] - before_queue["completed"]
            == before_summary["due_count"]
        )

        # Grade two of the three `got_it` — one card is left unsatisfied.
        for card in before_queue["cards"][:2]:
            response = await client.post(
                GRADE_URL,
                json={
                    "card_id": card["card_id"],
                    "grade": "got_it",
                    "rung_before": card["rung"],
                    "tz_offset_minutes": 0,
                },
            )
            assert response.status_code == 200, response.text

        mid_queue = await _queue(client)
        mid_summary = await _summary(client)
        # The bug this pins: before the fix `due_count` stayed `3` all day —
        # the whole day's set, never shrinking as cards were graded — which
        # made the header pill unable to ever reach zero through work.
        assert mid_summary["due_count"] == 1
        assert mid_queue["total"] - mid_queue["completed"] == mid_summary["due_count"]
        # The per-path chip moves too, not just the global count (§8: the
        # `Review N` chips must not "stay at their start-of-day value all
        # day").
        assert mid_summary["paths"] == [{"path_id": str(path_id), "due_count": 1}]

        # Grade the last card too — the pill must be able to reach zero.
        last_card = mid_queue["cards"][0]
        response = await client.post(
            GRADE_URL,
            json={
                "card_id": last_card["card_id"],
                "grade": "got_it",
                "rung_before": last_card["rung"],
                "tz_offset_minutes": 0,
            },
        )
        assert response.status_code == 200, response.text

        zero_summary = await _summary(client)

    assert zero_summary["due_count"] == 0
    assert zero_summary["estimated_minutes"] == 0
    assert zero_summary["paths"] == []


# --------------------------------------------------------------------------- #
# W25: the daily queue caps at ten with eleven due, and holds across requests
# — including across a grade, which is the derivation's real claim (§5.3/§15,
# ticket 8's finding #2): a bug in the "reviewed today" arm or the `COALESCE`
# would let the eleventh candidate silently take a graded card's place instead
# of the graded card staying pinned in the set. The two ungraded `GET`s below
# (`first == second`) are necessary but not sufficient — they were the whole
# of this test before this extension, and they cannot catch that class of bug
# because nothing has been graded yet when they run.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W25")
async def test_the_queue_caps_at_ten_and_holds_across_a_grade_at_the_cap(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        (
            path_id,
            lesson_id,
            lesson_title,
            path_title,
            generated_at,
        ) = await _seed_path_and_lesson(user_id)

        for i in range(11):
            await _seed_kept_card(
                user_id=user_id,
                due_on=today - timedelta(days=i),  # a spread of overdue cards
                source_path_id=path_id,
                source_lesson_id=lesson_id,
                source_lesson_title=lesson_title,
                source_path_title=path_title,
                source_generated_at=generated_at,
                front=f"card {i}",
            )

        first = await _queue(client)
        second = await _queue(client)

        assert first["total"] == 10  # capped, not eleven
        assert len(first["cards"]) == 10
        assert first == second  # pinned for the rest of the day (D3), pre-grade

        # Grade two of the ten — one `got_it`, one `again` (§11's own words:
        # "derive today's ten, grade three, re-derive"; two is enough to
        # exercise both the satisfied and the still-due arm of the pin).
        got_it_card, again_card = first["cards"][0], first["cards"][1]
        graded_ids = {got_it_card["card_id"], again_card["card_id"]}

        got_it_response = await client.post(
            GRADE_URL,
            json={
                "card_id": got_it_card["card_id"],
                "grade": "got_it",
                "rung_before": got_it_card["rung"],
                "tz_offset_minutes": 0,
            },
        )
        assert got_it_response.status_code == 200, got_it_response.text
        again_response = await client.post(
            GRADE_URL,
            json={
                "card_id": again_card["card_id"],
                "grade": "again",
                "rung_before": again_card["rung"],
                "tz_offset_minutes": 0,
            },
        )
        assert again_response.status_code == 200, again_response.text

        third = await _queue(client)

    # `total` still ten — the eleventh candidate has not slid into either
    # graded card's slot. `completed` is exactly the one `got_it` (the `again`
    # stays unsatisfied and therefore still appears in `cards`). The union of
    # what `third` still returns plus the two graded ids reconstructs the
    # exact original ten.
    assert third["total"] == 10
    assert third["completed"] == 1
    third_card_ids = {card["card_id"] for card in third["cards"]}
    assert third_card_ids | graded_ids == {card["card_id"] for card in first["cards"]}


# --------------------------------------------------------------------------- #
# W26: a lapse resurfaces without costing a slot.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W26")
async def test_a_lapse_resurfaces_behind_the_untouched_cards_without_costing_a_slot(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        (
            path_id,
            lesson_id,
            lesson_title,
            path_title,
            generated_at,
        ) = await _seed_path_and_lesson(user_id)

        for i in range(3):
            await _seed_kept_card(
                user_id=user_id,
                due_on=today,
                rung=2,
                source_path_id=path_id,
                source_lesson_id=lesson_id,
                source_lesson_title=lesson_title,
                source_path_title=path_title,
                source_generated_at=generated_at,
                front=f"card {i}",
            )

        before = await _queue(client)
        assert before["total"] == 3
        first_card = before["cards"][0]

        graded = await client.post(
            GRADE_URL,
            json={
                "card_id": first_card["card_id"],
                "grade": "again",
                "rung_before": first_card["rung"],
                "tz_offset_minutes": 0,
            },
        )
        assert graded.status_code == 200, graded.text

        after = await _queue(client)

    assert after["total"] == 3  # unchanged — a lapse never costs a slot (D8)
    assert after["completed"] == 0  # a lapse is not satisfied
    # The graded card is still present, but now behind the two untouched ones
    # (D8's "later in the session" — never-attempted first).
    after_fronts = [card["front"] for card in after["cards"]]
    assert after_fronts[-1] == first_card["front"]
    assert set(after_fronts) == {card["front"] for card in before["cards"]}


# --------------------------------------------------------------------------- #
# Grading: 409 stale_rung appends no review row; 409 not_due for an off-schedule
# card; another learner's cards never appear.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_stale_rung_before_is_409_and_appends_no_review_row(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        (
            path_id,
            lesson_id,
            lesson_title,
            path_title,
            generated_at,
        ) = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            rung=2,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lesson_title,
            source_path_title=path_title,
            source_generated_at=generated_at,
        )

        response = await client.post(
            GRADE_URL,
            json={
                "card_id": str(card_id),
                "grade": "got_it",
                "rung_before": 99,  # stale
                "tz_offset_minutes": 0,
            },
        )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"]["reason"] == "stale_rung"
    assert await _review_count(card_id) == 0


@pytest.mark.anyio
async def test_grading_a_card_not_due_today_is_409_not_due(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        (
            path_id,
            lesson_id,
            lesson_title,
            path_title,
            generated_at,
        ) = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today + timedelta(days=30),
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lesson_title,
            source_path_title=path_title,
            source_generated_at=generated_at,
        )

        response = await client.post(
            GRADE_URL,
            json={
                "card_id": str(card_id),
                "grade": "got_it",
                "rung_before": 0,
                "tz_offset_minutes": 0,
            },
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "not_due"


@pytest.mark.anyio
async def test_grading_an_unknown_card_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        response = await client.post(
            GRADE_URL,
            json={
                "card_id": str(uuid.uuid4()),
                "grade": "got_it",
                "rung_before": 0,
                "tz_offset_minutes": 0,
            },
        )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_another_learners_cards_never_appear(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as owner, _client(app) as other:
        owner_id = await _sign_in(owner, monkeypatch, OWNER)
        other_id = await _sign_in(other, monkeypatch, OTHER)

        (
            owner_path,
            owner_lesson,
            owner_lt,
            owner_pt,
            owner_gen,
        ) = await _seed_path_and_lesson(owner_id, topic="Owner's path")
        (
            other_path,
            other_lesson,
            other_lt,
            other_pt,
            other_gen,
        ) = await _seed_path_and_lesson(other_id, topic="Other's path")
        owner_card = await _seed_kept_card(
            user_id=owner_id,
            due_on=today,
            source_path_id=owner_path,
            source_lesson_id=owner_lesson,
            source_lesson_title=owner_lt,
            source_path_title=owner_pt,
            source_generated_at=owner_gen,
        )
        other_card = await _seed_kept_card(
            user_id=other_id,
            due_on=today,
            source_path_id=other_path,
            source_lesson_id=other_lesson,
            source_lesson_title=other_lt,
            source_path_title=other_pt,
            source_generated_at=other_gen,
        )

        owner_queue = await _queue(owner)

        # Grading the other learner's card as the owner is a 404, not a 409 —
        # ownership is decided before anything about scheduling is.
        cross_grade = await owner.post(
            GRADE_URL,
            json={
                "card_id": str(other_card),
                "grade": "got_it",
                "rung_before": 0,
                "tz_offset_minutes": 0,
            },
        )

    assert [card["card_id"] for card in owner_queue["cards"]] == [str(owner_card)]
    assert cross_grade.status_code == 404, cross_grade.text


# --------------------------------------------------------------------------- #
# D12: the citation degrades honestly — deleted path, regenerated lesson.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W27")
async def test_deleting_the_source_path_leaves_the_card_reviewable_and_degraded(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        (
            path_id,
            lesson_id,
            lesson_title,
            path_title,
            generated_at,
        ) = await _seed_path_and_lesson(user_id)
        await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lesson_title,
            source_path_title=path_title,
            source_generated_at=generated_at,
        )

        delete = await client.delete(f"/api/v1/paths/{path_id}")
        assert delete.status_code == 204, delete.text

        queue = await _queue(client)

    assert queue["total"] == 1
    source = queue["cards"][0]["source"]
    assert source["kind"] == "degraded"
    assert "lesson_id" not in source
    assert source["lesson_title"] == lesson_title
    assert source["path_title"] == path_title


@pytest.mark.anyio
async def test_regenerating_the_source_lesson_degrades_the_citation_too(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        (
            path_id,
            lesson_id,
            lesson_title,
            path_title,
            generated_at,
        ) = await _seed_path_and_lesson(user_id)
        await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lesson_title,
            source_path_title=path_title,
            source_generated_at=generated_at,
        )

        # Simulate a regeneration (a 2B Revision, a retry — anything that moves
        # `generated_at`) directly: the citation's judgement is exactly this
        # comparison (D12), so mutating the stamp is the honest arrange step.
        async with db.async_session() as session:
            lesson = await session.get(Lesson, lesson_id)
            assert lesson is not None
            lesson.generated_at = generated_at + timedelta(hours=1)
            await session.commit()

        queue = await _queue(client)

    source = queue["cards"][0]["source"]
    assert source["kind"] == "degraded"
    assert "lesson_id" not in source
