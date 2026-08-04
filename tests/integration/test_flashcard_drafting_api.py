"""Contract tests for the Flashcard Drafting API (Phase 3 TDD §11), real Postgres.

Ticket 4's three drafting routes — `POST /lessons/{id}/flashcard-drafts`, `GET
.../flashcard-drafts`, `POST .../flashcard-drafts/keep` — against the real HTTP
surface, real cookie auth (mirroring `test_reviews_api.py`/`test_paths_api.py`),
and a real database. The background claim + run is driven through
`FlashcardDraftingService`'s injected `_spawn`/`_resolve_model` seams exactly
the way `test_paths_api.py`/`test_lessons_api.py` drive `GenerationOrchestrator`
— a `CollectingSpawn` the test drains deterministically, and the deterministic
stub model at the model-resolution seam (fakes over mocks: no network, no
non-determinism).

Workflow tag (TDD §11): `@pytest.mark.workflow("W24")` on the trigger -> poll ->
keep-two flow — the backend half of "finishing a lesson produces a due card";
the full browser journey (shift-due, reload) is the Playwright W24 spec.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.config import settings
from aleph.models import (
    Flashcard,
    FlashcardDraftRun,
    FlashcardDraftRunState,
    Lesson,
    LessonGenerationState,
    Level,
    Path,
    PathStatus,
    QuickCheck,
    Unit,
)
from aleph.services import flashcard_drafting as fd_module
from aleph.services.stub_model import FORCE_DRAFT_FAILURE

from .conftest import (
    CollectingSpawn,
    _disable_flag_globally,
    _enable_flag_globally,
    stub_resolver,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="drafting-owner-subject",
    username="drafting-owner",
    display_name="Drafting Owner",
    email="drafting-owner@example.com",
)
ADMIN = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="drafting-admin-subject",
    username="drafting-admin",
    display_name="Drafting Admin",
    email="drafting-admin@mattjmcnaughton.com",
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

    Local to this file (as `test_reviews_api.py`'s own copy is) rather than
    `conftest.py` — every other flag fixture already lives there, but this
    ticket's edit scope does not include that file.
    """
    _enable_flag_globally(monkeypatch, "flashcards")


@pytest.fixture
def flashcards_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_flag_globally(monkeypatch, "flashcards")


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    """Point the module-level drafting service at the stub model + a drainable
    spawn — the `test_paths_api.py::spawn` fixture, respelled for
    `flashcard_drafting_service` (the same singleton-patching seam AL-041's
    lifespan itself uses via `bind_runtime` for the lesson orchestrator).
    """
    collector = CollectingSpawn()
    monkeypatch.setattr(
        fd_module.flashcard_drafting_service, "_resolve_model", stub_resolver()
    )
    monkeypatch.setattr(fd_module.flashcard_drafting_service, "_spawn", collector)
    return collector


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, identity: AuthIdentity
) -> uuid.UUID:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: identity)
    response = await client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 303, response.text
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200, session.text
    return uuid.UUID(session.json()["user"]["id"])


