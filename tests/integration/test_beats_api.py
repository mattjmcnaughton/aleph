"""Contract tests for the Beats & Briefs API (AL-522, issue #172, epic #163,
Phase 6 TDD §6).

The learner-facing HTTP surface over `services/briefing.py` (AL-521), exercised
end-to-end against real Postgres with a fake `Retriever` and a scripted
`FunctionModel` at the model-resolution seam — `test_briefing.py`'s own fakes
(`_FakeRetriever`, `_PipelineResponder`, `_doc`, `_finding_payload`,
`_resolver`), imported rather than respelled (the `test_reconciler.py` /
`test_generation.py` precedent for sharing fakes across integration test
files). Auth is the real cookie flow (a stubbed OIDC code exchange, mirroring
`test_paths_api.py`).

`services.briefing.briefing_service` is the module-level singleton the router
imports directly (`generation_orchestrator`'s own shape); its `_spawn` and
`_retriever`/`_resolve_model` seams are patched in place per test, exactly as
`test_paths_api.py`'s `spawn` fixture patches `generation_orchestrator`.

The `analyst` flag is launched (TDD D12) and defaults on, so every test that
exercises the live surface requests `analyst_flag_enabled` (`conftest.py`) for
the same reason `streaks`/`flashcards` coverage still requests theirs —
stating which flag a test's subject hangs off, redundantly with the code
default. The one flag-off test requests `analyst_flag_disabled` instead: since
launch, off is no longer any test's starting point for free, so proving the
`404` gate means *closing* the flag rather than omitting a fixture.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select, update

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.config import settings
from aleph.models import (
    Beat,
    BeatResearchRun,
    BeatResearchState,
    Brief,
    BriefKind,
    Level,
)
from aleph.repositories import BeatRepository, BriefRepository
from aleph.repositories.briefs import NewSource
from aleph.services import briefing as briefing_module

from .conftest import CollectingSpawn
from .test_briefing import (
    _doc,
    _FakeRetriever,
    _finding_payload,
    _PipelineResponder,
    _resolver,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="beats-owner-subject",
    username="beats-owner",
    display_name="Owner User",
    email="owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="beats-other-subject",
    username="beats-other",
    display_name="Other User",
    email="other@example.com",
)
ADMIN = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="beats-admin-subject",
    username="beats-admin",
    display_name="Admin User",
    email="admin@mattjmcnaughton.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    """Point the module-level `briefing_service` at a drainable spawn.

    Unlike `test_paths_api.py`'s `spawn` fixture, this does NOT swap
    `_resolve_model`/`_retriever` — most tests here never need the pipeline
    to actually run (ownership, flag gate, rate limits, the read ping,
    `builds_on`), so those seams stay at their production-inert defaults
    (`_UnconfiguredRetriever`, which raises loudly if ever reached
    unexpectedly). Tests that DO need a real run call `_wire_pipeline` below
    to bind a fake retriever + scripted model on top of this same collector.
    """
    collector = CollectingSpawn()
    monkeypatch.setattr(briefing_module.briefing_service, "_spawn", collector)
    return collector


def _wire_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    retriever: _FakeRetriever,
    responder: _PipelineResponder,
) -> None:
    """Bind a fake `Retriever` + scripted `FunctionModel` onto the singleton
    `briefing_service` for one test — the part of `test_paths_api.py`'s
    `spawn` fixture this module's own `spawn` fixture above deliberately
    leaves out, so only the tests that actually run the pipeline pay for it.
    """
    monkeypatch.setattr(briefing_module.briefing_service, "_retriever", retriever)
    monkeypatch.setattr(
        briefing_module.briefing_service,
        "_resolve_model",
        _resolver(FunctionModel(responder)),
    )


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


# --------------------------------------------------------------------------- #
# Arrange helpers — direct repository access for fixtures the HTTP surface
# alone cannot cheaply set up (a Beat mid-history, a specific Brief number).
# --------------------------------------------------------------------------- #


async def _seed_beat(
    *,
    user_id: uuid.UUID,
    topic: str = "EU AI regulation",
    anchor_weekday: int = 0,
) -> uuid.UUID:
    async with db.async_session() as session:
        beat = await BeatRepository(session).create(
            user_id=user_id,
            topic=topic,
            level=Level.SOME_EXPERIENCE,
            anchor_weekday=anchor_weekday,
        )
        await session.commit()
        return beat.id


async def _seed_published(
    beat_id: uuid.UUID, *, number: int, published_on: date, url: str
) -> uuid.UUID:
    async with db.async_session() as session:
        brief = await BriefRepository(session).create_published(
            beat_id=beat_id,
            number=number,
            published_at=datetime(
                published_on.year, published_on.month, published_on.day, 9, tzinfo=UTC
            ),
            published_on=published_on,
            title=f"Brief #{number}",
            body_markdown=f"Body of Brief #{number}.",
            claims=[f"claim {number}"],
            sources=[
                _new_source(url=url, title=f"Source for Brief #{number}"),
            ],
        )
        await session.commit()
        return brief.id


def _new_source(*, url: str, title: str) -> NewSource:
    return NewSource(
        url=url,
        publisher="Northlake Gazette",
        title=title,
        published_on=date(2026, 7, 1),
    )


async def _seed_skipped(beat_id: uuid.UUID, *, published_on: date) -> uuid.UUID:
    async with db.async_session() as session:
        brief = await BriefRepository(session).create_skipped(
            beat_id=beat_id,
            published_at=datetime(
                published_on.year, published_on.month, published_on.day, 9, tzinfo=UTC
            ),
            published_on=published_on,
            skip_line="Nothing material.",
        )
        await session.commit()
        return brief.id


async def _reload_beat(beat_id: uuid.UUID) -> Beat:
    async with db.async_session() as session:
        beat = await session.get(Beat, beat_id)
        assert beat is not None
        return beat


async def _reload_brief(brief_id: uuid.UUID) -> Brief:
    async with db.async_session() as session:
        brief = await session.get(Brief, brief_id)
        assert brief is not None
        return brief


async def _count_beats() -> int:
    async with db.async_session() as session:
        return len((await session.execute(select(Beat))).scalars().all())


# --------------------------------------------------------------------------- #
# W29 — deploy claims the first run in the same request; a published Brief
# with resolving Sources follows once drained.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W29")
async def test_deploy_claims_the_first_run_and_returns_immediately(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {
                "findings": [
                    _finding_payload("The backlash arrived", ["https://example.com/a"])
                ]
            },
        ),
        analyst=(
            "cited_urls",
            {
                "title": "The backlash arrived",
                "body_markdown": "Northlake published a review.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    _wire_pipeline(monkeypatch, retriever=retriever, responder=responder)

    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        resp = await client.post(
            "/api/v1/beats",
            json={
                "topic": "EU AI regulation",
                "level": "some_experience",
                "anchor_weekday": 0,
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["topic"] == "EU AI regulation"
        assert body["level"] == "some_experience"
        assert body["anchor_weekday"] == 0
        assert body["cadence"] == "weekly"
        assert body["guidance"] is None
        assert body["refusal_message"] is None
        assert body["entries"] == []
        # The first run is ALREADY claimed in this same response — not
        # merely triggered, not left for a follow-up poll to discover.
        assert body["research_state"] == "researching"
        assert body["research_started_at"] is not None
        beat_id = body["id"]

        # Immediate: nothing above awaited the spawned pipeline.
        assert len(spawn.tasks) == 1

        await spawn.drain()

        detail = await client.get(f"/api/v1/beats/{beat_id}")
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()
        assert detail_body["research_state"] == "idle"
        assert len(detail_body["entries"]) == 1
        entry = detail_body["entries"][0]
        assert entry["kind"] == "published"
        assert entry["number"] == 1
        assert entry["title"] == "The backlash arrived"
        assert entry["read_at"] is None

        brief_resp = await client.get(f"/api/v1/briefs/{entry['id']}")
        assert brief_resp.status_code == 200, brief_resp.text
        brief_body = brief_resp.json()
        assert brief_body["number"] == 1
        assert brief_body["body_markdown"] == "Northlake published a review."
        assert brief_body["builds_on"] is None  # Brief #1 has nothing below it
        assert len(brief_body["sources"]) == 1
        assert brief_body["sources"][0]["url"] == "https://example.com/a"


# --------------------------------------------------------------------------- #
# Flag off -> 404 before any work, and NO research is spawned.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_off_hides_every_route_and_spawns_nothing(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_disabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        brief_id = await _seed_published(
            beat_id, number=1, published_on=date(2026, 7, 20), url="https://x.test/a"
        )

        deploy = await client.post(
            "/api/v1/beats",
            json={"topic": "x", "level": "new_to_it", "anchor_weekday": 0},
        )
        list_resp = await client.get("/api/v1/beats")
        detail_resp = await client.get(f"/api/v1/beats/{beat_id}")
        delete_resp = await client.delete(f"/api/v1/beats/{beat_id}")
        retry_resp = await client.post(f"/api/v1/beats/{beat_id}/retry")
        brief_resp = await client.get(f"/api/v1/briefs/{brief_id}")
        read_resp = await client.post(
            f"/api/v1/briefs/{brief_id}/read", json={"marker": "opened"}
        )

    for response in (
        deploy,
        list_resp,
        detail_resp,
        delete_resp,
        retry_resp,
        brief_resp,
        read_resp,
    ):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"

    # No work happened at all: no task spawned, the Beat/Brief rows and the
    # research state are exactly as seeded, and the count of Beats is
    # unchanged (the gated ``deploy`` created no row).
    assert spawn.tasks == []
    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE
    brief = await _reload_brief(brief_id)
    assert brief.read_at is None
    assert await _count_beats() == 1


# --------------------------------------------------------------------------- #
# Ownership: another learner's Beat or Brief is 404, never 403.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_non_owner_gets_404_everywhere(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as owner, _client(app) as other:
        owner_id = await _sign_in(owner, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=owner_id)
        brief_id = await _seed_published(
            beat_id, number=1, published_on=date(2026, 7, 20), url="https://x.test/a"
        )

        await _sign_in(other, monkeypatch, OTHER)
        assert (await other.get(f"/api/v1/beats/{beat_id}")).status_code == 404
        assert (await other.post(f"/api/v1/beats/{beat_id}/retry")).status_code == 404
        assert (await other.delete(f"/api/v1/beats/{beat_id}")).status_code == 404
        assert (await other.get(f"/api/v1/briefs/{brief_id}")).status_code == 404
        assert (
            await other.post(
                f"/api/v1/briefs/{brief_id}/read", json={"marker": "opened"}
            )
        ).status_code == 404
        # The other learner's own list is empty (isolation).
        assert (await other.get("/api/v1/beats")).json()["beats"] == []

    # None of that touched the owner's data.
    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE
    brief = await _reload_brief(brief_id)
    assert brief.read_at is None


# --------------------------------------------------------------------------- #
# Beat cap -> 429 through the shared envelope. The daily research cap never
# 429s a GET.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_beat_cap_returns_429_envelope_and_creates_no_row(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    monkeypatch.setattr(settings, "max_beats_per_learner", 1)
    retriever = _FakeRetriever(documents=[])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    _wire_pipeline(monkeypatch, retriever=retriever, responder=responder)

    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        first = await client.post(
            "/api/v1/beats",
            json={"topic": "one", "level": "new_to_it", "anchor_weekday": 0},
        )
        assert first.status_code == 202, first.text
        await spawn.drain()

        second = await client.post(
            "/api/v1/beats",
            json={"topic": "two", "level": "new_to_it", "anchor_weekday": 1},
        )
        assert second.status_code == 429, second.text
        body = second.json()
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["message"]
        assert body["error"]["request_id"] == second.headers["X-Request-ID"]

    # The denied create inserted no row.
    assert await _count_beats() == 1


@pytest.mark.anyio
async def test_admin_is_exempt_from_the_beat_cap(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    monkeypatch.setattr(settings, "max_beats_per_learner", 1)
    retriever = _FakeRetriever(documents=[])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    _wire_pipeline(monkeypatch, retriever=retriever, responder=responder)

    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)

        first = await client.post(
            "/api/v1/beats",
            json={"topic": "one", "level": "new_to_it", "anchor_weekday": 0},
        )
        assert first.status_code == 202
        await spawn.drain()

        second = await client.post(
            "/api/v1/beats",
            json={"topic": "two", "level": "new_to_it", "anchor_weekday": 1},
        )
        assert second.status_code == 202, second.text
        await spawn.drain()

    assert await _count_beats() == 2


@pytest.mark.anyio
async def test_daily_research_cap_never_429s_a_get(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    """TDD §7: "the research cap is checked inside the drain … never at the
    route" — hitting it degrades a `GET`'s own drain to "no research this
    time", and the route still answers `200`."""
    monkeypatch.setattr(settings, "rate_limit_brief_research_per_day", 1)
    retriever = _FakeRetriever(documents=[])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    _wire_pipeline(monkeypatch, retriever=retriever, responder=responder)

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)

        # Spend the one unit of daily capacity via a real deploy + drain.
        first = await client.post(
            "/api/v1/beats",
            json={"topic": "spends the cap", "level": "new_to_it", "anchor_weekday": 0},
        )
        assert first.status_code == 202
        await spawn.drain()

        # A second, independently due Beat (seeded directly, so its own
        # cadence is immediately claimable): its arrival-triggered drain
        # cannot claim — capacity is spent — but the GET still succeeds.
        second_beat_id = await _seed_beat(user_id=user_id, topic="capacity spent")
        resp = await client.get(f"/api/v1/beats/{second_beat_id}")

    assert resp.status_code == 200, resp.text
    beat = await _reload_beat(second_beat_id)
    assert beat.research_state is BeatResearchState.IDLE  # never claimed


