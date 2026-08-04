"""Contract tests for the card-management API (AL-410 / issue #156 §5-7), real
Postgres.

The browse/edit/delete surface `GET /flashcards`, `PATCH /flashcards/{id}`,
`DELETE /flashcards/{id}` — added to the same flag-gated router
`test_reviews_api.py` already covers, so this file mirrors its shape: real
HTTP, real cookie auth (`_sign_in`), cards seeded directly as already-**kept**
rows (D6) rather than through the drafting pipeline, which is not this
ticket's concern.

Every scenario anchors `due_on` relative to `datetime.now(UTC).date()` (never
a hardcoded date) — `test_progress_api.py`/`test_reviews_api.py`'s same
posture, so nothing here depends on what hour of day the suite happens to run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.dtos.flashcards import CARD_FRONT_MAX_CHARS
from aleph.models import (
    Flashcard,
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

CARDS_URL = "/api/v1/flashcards"
QUEUE_URL = "/api/v1/reviews/queue"
SUMMARY_URL = "/api/v1/reviews/summary"
GRADE_URL = "/api/v1/reviews"
PROGRESS_URL = "/api/v1/progress/summary"

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="cards-owner-subject",
    username="cards-owner",
    display_name="Cards Owner",
    email="cards-owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="cards-other-subject",
    username="cards-other",
    display_name="Cards Other",
    email="cards-other@example.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def flashcards_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the `flashcards` flag on globally for one test (TDD D10) — the
    same lever `test_reviews_api.py`'s own fixture of this name pulls,
    respelled locally per that file's own convention (each integration file
    defines its own copy rather than sharing one across files)."""
    _enable_flag_globally(monkeypatch, "flashcards")