async def _seed_lesson(
    user_id: uuid.UUID,
    *,
    topic: str = "Rust ownership",
    completed: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Commit a `ready` path + one generated lesson (+ its Quick check).

    Returns `(path_id, lesson_id)`. `completed=False` seeds an available (not
    yet completed) lesson — the `409 lesson_not_complete` fixture.
    """
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
            read_passage="Ownership tracks who is responsible for a value. " * 10,
            generated_at=datetime.now(UTC),
            completed_at=datetime.now(UTC) if completed else None,
        )
        quick_check = QuickCheck(
            lesson=lesson,
            stem="What keyword declares a mutable binding?",
            options=["let", "mut", "const", "var"],
            correct_index=1,
            explanation="`mut` marks a binding mutable; `let` alone is immutable.",
        )
        session.add_all([path, unit, lesson, quick_check])
        await session.commit()
        return path.id, lesson.id


async def _draft_run_row(lesson_id: uuid.UUID) -> FlashcardDraftRun | None:
    async with db.async_session() as session:
        return await session.get(FlashcardDraftRun, lesson_id)


async def _drafts_for_lesson(lesson_id: uuid.UUID) -> list[Flashcard]:
    async with db.async_session() as session:
        result = await session.execute(
            select(Flashcard).where(Flashcard.source_lesson_id == lesson_id)
        )
        return list(result.scalars())


async def _seed_draft_run(
    lesson_id: uuid.UUID,
    *,
    state: FlashcardDraftRunState,
    started_at: datetime | None = None,
) -> None:
    async with db.async_session() as session:
        session.add(
            FlashcardDraftRun(
                lesson_id=lesson_id,
                state=state,
                started_at=started_at or datetime.now(UTC),
                error=None,
            )
        )
        await session.commit()


TRIGGER_URL = "/api/v1/lessons/{lesson_id}/flashcard-drafts"
POLL_URL = "/api/v1/lessons/{lesson_id}/flashcard-drafts"
KEEP_URL = "/api/v1/lessons/{lesson_id}/flashcard-drafts/keep"


# --------------------------------------------------------------------------- #
# The flag gate (D10)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_off_hides_every_drafting_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_disabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        lesson_id = uuid.uuid4()

        trigger = await client.post(TRIGGER_URL.format(lesson_id=lesson_id))
        poll = await client.get(POLL_URL.format(lesson_id=lesson_id))
        keep = await client.post(
            KEEP_URL.format(lesson_id=lesson_id),
            json={"kept_ids": [], "tz_offset_minutes": 0},
        )

    for response in (trigger, poll, keep):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- #
# W24: trigger -> poll -> keep two -> exactly two survive, the discarded two gone.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W24")
async def test_trigger_poll_keep_two_leaves_exactly_two_kept_rows(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    today = datetime.now(UTC).date()
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        _path_id, lesson_id = await _seed_lesson(user_id)

        trigger = await client.post(TRIGGER_URL.format(lesson_id=lesson_id))
        assert trigger.status_code == 202, trigger.text
        assert trigger.json()["id"] == str(lesson_id)
        await spawn.drain()

        polled = await client.get(POLL_URL.format(lesson_id=lesson_id))
        assert polled.status_code == 200, polled.text
        body = polled.json()
        assert body["state"] == "generated"
        assert len(body["cards"]) >= 2  # the PRD §6 band is 3-5

        # Deliberately requested in the *reverse* of creation order: the
        # smaller fix this pins is that `kept_ids` in the response echoes the
        # **request's** order (`dict.fromkeys`, order-preserving dedup), not
        # an arbitrary one — a `sorted(...)` comparison here would conceal a
        # `set()` round-trip scrambling it.
        kept_ids = [card["id"] for card in body["cards"][:2]][::-1]
        kept = await client.post(
            KEEP_URL.format(lesson_id=lesson_id),
            json={"kept_ids": kept_ids, "tz_offset_minutes": 0},
        )
        assert kept.status_code == 200, kept.text
        assert kept.json()["kept_ids"] == kept_ids

    survivors = await _drafts_for_lesson(lesson_id)
    assert {str(card.id) for card in survivors} == set(kept_ids)  # discarded ones gone
    for card in survivors:
        assert card.kept_at is not None
        assert card.rung == 0
        assert card.due_on == today + timedelta(days=1)  # ladder[0] — never today (D1)


# --------------------------------------------------------------------------- #
# 409: drafting an incomplete lesson.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_drafting_an_incomplete_lesson_is_409(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        _path_id, lesson_id = await _seed_lesson(user_id, completed=False)

        response = await client.post(TRIGGER_URL.format(lesson_id=lesson_id))

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"]["reason"] == "lesson_not_complete"
    assert await _draft_run_row(lesson_id) is None  # no claim was ever attempted


# --------------------------------------------------------------------------- #
# 404: an unowned/unknown lesson, never 403.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_triggering_an_unknown_lesson_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, flashcards_flag_enabled: None
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        response = await client.post(TRIGGER_URL.format(lesson_id=uuid.uuid4()))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- #
# D7: a double trigger while generating is a no-op; a failed run is re-claimable.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_trigger_while_already_generating_is_a_structural_noop(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        _path_id, lesson_id = await _seed_lesson(user_id)

        # Simulate an in-flight run: a first `POST` already won the claim and
        # its background task has not resolved yet (§5.2 #2).
        in_flight_started_at = datetime.now(UTC)
        await _seed_draft_run(
            lesson_id,
            state=FlashcardDraftRunState.GENERATING,
            started_at=in_flight_started_at,
        )

        response = await client.post(TRIGGER_URL.format(lesson_id=lesson_id))
        assert response.status_code == 202, response.text  # still 202 (D7's no-op)
        await spawn.drain()

    # The claim lost the race (the row's `started_at` is not stale), so nothing
    # was persisted and the row's stamp is exactly what it was before.
    row = await _draft_run_row(lesson_id)
    assert row is not None
    assert row.state == FlashcardDraftRunState.GENERATING
    assert row.started_at == in_flight_started_at
    assert await _drafts_for_lesson(lesson_id) == []


@pytest.mark.anyio
async def test_a_trigger_once_generated_is_a_structural_noop(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        _path_id, lesson_id = await _seed_lesson(user_id)

        first = await client.post(TRIGGER_URL.format(lesson_id=lesson_id))
        assert first.status_code == 202
        await spawn.drain()
        generated_row = await _draft_run_row(lesson_id)
        assert generated_row is not None
        assert generated_row.state == FlashcardDraftRunState.GENERATED
        cards_before = await _drafts_for_lesson(lesson_id)

        second = await client.post(TRIGGER_URL.format(lesson_id=lesson_id))
        assert (
            second.status_code == 202
        )  # D7: `generated` is terminal, never re-claimed
        await spawn.drain()

    row_after = await _draft_run_row(lesson_id)
    assert row_after is not None
    assert row_after.started_at == generated_row.started_at  # never re-stamped
    cards_after = await _drafts_for_lesson(lesson_id)
    assert {c.id for c in cards_after} == {
        c.id for c in cards_before
    }  # no duplicate drafts


@pytest.mark.anyio
async def test_a_failed_run_is_re_claimable(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        # The stub's forced-failure sentinel (services/stub_model.py) — the
        # topic carries it because the drafting prompt reads `path.topic`.
        _path_id, lesson_id = await _seed_lesson(
            user_id, topic=f"Rust ownership {FORCE_DRAFT_FAILURE}"
        )

        first = await client.post(TRIGGER_URL.format(lesson_id=lesson_id))
        assert first.status_code == 202
        await spawn.drain()
        failed_row = await _draft_run_row(lesson_id)
        assert failed_row is not None
        assert failed_row.state == FlashcardDraftRunState.FAILED

        polled = await client.get(POLL_URL.format(lesson_id=lesson_id))
        assert polled.json()["state"] == "failed"

        second = await client.post(TRIGGER_URL.format(lesson_id=lesson_id))
        assert second.status_code == 202
        await spawn.drain()

    retried_row = await _draft_run_row(lesson_id)
    assert retried_row is not None
    assert (
        retried_row.state == FlashcardDraftRunState.FAILED
    )  # the sentinel fails every time
    # A NEW claim stamp proves the retry re-claimed and re-ran it (mirrors
    # `test_lessons_api.py`'s W8 assertion), not a silent no-op.
    assert retried_row.started_at > failed_row.started_at


# --------------------------------------------------------------------------- #
# BLOCKER (D7/§5.6): a stale `generating` run must poll as `failed` — never a
# permanent dead spinner — and be re-claimable by a fresh `POST` once it does.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_stale_generating_run_polls_as_failed_and_is_reclaimable(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    """The BLOCKER this ticket exists to fix: a crashed drafting worker (a Fly
    machine restart, a task cancelled at shutdown — neither caught by the
    service's own top-level `except Exception`) leaves the row `generating`
    forever with no further `POST` ever coming. Before the fix, `_load_drafts`
    mapped `run.state` straight through with no stale check, so the poll
    reported `"generating"` forever and the frontend's retry affordance — only
    rendered on the `failed` branch — was never reachable: a permanent dead
    spinner despite the row being re-claimable all along.

    Seeds a `generating` run with a `started_at` well past
    `settings.generation_stale_after_seconds`, polls (expecting the effective
    `"failed"`, and the raw row itself untouched — a `GET` never writes), then
    re-`POST`s (the existing retry affordance) and confirms the claim actually
    succeeds through `claim_draft_run`'s `WHERE state = 'failed'` arm.
    """
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        _path_id, lesson_id = await _seed_lesson(user_id)

        stale_started_at = datetime.now(UTC) - timedelta(
            seconds=settings.generation_stale_after_seconds + 120
        )
        await _seed_draft_run(
            lesson_id,
            state=FlashcardDraftRunState.GENERATING,
            started_at=stale_started_at,
        )

        polled = await client.get(POLL_URL.format(lesson_id=lesson_id))
        assert polled.status_code == 200, polled.text
        assert polled.json() == {"state": "failed", "cards": []}

        # A poll is a read: the row itself is untouched by it.
        stale_row = await _draft_run_row(lesson_id)
        assert stale_row is not None
        assert stale_row.state == FlashcardDraftRunState.GENERATING
        assert stale_row.started_at == stale_started_at

        retried = await client.post(TRIGGER_URL.format(lesson_id=lesson_id))
        assert retried.status_code == 202, retried.text
        await spawn.drain()

        polled_again = await client.get(POLL_URL.format(lesson_id=lesson_id))
        assert polled_again.status_code == 200, polled_again.text
        assert polled_again.json()["state"] == "generated"

    retried_row = await _draft_run_row(lesson_id)
    assert retried_row is not None
    assert retried_row.state == FlashcardDraftRunState.GENERATED
    assert retried_row.started_at > stale_started_at  # a real re-claim, not a no-op
    assert len(await _drafts_for_lesson(lesson_id)) >= 1


# --------------------------------------------------------------------------- #
# Keeping a draft id belonging to another lesson: 404, mutates nothing.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_keeping_another_lessons_draft_id_is_404_and_mutates_nothing(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        _path_a, lesson_a = await _seed_lesson(user_id, topic="Path A")
        _path_b, lesson_b = await _seed_lesson(user_id, topic="Path B")

        triggered = await client.post(TRIGGER_URL.format(lesson_id=lesson_a))
        assert triggered.status_code == 202
        await spawn.drain()

        drafts_a_before = await _drafts_for_lesson(lesson_a)
        assert len(drafts_a_before) >= 1
        foreign_id = str(drafts_a_before[0].id)

        # Keep lesson A's draft id — through lesson B's keep route.
        response = await client.post(
            KEEP_URL.format(lesson_id=lesson_b),
            json={"kept_ids": [foreign_id], "tz_offset_minutes": 0},
        )

    assert response.status_code == 404, response.text
    # Lesson A's pending drafts are untouched: same count, still all pending.
    drafts_a_after = await _drafts_for_lesson(lesson_a)
    assert {c.id for c in drafts_a_after} == {c.id for c in drafts_a_before}
    assert all(c.kept_at is None for c in drafts_a_after)


# --------------------------------------------------------------------------- #
# Keep `[]` is "Skip — keep none": every pending draft of the lesson is gone.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_keeping_none_discards_every_pending_draft(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        _path_id, lesson_id = await _seed_lesson(user_id)

        await client.post(TRIGGER_URL.format(lesson_id=lesson_id))
        await spawn.drain()
        assert len(await _drafts_for_lesson(lesson_id)) >= 1

        response = await client.post(
            KEEP_URL.format(lesson_id=lesson_id),
            json={"kept_ids": [], "tz_offset_minutes": 0},
        )

    assert response.status_code == 200, response.text
    assert response.json()["kept_ids"] == []
    assert await _drafts_for_lesson(lesson_id) == []


# --------------------------------------------------------------------------- #
# 429: over the daily drafting cap; admins are exempt.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_drafting_over_the_daily_cap_is_429(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    monkeypatch.setattr(settings, "flashcard_drafts_per_day", 1)
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        _path_a, lesson_a = await _seed_lesson(user_id, topic="Path A")
        _path_b, lesson_b = await _seed_lesson(user_id, topic="Path B")

        # Consume today's one unit of quota.
        first = await client.post(TRIGGER_URL.format(lesson_id=lesson_a))
        assert first.status_code == 202
        await spawn.drain()

        second = await client.post(TRIGGER_URL.format(lesson_id=lesson_b))

    assert second.status_code == 429, second.text
    body = second.json()
    assert body["error"]["code"] == "rate_limited"
    assert await _draft_run_row(lesson_b) is None  # the cap denies before any claim


@pytest.mark.anyio
async def test_an_admin_is_exempt_from_the_drafting_cap(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    flashcards_flag_enabled: None,
) -> None:
    monkeypatch.setattr(settings, "flashcard_drafts_per_day", 1)
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, ADMIN)
        _path_a, lesson_a = await _seed_lesson(user_id, topic="Path A")
        _path_b, lesson_b = await _seed_lesson(user_id, topic="Path B")

        first = await client.post(TRIGGER_URL.format(lesson_id=lesson_a))
        assert first.status_code == 202
        await spawn.drain()

        second = await client.post(TRIGGER_URL.format(lesson_id=lesson_b))
        assert second.status_code == 202  # admin never capped