# --------------------------------------------------------------------------- #
# FIX 1 (code review, AL-522): a GET that triggers a drain must REFLECT the
# claim that drain made — never the stale, pre-drain "idle" a naive
# read-then-drain ordering would return, which `lib/polling.ts` treats as
# terminal (idle is a Beat's pre-run AND post-success state) and would never
# resume polling from. FIX 5: `hold=True` — a *local* CollectingSpawn, not
# the module `spawn` fixture (which defaults to `hold=False`) — so the
# spawned pipeline task is parked and can never race these assertions, which
# read DB state written by the request itself, exactly the discipline
# `tests/integration/conftest.py` and AL-521's own drain tests already use.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_get_beat_reflects_the_claim_its_own_drain_made(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    held_spawn = CollectingSpawn(hold=True)
    monkeypatch.setattr(briefing_module.briefing_service, "_spawn", held_spawn)

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        # A fresh, idle Beat with no entries: cadence is due immediately
        # (D4), so THIS request's own drain WILL claim it.
        beat_id = await _seed_beat(user_id=user_id)

        before = await _reload_beat(beat_id)
        assert before.research_state is BeatResearchState.IDLE

        resp = await client.get(f"/api/v1/beats/{beat_id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The response reflects the claim THIS request's own drain just
    # committed — never a stale pre-drain "idle".
    assert body["research_state"] == "researching"
    assert body["research_started_at"] is not None

    after = await _reload_beat(beat_id)
    assert after.research_state is BeatResearchState.RESEARCHING
    assert after.research_started_at is not None
    assert len(held_spawn.tasks) == 1
    await held_spawn.cancel_pending()


@pytest.mark.anyio
async def test_list_beats_reflects_the_claim_its_own_drain_made(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    held_spawn = CollectingSpawn(hold=True)
    monkeypatch.setattr(briefing_module.briefing_service, "_spawn", held_spawn)

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)

        resp = await client.get("/api/v1/beats")

    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["beats"] if r["id"] == str(beat_id))
    assert row["research_state"] == "researching"
    assert row["research_started_at"] is not None

    after = await _reload_beat(beat_id)
    assert after.research_state is BeatResearchState.RESEARCHING
    assert len(held_spawn.tasks) == 1
    await held_spawn.cancel_pending()