@pytest.fixture
def flashcards_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror fixture: `flashcards` now launches on by default (D10), but a
    test proving the `404` gate closes it explicitly rather than assuming a
    default."""
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
    """Commit a bare, `ready` path + one generated lesson — the `test_reviews_api.py`
    helper of the same name, respelled locally (returns `(path_id, lesson_id,
    lesson_title, path_title, generated_at)`, everything a kept card's four
    `source_*` columns need, D12)."""
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
    kept_at: datetime | None = None,
    edited_at: datetime | None = None,
    deleted_at: datetime | None = None,
    source_path_id: uuid.UUID | None,
    source_lesson_id: uuid.UUID | None,
    source_lesson_title: str = "A lesson",
    source_path_title: str = "A path",
    source_generated_at: datetime | None = None,
) -> uuid.UUID:
    """A **kept** card row (D6), seeded directly — the drafting routes are not
    this file's concern, mirroring `test_reviews_api.py`'s own helper."""
    async with db.async_session() as session:
        card = Flashcard(
            user_id=user_id,
            front=front,
            back=back,
            kept_at=kept_at or datetime.now(UTC),
            rung=rung,
            due_on=due_on,
            edited_at=edited_at,
            deleted_at=deleted_at,
            source_lesson_id=source_lesson_id,
            source_path_id=source_path_id,
            source_lesson_title=source_lesson_title,
            source_path_title=source_path_title,
            source_generated_at=source_generated_at or datetime.now(UTC),
        )
        session.add(card)
        await session.commit()
        return card.id


async def _seed_draft(
    *,
    user_id: uuid.UUID,
    source_path_id: uuid.UUID | None,
    source_lesson_id: uuid.UUID | None,
    front: str = "draft front",
    back: str = "draft back",
    source_lesson_title: str = "A lesson",
    source_path_title: str = "A path",
    source_generated_at: datetime | None = None,
) -> uuid.UUID:
    """A **draft** row (`kept_at IS NULL`, D6) — never kept, so it must never
    surface on the card-management routes."""
    async with db.async_session() as session:
        card = Flashcard(
            user_id=user_id,
            front=front,
            back=back,
            kept_at=None,
            rung=None,
            due_on=None,
            source_lesson_id=source_lesson_id,
            source_path_id=source_path_id,
            source_lesson_title=source_lesson_title,
            source_path_title=source_path_title,
            source_generated_at=source_generated_at or datetime.now(UTC),
        )
        session.add(card)
        await session.commit()
        return card.id


async def _list_cards(client: AsyncClient, **params: str | int) -> dict:
    response = await client.get(CARDS_URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# The flag gate (D10) — inherited by construction, no gate of AL-410's own.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_off_hides_every_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_disabled: None
) -> None:
    """`uuid.uuid4()` alone proves nothing for the PATCH/DELETE arms: a random
    id is already `404` with the flag **on** (see
    `test_patching_an_unknown_card_is_404`/`test_deleting_an_unknown_card_is_404`,
    below) because it does not exist — deleting the router-level flag gate
    would leave those two arms green. Using a **real, kept card the
    signed-in learner owns** is what actually pins the gate: with the flag on
    this id is a legitimate `200`/`204`, so the `404` seen here can only come
    from `require_flashcards_enabled`, never from ownership or existence.
    """
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        list_response = await client.get(CARDS_URL)
        patch_response = await client.patch(
            f"{CARDS_URL}/{card_id}", json={"front": "a", "back": "b"}
        )
        # Reuses the same owned `card_id` the PATCH attempt above targeted:
        # the gate rejects both requests before either ever reaches the
        # service, so the card is never actually mutated or deleted by this
        # test, and one seeded row is enough to prove all three arms.
        delete_response = await client.delete(f"{CARDS_URL}/{card_id}")

    for response in (list_response, patch_response, delete_response):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_flag_on_serves_the_list_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        body = await _list_cards(client)

    assert body == {"cards": [], "next_cursor": None}


# --------------------------------------------------------------------------- #
# Ownership: 404, never 403 (§4 item 3's posture, held for AL-410 too).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_another_learners_cards_never_appear_in_the_list(
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
        await _seed_kept_card(
            user_id=other_id,
            due_on=today,
            source_path_id=other_path,
            source_lesson_id=other_lesson,
            source_lesson_title=other_lt,
            source_path_title=other_pt,
            source_generated_at=other_gen,
        )

        owner_list = await _list_cards(owner)

    assert [card["id"] for card in owner_list["cards"]] == [str(owner_card)]


@pytest.mark.anyio
async def test_editing_another_learners_card_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as owner, _client(app) as other:
        await _sign_in(owner, monkeypatch, OWNER)
        other_id = await _sign_in(other, monkeypatch, OTHER)
        (
            other_path,
            other_lesson,
            other_lt,
            other_pt,
            other_gen,
        ) = await _seed_path_and_lesson(other_id)
        other_card = await _seed_kept_card(
            user_id=other_id,
            due_on=today,
            source_path_id=other_path,
            source_lesson_id=other_lesson,
            source_lesson_title=other_lt,
            source_path_title=other_pt,
            source_generated_at=other_gen,
        )

        response = await owner.patch(
            f"{CARDS_URL}/{other_card}", json={"front": "new front", "back": "new back"}
        )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_deleting_another_learners_card_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as owner, _client(app) as other:
        await _sign_in(owner, monkeypatch, OWNER)
        other_id = await _sign_in(other, monkeypatch, OTHER)
        (
            other_path,
            other_lesson,
            other_lt,
            other_pt,
            other_gen,
        ) = await _seed_path_and_lesson(other_id)
        other_card = await _seed_kept_card(
            user_id=other_id,
            due_on=today,
            source_path_id=other_path,
            source_lesson_id=other_lesson,
            source_lesson_title=other_lt,
            source_path_title=other_pt,
            source_generated_at=other_gen,
        )

        response = await owner.delete(f"{CARDS_URL}/{other_card}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- #
# Browse: ordering, filters, pagination, and what never appears.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_list_orders_most_recently_kept_first(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        base = datetime.now(UTC)
        oldest = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            kept_at=base,
            front="oldest",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )
        newest = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            kept_at=base + timedelta(minutes=5),
            front="newest",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        body = await _list_cards(client)

    assert [card["id"] for card in body["cards"]] == [str(newest), str(oldest)]


@pytest.mark.anyio
async def test_a_draft_never_appears_in_the_list(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        kept = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )
        await _seed_draft(
            user_id=user_id,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        body = await _list_cards(client)

    assert [card["id"] for card in body["cards"]] == [str(kept)]


@pytest.mark.anyio
async def test_a_deleted_card_never_appears_in_the_list_queue_or_summary(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        delete_response = await client.delete(f"{CARDS_URL}/{card_id}")
        assert delete_response.status_code == 204, delete_response.text

        list_body = await _list_cards(client)
        queue_response = await client.get(QUEUE_URL)
        summary_response = await client.get(SUMMARY_URL)
        grade_response = await client.post(
            GRADE_URL,
            json={
                "card_id": str(card_id),
                "grade": "got_it",
                "rung_before": 0,
                "tz_offset_minutes": 0,
            },
        )

    assert list_body["cards"] == []
    assert queue_response.json()["cards"] == []
    assert summary_response.json()["due_count"] == 0
    assert grade_response.status_code == 404, grade_response.text
    assert grade_response.json()["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_path_id_and_q_filter_the_same_list(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    """`path_id`/`q` narrow one endpoint, never a second one (§5)."""
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_a, lesson_a, lt_a, pt_a, gen_a = await _seed_path_and_lesson(
            user_id, topic="Path A"
        )
        path_b, lesson_b, lt_b, pt_b, gen_b = await _seed_path_and_lesson(
            user_id, topic="Path B"
        )
        card_a = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="ownership basics",
            source_path_id=path_a,
            source_lesson_id=lesson_a,
            source_lesson_title=lt_a,
            source_path_title=pt_a,
            source_generated_at=gen_a,
        )
        await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="borrow checker",
            source_path_id=path_b,
            source_lesson_id=lesson_b,
            source_lesson_title=lt_b,
            source_path_title=pt_b,
            source_generated_at=gen_b,
        )

        by_path = await _list_cards(client, path_id=str(path_a))
        by_query = await _list_cards(client, q="ownership")

    assert [card["id"] for card in by_path["cards"]] == [str(card_a)]
    assert [card["id"] for card in by_query["cards"]] == [str(card_a)]


@pytest.mark.anyio
async def test_q_matches_either_front_or_back_case_insensitively(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        front_match = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="What is OWNERSHIP?",
            back="A memory model.",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )
        back_match = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="What is a borrow?",
            back="A reference under ownership rules.",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )
        await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="Unrelated",
            back="Nothing to do with the search term.",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        body = await _list_cards(client, q="ownership")

    assert {card["id"] for card in body["cards"]} == {
        str(front_match),
        str(back_match),
    }


@pytest.mark.anyio
async def test_a_percent_in_q_is_a_literal_not_a_wildcard(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    """An unescaped `%` would silently match everything — §2's own stated risk."""
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        percent_card = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="Discounts are 50% off",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )
        await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="No percent sign here",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        body = await _list_cards(client, q="50%")

    assert [card["id"] for card in body["cards"]] == [str(percent_card)]


@pytest.mark.anyio
async def test_an_underscore_in_q_is_a_literal_not_a_single_char_wildcard(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    """`ILIKE`'s *other* wildcard: unescaped, `_` matches any single
    character, not just a literal underscore. `_escape_like` (the repository)
    already escapes it exactly like `%` — the code is correct, but nothing
    pinned this half of it until now (finding 7's second test gap)."""
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        underscore_card = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="snake_case_variable",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )
        # If `_` were read as a single-char wildcard, searching `snake_case`
        # would also match this card (any one character standing in for the
        # `_`) — it must not.
        await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="snakeXcaseXvariable",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        body = await _list_cards(client, q="snake_case")

    assert [card["id"] for card in body["cards"]] == [str(underscore_card)]


@pytest.mark.anyio
async def test_pagination_is_stable_across_a_concurrent_delete(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    """Cursor, not offset (§2): deleting a row on the **already-read page 1**
    must not shift page 2.

    Deliberately deletes `expected_order[1]` — a row **page 1 already
    returned** — never a row page 2 would be the first to show. That
    distinction is what makes this test actually pin cursor-vs-offset: an
    earlier version of this test deleted the *first row page 2 would have
    returned*, and an offset scheme recomputes "skip 2" by counting rows in
    whatever the table looks like *now* — with one page-1 row gone, `OFFSET 2`
    over the four remaining rows lands on `expected_order[3]`, the exact same
    answer a cursor gives, so that version passed under both schemes and
    proved nothing. Deleting a page-1 row instead makes the two schemes
    genuinely disagree: `OFFSET 2` over the four-row remainder still starts
    counting from zero and lands one row too far in (`expected_order[3]`,
    skipping `expected_order[2]` entirely), while a cursor keyed on
    `(kept_at, id) < (page 1's last row)` never counts rows at all — it is a
    predicate against a value, unaffected by how many rows now sit ahead of
    it — and correctly still returns `expected_order[2]` first.
    """
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        base = datetime.now(UTC)
        card_ids = []
        for i in range(5):
            card_id = await _seed_kept_card(
                user_id=user_id,
                due_on=today,
                kept_at=base + timedelta(minutes=i),
                front=f"card {i}",
                source_path_id=path_id,
                source_lesson_id=lesson_id,
                source_lesson_title=lt,
                source_path_title=pt,
                source_generated_at=gen,
            )
            card_ids.append(card_id)
        # Most-recently-kept first: reverse chronological.
        expected_order = list(reversed(card_ids))

        first_page = await _list_cards(client, limit=2)
        assert [c["id"] for c in first_page["cards"]] == [
            str(expected_order[0]),
            str(expected_order[1]),
        ]
        assert first_page["next_cursor"] is not None

        # Delete the *second* row of the page just read — a row page 2 was
        # never going to return under either scheme — between the two page
        # reads.
        delete_response = await client.delete(f"{CARDS_URL}/{expected_order[1]}")
        assert delete_response.status_code == 204, delete_response.text

        second_page = await _list_cards(
            client, limit=2, cursor=first_page["next_cursor"]
        )
        assert second_page["next_cursor"] is not None

        third_page = await _list_cards(
            client, limit=2, cursor=second_page["next_cursor"]
        )

    # The cursor must return exactly `expected_order[2]`/`[3]` next — an
    # offset scheme would instead skip straight to `expected_order[3]`/`[4]`
    # here (see the docstring above), silently dropping `expected_order[2]`
    # from the learner's browse entirely.
    assert [c["id"] for c in second_page["cards"]] == [
        str(expected_order[2]),
        str(expected_order[3]),
    ]
    assert [c["id"] for c in third_page["cards"]] == [str(expected_order[4])]
    assert third_page["next_cursor"] is None


@pytest.mark.anyio
async def test_a_malformed_cursor_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        response = await client.get(CARDS_URL, params={"cursor": "not-a-real-cursor"})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.anyio
async def test_limit_out_of_range_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        response = await client.get(CARDS_URL, params={"limit": 51})

    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------- #
# Edit (§3): shape validation, and what it never touches.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_patch_updates_text_and_sets_edited_at(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="old front",
            back="old back",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        response = await client.patch(
            f"{CARDS_URL}/{card_id}",
            json={"front": "new front", "back": "new back"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["front"] == "new front"
    assert body["back"] == "new back"
    assert body["edited_at"] is not None


@pytest.mark.anyio
async def test_patch_leaves_rung_and_due_on_untouched(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    due_on = today + timedelta(days=3)
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=due_on,
            rung=2,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        response = await client.patch(
            f"{CARDS_URL}/{card_id}",
            json={"front": "fixed a typo", "back": "still the same answer"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rung"] == 2
    assert body["due_on"] == due_on.isoformat()


@pytest.mark.anyio
async def test_patch_with_empty_front_is_422_and_mutates_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="original front",
            back="original back",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        response = await client.patch(
            f"{CARDS_URL}/{card_id}", json={"front": "   ", "back": "original back"}
        )
        list_body = await _list_cards(client)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert list_body["cards"][0]["front"] == "original front"
    assert list_body["cards"][0]["edited_at"] is None


@pytest.mark.anyio
async def test_patch_over_the_word_cap_is_422_and_mutates_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    """Mirrors `test_patch_with_empty_front_is_422_and_mutates_nothing`'s
    re-read (the issue's acceptance criterion is `422` **and** "mutates
    nothing", not `422` alone) — the empty-front case was the only one of the
    three shape violations that actually checked the second half."""
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="original front",
            back="original back",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )
        over_cap_front = " ".join(["word"] * 26)  # FlashcardCaps default max is 25

        response = await client.patch(
            f"{CARDS_URL}/{card_id}", json={"front": over_cap_front, "back": "back"}
        )
        list_body = await _list_cards(client)

    assert response.status_code == 422, response.text
    assert list_body["cards"][0]["front"] == "original front"
    assert list_body["cards"][0]["back"] == "original back"
    assert list_body["cards"][0]["edited_at"] is None


@pytest.mark.anyio
async def test_patch_with_identical_sides_is_422_and_mutates_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    """See `test_patch_over_the_word_cap_is_422_and_mutates_nothing`'s
    docstring: the same "mutates nothing" gap, for the identical-sides
    violation."""
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="original front",
            back="original back",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        response = await client.patch(
            f"{CARDS_URL}/{card_id}",
            json={"front": "same text", "back": "Same Text"},
        )
        list_body = await _list_cards(client)

    assert response.status_code == 422, response.text
    assert list_body["cards"][0]["front"] == "original front"
    assert list_body["cards"][0]["back"] == "original back"
    assert list_body["cards"][0]["edited_at"] is None


@pytest.mark.anyio
async def test_patch_with_a_grossly_oversized_front_is_422_and_mutates_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    """AL-410 review finding 1: `within_word_cap` bounds *word* count via
    `str.split()`, so a single whitespace-free token of any length sailed
    through every existing shape predicate untouched — verified live before
    this fix: a `PATCH` with a 500,000-character `front` returned `200` and
    persisted the whole thing, and every later `GET /flashcards` page shipped
    it. `CardFrontStr`'s `max_length` (`dtos/flashcards.py`) is the
    character-level backstop; this pins it as an ordinary route-level `422`
    that mutates nothing, the same posture every other shape violation gets.

    Deliberately far larger than :data:`CARD_FRONT_MAX_CHARS`, not merely one
    character over it — this test's whole point is the pathological payload
    the finding actually reported (500,000 characters), not a boundary probe.
    """
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            front="original front",
            back="original back",
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )
        oversized_front = "x" * (CARD_FRONT_MAX_CHARS + 499_000)

        response = await client.patch(
            f"{CARDS_URL}/{card_id}", json={"front": oversized_front, "back": "back"}
        )
        list_body = await _list_cards(client)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert list_body["cards"][0]["front"] == "original front"
    assert list_body["cards"][0]["back"] == "original back"
    assert list_body["cards"][0]["edited_at"] is None


@pytest.mark.anyio
async def test_patching_a_draft_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        draft_id = await _seed_draft(
            user_id=user_id,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        response = await client.patch(
            f"{CARDS_URL}/{draft_id}", json={"front": "a", "back": "b"}
        )

    assert response.status_code == 404, response.text


@pytest.mark.anyio
async def test_patching_a_deleted_card_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )
        delete_response = await client.delete(f"{CARDS_URL}/{card_id}")
        assert delete_response.status_code == 204, delete_response.text

        response = await client.patch(
            f"{CARDS_URL}/{card_id}", json={"front": "a", "back": "b"}
        )

    assert response.status_code == 404, response.text


@pytest.mark.anyio
async def test_patching_an_unknown_card_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        response = await client.patch(
            f"{CARDS_URL}/{uuid.uuid4()}", json={"front": "a", "back": "b"}
        )

    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- #
# Delete (§1/§3): soft, honest on a double-tap, and the streak invariant this
# whole design exists for.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_deleting_an_unknown_card_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        response = await client.delete(f"{CARDS_URL}/{uuid.uuid4()}")

    assert response.status_code == 404, response.text


@pytest.mark.anyio
async def test_a_double_tapped_delete_is_404_not_a_silent_second_success(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        first = await client.delete(f"{CARDS_URL}/{card_id}")
        second = await client.delete(f"{CARDS_URL}/{card_id}")

    assert first.status_code == 204, first.text
    assert second.status_code == 404, second.text


@pytest.mark.anyio
async def test_deleting_a_card_with_reviews_leaves_the_daily_streak_unchanged(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
    streaks_flag_enabled: None,
) -> None:
    """The whole justification for soft delete (migration 0011's docstring,
    §1 of the plan): a graded review is what makes today an Active day (D11's
    union), and deleting the card it belongs to must never take that day back.

    **If a future change makes this a hard delete, this test must fail
    loudly**: `flashcard_reviews` is `ON DELETE CASCADE` from `flashcards`
    (migration 0010), so a hard delete would erase the review this test just
    graded, `review_days_for_user` would no longer see today, and
    `current_streak` would drop — silently, from the learner's point of view,
    since nothing else in the product tells them a delete touched their
    streak.
    """
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        path_id, lesson_id, lt, pt, gen = await _seed_path_and_lesson(user_id)
        card_id = await _seed_kept_card(
            user_id=user_id,
            due_on=today,
            source_path_id=path_id,
            source_lesson_id=lesson_id,
            source_lesson_title=lt,
            source_path_title=pt,
            source_generated_at=gen,
        )

        grade_response = await client.post(
            GRADE_URL,
            json={
                "card_id": str(card_id),
                "grade": "got_it",
                "rung_before": 0,
                "tz_offset_minutes": 0,
            },
        )
        assert grade_response.status_code == 200, grade_response.text

        before_progress = await client.get(PROGRESS_URL)
        assert before_progress.status_code == 200, before_progress.text
        before_streak = before_progress.json()["current_streak"]
        # Sanity check on the arrange step: reviewing today must already have
        # made today an Active day, or the assertion below would pass
        # vacuously (both sides zero) without proving anything.
        assert before_streak >= 1

        delete_response = await client.delete(f"{CARDS_URL}/{card_id}")
        assert delete_response.status_code == 204, delete_response.text

        after_progress = await client.get(PROGRESS_URL)

    assert after_progress.status_code == 200, after_progress.text
    after_streak = after_progress.json()["current_streak"]
    assert after_streak == before_streak, (
        "deleting a card must never erase the Active day its review created — "
        "see migration 0011's docstring ('why soft delete') for the mechanism "
        "a hard delete would break."
    )