# --------------------------------------------------------------------------- #
# The read ping: idempotent per marker; `opened`/`sources` independent.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_read_ping_is_idempotent_and_markers_are_independent(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        brief_id = await _seed_published(
            beat_id, number=1, published_on=date(2026, 7, 20), url="https://x.test/a"
        )

        first_open = await client.post(
            f"/api/v1/briefs/{brief_id}/read", json={"marker": "opened"}
        )
        assert first_open.status_code == 204, first_open.text

    once = await _reload_brief(brief_id)
    assert once.read_at is not None
    assert once.sources_seen_at is None  # independent of "opened"
    first_stamp = once.read_at

    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        second_open = await client.post(
            f"/api/v1/briefs/{brief_id}/read", json={"marker": "opened"}
        )
        assert second_open.status_code == 204
        sources_ping = await client.post(
            f"/api/v1/briefs/{brief_id}/read", json={"marker": "sources"}
        )
        assert sources_ping.status_code == 204

    twice = await _reload_brief(brief_id)
    assert twice.read_at == first_stamp  # a repeat ping never moves it
    assert twice.sources_seen_at is not None  # the independent marker fired


@pytest.mark.anyio
async def test_read_ping_bad_marker_is_422(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        brief_id = await _seed_published(
            beat_id, number=1, published_on=date(2026, 7, 20), url="https://x.test/a"
        )

        resp = await client.post(
            f"/api/v1/briefs/{brief_id}/read", json={"marker": "not-a-real-marker"}
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- #
# code-review FIX 2 (AL-531): a read ping against a Skipped Brief must not
# stamp `read_at`/`sources_seen_at` — `repositories/briefs.py`'s own
# docstring states this as a load-bearing invariant ("a Skipped row's
# `read_at` can never be stamped … no read ping is ever sent for one") that
# `unread_counts_by_beat`'s published-only filter depends on, and
# `brief_read_rate.sql` (opened ÷ published) would otherwise let a Skipped id
# enter the numerator with no denominator row to match it. Reproduces the
# reviewer's own probe: before this fix, `POST /briefs/{skipped_id}/read
# {"marker":"opened"}` returned `204` and the row came back with `read_at`
# stamped — there was no server-side guard at all.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_read_ping_against_a_skipped_brief_is_a_noop(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        skipped_id = await _seed_skipped(beat_id, published_on=date(2026, 7, 20))

        opened_resp = await client.post(
            f"/api/v1/briefs/{skipped_id}/read", json={"marker": "opened"}
        )
        sources_resp = await client.post(
            f"/api/v1/briefs/{skipped_id}/read", json={"marker": "sources"}
        )

    # A `204` no-op — deliberately the SAME shape as a repeat ping on an
    # already-read published Brief (`read_brief`'s own docstring), never a
    # `404`: `GET /briefs/{id}` already resolves a Skipped id successfully,
    # so this route stays consistent with that precedent.
    assert opened_resp.status_code == 204, opened_resp.text
    assert sources_resp.status_code == 204, sources_resp.text

    skipped = await _reload_brief(skipped_id)
    assert skipped.kind is BriefKind.SKIPPED
    # The invariant `repositories/briefs.py` states outright: neither column
    # was ever touched, no matter how many pings targeted this id.
    assert skipped.read_at is None
    assert skipped.sources_seen_at is None


@pytest.mark.anyio
async def test_read_ping_still_stamps_a_published_brief_next_to_a_skipped_one(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    """The `kind == PUBLISHED` guard added to `mark_read`/`mark_sources_seen`
    (FIX 2) must not weaken the existing first-write-wins guard for a
    genuinely published Brief on the same Beat — the two predicates are
    ANDed, not substituted for one another."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        await _seed_skipped(beat_id, published_on=date(2026, 7, 13))
        published_id = await _seed_published(
            beat_id, number=1, published_on=date(2026, 7, 20), url="https://x.test/a"
        )

        resp = await client.post(
            f"/api/v1/briefs/{published_id}/read", json={"marker": "opened"}
        )
    assert resp.status_code == 204, resp.text

    published = await _reload_brief(published_id)
    assert published.kind is BriefKind.PUBLISHED
    assert published.read_at is not None


# --------------------------------------------------------------------------- #
# `builds_on` resolves to the previous published Brief; null on Brief #1 and
# on every Skipped entry.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_builds_on_resolves_and_is_null_for_first_brief_and_skipped(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        first_id = await _seed_published(
            beat_id, number=1, published_on=date(2026, 7, 6), url="https://x.test/a"
        )
        skipped_id = await _seed_skipped(beat_id, published_on=date(2026, 7, 13))
        second_id = await _seed_published(
            beat_id, number=2, published_on=date(2026, 7, 20), url="https://x.test/b"
        )

        first_resp = await client.get(f"/api/v1/briefs/{first_id}")
        second_resp = await client.get(f"/api/v1/briefs/{second_id}")
        skipped_resp = await client.get(f"/api/v1/briefs/{skipped_id}")

    assert first_resp.status_code == 200
    assert first_resp.json()["builds_on"] is None

    assert second_resp.status_code == 200
    second_builds_on = second_resp.json()["builds_on"]
    assert second_builds_on is not None
    assert second_builds_on == {
        "id": str(first_id),
        "number": 1,
        "published_on": "2026-07-06",
    }

    assert skipped_resp.status_code == 200
    skipped_body = skipped_resp.json()
    assert skipped_body["builds_on"] is None
    assert skipped_body["number"] is None
    assert skipped_body["title"] is None
    assert skipped_body["body_markdown"] is None
    assert skipped_body["sources"] == []


# --------------------------------------------------------------------------- #
# The rail is one list of both kinds, newest first (D2/§6).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_beat_detail_rail_interleaves_both_kinds_newest_first(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        await _seed_published(
            beat_id, number=1, published_on=date(2026, 7, 6), url="https://x.test/a"
        )
        await _seed_skipped(beat_id, published_on=date(2026, 7, 13))
        await _seed_published(
            beat_id, number=2, published_on=date(2026, 7, 20), url="https://x.test/b"
        )

        resp = await client.get(f"/api/v1/beats/{beat_id}")

    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert [(e["kind"], e.get("number"), e["published_on"]) for e in entries] == [
        ("published", 2, "2026-07-20"),
        ("skipped", None, "2026-07-13"),
        ("published", 1, "2026-07-06"),
    ]
    # The discriminated shapes on the wire: a skipped row carries no
    # ``title``/``read_at`` keys at all, and a published row carries no
    # ``skip_line`` key at all (TDD §6's own example).
    published_row, skipped_row = entries[0], entries[1]
    assert "title" in published_row
    assert "read_at" in published_row
    assert "skip_line" not in published_row
    assert "skip_line" in skipped_row
    assert "title" not in skipped_row
    assert "read_at" not in skipped_row


# --------------------------------------------------------------------------- #
# DELETE: also how standing orders change (PRD §4.11).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_delete_removes_the_beat_and_cascades_its_briefs(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        keep_id = await _seed_beat(user_id=user_id, topic="keep me")
        drop_id = await _seed_beat(user_id=user_id, topic="drop me")
        brief_id = await _seed_published(
            drop_id, number=1, published_on=date(2026, 7, 20), url="https://x.test/a"
        )

        resp = await client.delete(f"/api/v1/beats/{drop_id}")
        assert resp.status_code == 204, resp.text

        gone = await client.get(f"/api/v1/beats/{drop_id}")
        assert gone.status_code == 404

    async with db.async_session() as session:
        assert await session.get(Beat, drop_id) is None
        assert await session.get(Brief, brief_id) is None
        assert await session.get(Beat, keep_id) is not None


# --------------------------------------------------------------------------- #
# 422 on a bad `anchor_weekday` or a bad `tz_offset_minutes`.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_invalid_anchor_weekday_is_422(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post(
            "/api/v1/beats",
            json={"topic": "x", "level": "new_to_it", "anchor_weekday": 7},
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert await _count_beats() == 0


@pytest.mark.anyio
async def test_invalid_tz_offset_minutes_is_422(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.get("/api/v1/beats", params={"tz_offset_minutes": 901})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- #
# 401: anonymous requests are rejected through the shared envelope.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_anonymous_requests_get_401(
    app: FastAPI, analyst_flag_enabled: None
) -> None:
    random_id = uuid.uuid4()
    async with _client(app) as client:
        assert (await client.get("/api/v1/beats")).status_code == 401
        assert (
            await client.post(
                "/api/v1/beats",
                json={"topic": "x", "level": "new_to_it", "anchor_weekday": 0},
            )
        ).status_code == 401
        assert (await client.get(f"/api/v1/beats/{random_id}")).status_code == 401
        assert (await client.delete(f"/api/v1/beats/{random_id}")).status_code == 401
        assert (
            await client.post(f"/api/v1/beats/{random_id}/retry")
        ).status_code == 401
        assert (await client.get(f"/api/v1/briefs/{random_id}")).status_code == 401
        assert (
            await client.post(
                f"/api/v1/briefs/{random_id}/read", json={"marker": "opened"}
            )
        ).status_code == 401

        body = (await client.get("/api/v1/beats")).json()
        assert body["error"]["code"] == "unauthenticated"


# --------------------------------------------------------------------------- #
# The admin model picker on ``POST /beats`` (TDD D7/§5.3), enforced through
# the SAME ``validate_model_override`` ``POST /paths`` uses.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_non_admin_model_override_is_403(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post(
            "/api/v1/beats",
            json={
                "topic": "x",
                "level": "new_to_it",
                "anchor_weekday": 0,
                "model_research": "anthropic/claude-haiku-4-5",
            },
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"
    assert await _count_beats() == 0


@pytest.mark.anyio
async def test_admin_off_allowlist_model_override_is_422(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)
        resp = await client.post(
            "/api/v1/beats",
            json={
                "topic": "x",
                "level": "new_to_it",
                "anchor_weekday": 0,
                "model_brief": "anthropic/claude-not-a-real-model",
            },
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"
    assert await _count_beats() == 0


@pytest.mark.anyio
async def test_admin_model_override_persists_on_the_beat_row(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    retriever = _FakeRetriever(documents=[])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    _wire_pipeline(monkeypatch, retriever=retriever, responder=responder)

    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)
        resp = await client.post(
            "/api/v1/beats",
            json={
                "topic": "x",
                "level": "new_to_it",
                "anchor_weekday": 0,
                "model_research": "anthropic/claude-opus-4-8",
                "model_brief": "anthropic/claude-haiku-4-5",
            },
        )
        assert resp.status_code == 202, resp.text
        beat_id = resp.json()["id"]
        await spawn.drain()

    beat = await _reload_beat(beat_id)
    assert beat.model_research == "anthropic/claude-opus-4-8"
    assert beat.model_brief == "anthropic/claude-haiku-4-5"


# --------------------------------------------------------------------------- #
# POST /beats/{id}/retry: the only re-claim of a ``failed`` run.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_retry_reclaims_a_failed_beat(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        analyst=(
            "cited_urls",
            {
                "title": "Recovered",
                "body_markdown": "Body.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    _wire_pipeline(monkeypatch, retriever=retriever, responder=responder)

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        # Force the Beat into a real, terminal ``failed`` state — an ordinary
        # arrival would never re-claim it (D3); only this explicit retry may.
        async with db.async_session() as session:
            await session.execute(
                update(Beat)
                .where(Beat.id == beat_id)
                .values(
                    research_state=BeatResearchState.FAILED,
                    research_started_at=datetime(2026, 7, 1, tzinfo=UTC),
                    research_error="Couldn't reach sources. Please retry.",
                )
            )
            await session.commit()

        resp = await client.post(f"/api/v1/beats/{beat_id}/retry")
        assert resp.status_code == 202, resp.text
        # The claim is awaited before the response is built (code-review
        # FIX 9), so the 202 body already reflects it — "researching", never
        # the stale, pre-claim "failed" this Beat started this test at. Only
        # the pipeline itself (retrieval + the two model calls) is left
        # running for `spawn.drain()` below to finish.
        assert resp.json()["research_state"] == "researching"

        await spawn.drain()

    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE
    async with db.async_session() as session:
        briefs = await BriefRepository(session).list_for_beat(beat_id)
    assert len(briefs) == 1
    assert briefs[0].kind is BriefKind.PUBLISHED


async def _count_research_runs(beat_id: uuid.UUID) -> int:
    async with db.async_session() as session:
        rows = (
            await session.execute(
                select(BeatResearchRun).where(BeatResearchRun.beat_id == beat_id)
            )
        ).scalars()
        return len(list(rows))


@pytest.mark.anyio
async def test_retry_on_an_idle_beat_is_a_noop(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    """FIX 2(b): ``idle`` is a Beat's HEALTHY STEADY STATE (unlike a path's
    ``pending``, a pre-run state), so retry must NOT re-claim it — a stray
    retry on an untouched Beat would win the claim, drive a full billed
    pipeline, and publish an off-cadence Brief that resets D4's cadence floor
    early. A genuine no-op, made real (FIX 4): no task spawned, no
    ``BeatResearchRun`` row (no claim was won), and no Brief written — not
    merely "still idle", which would pass identically whether nothing
    happened or a full run completed (``idle`` is also the post-success
    state).
    """
    retriever = _FakeRetriever(documents=[_doc("https://example.com/a")])
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        analyst=(
            "cited_urls",
            {
                "title": "Should never be written",
                "body_markdown": "Body.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    _wire_pipeline(monkeypatch, retriever=retriever, responder=responder)

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)

        resp = await client.post(f"/api/v1/beats/{beat_id}/retry")
        assert resp.status_code == 202, resp.text
        assert resp.json()["research_state"] == "idle"

    # No claim, no spawn, no billing: the Beat is untouched down to its
    # research_started_at, no research-run row was inserted (the retry cap
    # is checked ONLY once a real failed Beat is confirmed — FIX 2a — so a
    # no-op retry spends no quota either), and no Brief exists.
    assert spawn.tasks == []
    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.IDLE
    assert beat.research_started_at is None
    assert await _count_research_runs(beat_id) == 0
    async with db.async_session() as session:
        briefs = await BriefRepository(session).list_for_beat(beat_id)
    assert briefs == []


@pytest.mark.anyio
async def test_retry_on_a_refused_beat_is_a_noop(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    """``refused`` is terminal (PRD §2's safety branch, D3) — retry must not
    re-claim it either. Only a genuine ``failed`` run is retry-claimable."""
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        async with db.async_session() as session:
            await session.execute(
                update(Beat)
                .where(Beat.id == beat_id)
                .values(
                    research_state=BeatResearchState.REFUSED,
                    refusal_message="Out of scope for this analyst.",
                )
            )
            await session.commit()

        resp = await client.post(f"/api/v1/beats/{beat_id}/retry")
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["research_state"] == "refused"
        assert body["refusal_message"] == "Out of scope for this analyst."

    assert spawn.tasks == []
    beat = await _reload_beat(beat_id)
    assert beat.research_state is BeatResearchState.REFUSED
    assert await _count_research_runs(beat_id) == 0


@pytest.mark.anyio
async def test_retry_on_a_failed_beat_is_429_at_the_daily_research_cap(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
) -> None:
    """FIX 2(a): unlike the arrival drain's own non-raising check, the
    explicit retry route's cap check RAISES — the `POST /paths/{id}/retry`
    precedent (`check_outline_generation`), because an explicit `POST` is
    the billed trigger the drain's own "never at the route" reasoning does
    not cover. Checked only after confirming the Beat is genuinely `failed`
    (FIX 2b), so the no-op path never spends or blocks on this cap."""
    monkeypatch.setattr(settings, "rate_limit_brief_research_per_day", 1)
    retriever = _FakeRetriever(documents=[])
    responder = _PipelineResponder(researcher=("findings", {"findings": []}))
    _wire_pipeline(monkeypatch, retriever=retriever, responder=responder)

    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch, OWNER)

        # Spend the one unit of daily research capacity via a real deploy.
        first = await client.post(
            "/api/v1/beats",
            json={"topic": "spends the cap", "level": "new_to_it", "anchor_weekday": 0},
        )
        assert first.status_code == 202
        await spawn.drain()

        # A second, genuinely FAILED Beat: the retry route confirms it is
        # eligible for real work, THEN hits the (now-exhausted) cap.
        second_beat_id = await _seed_beat(user_id=user_id, topic="failed one")
        async with db.async_session() as session:
            await session.execute(
                update(Beat)
                .where(Beat.id == second_beat_id)
                .values(
                    research_state=BeatResearchState.FAILED,
                    research_started_at=datetime(2026, 7, 1, tzinfo=UTC),
                    research_error="Couldn't reach sources. Please retry.",
                )
            )
            await session.commit()

        resp = await client.post(f"/api/v1/beats/{second_beat_id}/retry")

    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["request_id"] == resp.headers["X-Request-ID"]

    # The 429 happened before any claim: the Beat is still failed, untouched.
    beat = await _reload_beat(second_beat_id)
    assert beat.research_state is BeatResearchState.FAILED
    # Zero runs on THIS Beat: the cap rejected the retry before it could claim.
    # (The one run the deploy above spent belongs to the first Beat, not this one.)
    assert await _count_research_runs(second_beat_id) == 0
