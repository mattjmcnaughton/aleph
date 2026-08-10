"""Product events are emitted by the real API routes (AL-070, PRD §5.7 / TDD §9).

Drives the genuine HTTP surface (real Postgres, stub model at the resolution seam,
real cookie auth) and captures what lands in Logfire via ``capfire`` — the same
capture the AL-005 pipeline test uses. Each user action must emit its product
event carrying every field the §7 metric queries need; here we assert, per event,
that the captured record carries at least its ``EVENT_FIELDS`` manifest set. That
closes the loop the unit tests open: ``test_events`` pins the manifest to the
emitters, ``test_metrics_queries`` pins the queries to the manifest, and this
proves the emitters actually fire (with those fields) when a learner acts.

``capfire`` (logfire's pytest11 plugin) gives an in-memory span exporter with
``send_to_logfire=False``; ``configure_logging`` installs the StructlogProcessor
so structlog events reach it as log records (``span_type == "log"``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.function import FunctionModel

from aleph import events
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.logging import configure_logging
from aleph.routers.v1 import beats as beats_router_module
from aleph.services import briefing as briefing_module
from aleph.services import generation as gen_module

from .conftest import CollectingSpawn, assert_event, captured_records, stub_resolver
from .test_beats_api import _seed_beat, _seed_published
from .test_beats_api import _sign_in as _sign_in_and_get_user_id
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
    subject="owner-subject",
    username="owner",
    display_name="Owner User",
    email="owner@example.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    collector = CollectingSpawn()
    monkeypatch.setattr(
        gen_module.generation_orchestrator, "_resolve_model", stub_resolver()
    )
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)
    return collector


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, identity: AuthIdentity
) -> None:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: identity)
    resp = await client.get("/auth/callback", follow_redirects=False)
    assert resp.status_code == 303


def _flat_lessons(path_body: dict) -> list[dict]:
    lessons = [lesson for unit in path_body["units"] for lesson in unit["lessons"]]
    return sorted(lessons, key=lambda lesson: lesson["position_in_path"])


async def _drive_full_journey(
    client: AsyncClient, spawn: CollectingSpawn, topic: str
) -> str:
    """W1→W3→W5 as HTTP: create → view/attempt/complete every lesson → delete."""
    resp = await client.post(
        "/api/v1/paths", json={"topic": topic, "level": "some_experience"}
    )
    assert resp.status_code == 202, resp.text
    path_id = resp.json()["id"]
    await spawn.drain()

    # Walk the path to completion, one available lesson at a time.
    for _ in range(200):  # generous bound; real paths are ~6-15 lessons
        detail = await client.get(f"/api/v1/paths/{path_id}")
        await spawn.drain()
        body = detail.json()
        lessons = _flat_lessons(body)
        target = next(
            (lesson for lesson in lessons if lesson["unlock_state"] == "available"),
            None,
        )
        if target is None:
            break  # nothing available → path complete

        # View it (emits lesson_viewed; also advances prefetch).
        view = await client.get(f"/api/v1/lessons/{target['id']}")
        await spawn.drain()
        if view.json()["generation_state"] != "generated":
            # Ensure generation, then poll until generated.
            await client.post(f"/api/v1/lessons/{target['id']}/generate")
            await spawn.drain()
            await client.get(f"/api/v1/lessons/{target['id']}")
            await spawn.drain()

        await client.post(
            f"/api/v1/lessons/{target['id']}/attempt", json={"selected_index": 0}
        )
        await client.post(f"/api/v1/lessons/{target['id']}/complete")
        await spawn.drain()

    # Delete the (now complete) path.
    deleted = await client.delete(f"/api/v1/paths/{path_id}")
    assert deleted.status_code == 204
    return path_id


@pytest.mark.anyio
@pytest.mark.workflow("W1")
async def test_full_journey_emits_every_product_event(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch, capfire
) -> None:
    configure_logging()
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path_id = await _drive_full_journey(client, spawn, "Rust ownership")

    # Account + path lifecycle.
    account = assert_event(capfire, events.ACCOUNT_CREATED)
    assert account["account_id"]
    created = assert_event(capfire, events.PATH_CREATED)
    assert created["path_id"] == path_id
    assert created["path_level"] == "some_experience"

    # Generation (fenced success), with the cost/latency fields.
    outline = assert_event(capfire, events.OUTLINE_GENERATED)
    assert outline["outcome"] == "ready"
    assert outline["success"] is True
    assert outline["path_id"] == path_id
    # Token usage is carried through, not silently zeroed (guards the
    # ``usage_tokens`` best-effort path from deflating the cost-per-path metric).
    assert outline["total_tokens"] > 0
    lesson_gen = assert_event(capfire, events.LESSON_GENERATED)
    assert lesson_gen["outcome"] == "generated"
    assert lesson_gen["success"] is True
    assert lesson_gen["total_tokens"] > 0

    # Learner progression.
    viewed = assert_event(capfire, events.LESSON_VIEWED)
    assert viewed["path_id"] == path_id
    attempted = assert_event(capfire, events.QUICK_CHECK_ATTEMPTED)
    assert attempted["outcome"] in {"correct", "incorrect"}
    assert attempted["is_correct"] in {True, False}
    assert_event(capfire, events.LESSON_COMPLETED)

    # Path completion (derived) + deletion.
    completed = assert_event(capfire, events.PATH_COMPLETED)
    assert completed["path_id"] == path_id
    assert completed["lesson_count"] >= 1
    deleted = assert_event(capfire, events.PATH_DELETED)
    assert deleted["path_id"] == path_id


async def _first_attemptable_lesson(
    client: AsyncClient, spawn: CollectingSpawn, topic: str
) -> str:
    """Create a path and return one generated, attemptable lesson id."""
    resp = await client.post(
        "/api/v1/paths", json={"topic": topic, "level": "some_experience"}
    )
    assert resp.status_code == 202, resp.text
    path_id = resp.json()["id"]
    await spawn.drain()

    detail = await client.get(f"/api/v1/paths/{path_id}")
    await spawn.drain()
    target = next(
        lesson
        for lesson in _flat_lessons(detail.json())
        if lesson["unlock_state"] == "available"
    )
    view = await client.get(f"/api/v1/lessons/{target['id']}")
    await spawn.drain()
    if view.json()["generation_state"] != "generated":
        await client.post(f"/api/v1/lessons/{target['id']}/generate")
        await spawn.drain()
        await client.get(f"/api/v1/lessons/{target['id']}")
        await spawn.drain()
    return target["id"]


@pytest.mark.anyio
@pytest.mark.workflow("W6")
async def test_quick_check_attempted_emitted_once_per_lesson(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch, capfire
) -> None:
    """A repeat submit is not a new Attempt (CONTEXT.md / AL-012): only the
    first-wins Attempt emits ``quick_check_attempted``. A second submit must emit
    nothing, or it double-counts the correctness guardrail's denominator."""
    configure_logging()
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        lesson_id = await _first_attemptable_lesson(client, spawn, "Rust ownership")

        first = await client.post(
            f"/api/v1/lessons/{lesson_id}/attempt", json={"selected_index": 0}
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            f"/api/v1/lessons/{lesson_id}/attempt", json={"selected_index": 1}
        )
        assert second.status_code == 200, second.text

    attempts = captured_records(capfire, events.QUICK_CHECK_ATTEMPTED)
    assert len(attempts) == 1, (
        f"expected exactly one quick_check_attempted, got {len(attempts)}"
    )


@pytest.mark.anyio
async def test_account_created_only_on_first_sign_in(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, capfire
) -> None:
    configure_logging()
    async with _client(app) as first, _client(app) as second:
        await _sign_in(first, monkeypatch, OWNER)
        await _sign_in(second, monkeypatch, OWNER)  # same identity → returning
    # Provisioned once; the returning sign-in reuses the row and emits nothing.
    assert len(captured_records(capfire, events.ACCOUNT_CREATED)) == 1


@pytest.mark.anyio
@pytest.mark.workflow("W7")
async def test_outline_refused_and_failed_outcomes(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch, capfire
) -> None:
    configure_logging()
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        refused = await client.post(
            "/api/v1/paths",
            json={"topic": "[force-refusal] no", "level": "new_to_it"},
        )
        await spawn.drain()
        failed = await client.post(
            "/api/v1/paths",
            json={"topic": "[force-outline-failure] no", "level": "new_to_it"},
        )
        await spawn.drain()
        refused_id = refused.json()["id"]
        failed_id = failed.json()["id"]

    outcomes = {
        record["attributes"]["path_id"]: record["attributes"]["outcome"]
        for record in captured_records(capfire, events.OUTLINE_GENERATED)
    }
    assert outcomes[refused_id] == "refused"
    assert outcomes[failed_id] == "failed"
    # A failed outline never reports success (the failure-rate guardrail signal).
    failed_record = next(
        record["attributes"]
        for record in captured_records(capfire, events.OUTLINE_GENERATED)
        if record["attributes"]["path_id"] == failed_id
    )
    assert failed_record["success"] is False


# --------------------------------------------------------------------------- #
# Phase 6 — the analyst (AL-540): beat_deployed and brief_read, driven as
# real HTTP against the real Beats & Briefs API (routers/v1/beats.py).
# brief_research_completed's four outcomes are exercised at the service layer
# (tests/integration/test_briefing.py), which already carries the fakes this
# pipeline needs (a scripted FunctionModel serving both agent calls).
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W29")
async def test_beat_deployed_and_brief_read_emit_every_declared_field(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
    capfire,
) -> None:
    configure_logging()
    beats_spawn = CollectingSpawn()
    monkeypatch.setattr(briefing_module.briefing_service, "_spawn", beats_spawn)
    monkeypatch.setattr(
        briefing_module.briefing_service,
        "_retriever",
        _FakeRetriever(documents=[_doc("https://example.com/a")]),
    )
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
    monkeypatch.setattr(
        briefing_module.briefing_service,
        "_resolve_model",
        _resolver(FunctionModel(responder)),
    )

    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        deployed = await client.post(
            "/api/v1/beats",
            json={
                "topic": "EU AI regulation",
                "level": "some_experience",
                "anchor_weekday": 0,
                "guidance": "focus on enforcement",
            },
        )
        assert deployed.status_code == 202, deployed.text
        beat_id = deployed.json()["id"]
        await beats_spawn.drain()

        detail = await client.get(f"/api/v1/beats/{beat_id}")
        assert detail.status_code == 200, detail.text
        entry = detail.json()["entries"][0]
        brief_id = entry["id"]

        opened = await client.post(
            f"/api/v1/briefs/{brief_id}/read", json={"marker": "opened"}
        )
        assert opened.status_code == 204, opened.text
        sources = await client.post(
            f"/api/v1/briefs/{brief_id}/read", json={"marker": "sources"}
        )
        assert sources.status_code == 204, sources.text

    deployed_event = assert_event(capfire, events.BEAT_DEPLOYED)
    assert deployed_event["beat_id"] == beat_id
    assert deployed_event["beat_level"] == "some_experience"
    assert deployed_event["anchor_weekday"] == 0
    assert deployed_event["has_guidance"] is True

    research = assert_event(capfire, events.BRIEF_RESEARCH_COMPLETED)
    assert research["beat_id"] == beat_id
    assert research["outcome"] == "published"
    assert research["total_tokens"] > 0

    read_records = captured_records(capfire, events.BRIEF_READ)
    assert len(read_records) == 2
    markers = {r["attributes"]["marker"] for r in read_records}
    assert markers == {"opened", "sources"}
    for record in read_records:
        attributes = record["attributes"]
        missing = events.EVENT_FIELDS[events.BRIEF_READ] - set(attributes)
        assert not missing, f"brief_read missing fields {sorted(missing)}"
        assert attributes["beat_id"] == beat_id
        assert attributes["brief_id"] == brief_id
        assert attributes["age_days"] >= 0


@pytest.mark.anyio
async def test_read_ping_age_days_uses_the_learners_local_day_not_utc(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
    capfire,
) -> None:
    """FIX 9 (code-review, AL-540): `age_days` is computed against the
    learner's LOCAL day via the route's new `tz_offset_minutes` param, not
    always UTC's. Regression case: a learner nine hours EAST of UTC (Tokyo,
    `tz_offset_minutes = -540`, the JS `getTimezoneOffset()` sign convention
    `dtos/progress.py` documents) reading a Brief in their own morning, at a
    moment UTC still reads the PREVIOUS day.

    The existing ``age_days >= 0`` assertion just above this test
    (``test_beat_deployed_and_brief_read_emit_every_declared_field``) is
    never exercised under a non-UTC offset — every call there omits
    ``tz_offset_minutes`` and reads it same-day in UTC too, so it would stay
    green even if this route silently went back to UTC-only. This test pins
    the one case that distinguishes them: before FIX 9, this exact scenario
    produced ``age_days == -1``."""
    configure_logging()

    class _FrozenDatetime(datetime):
        """Freezes ``datetime.now(UTC)`` inside the beats router to
        2026-08-10T16:00Z — 2026-08-11T01:00 for a Tokyo-local learner
        (UTC+9), already the SAME calendar day as the Brief's own
        ``published_on`` (2026-08-11, the learner's local day at publish
        time, D4a) even though UTC itself still reads 2026-08-10."""

        @classmethod
        def now(cls, tz: object = None) -> _FrozenDatetime:
            del tz
            return cls(2026, 8, 10, 16, 0, tzinfo=UTC)

    monkeypatch.setattr(beats_router_module, "datetime", _FrozenDatetime)

    async with _client(app) as client:
        # This module's own `_sign_in` returns `None`; `test_beats_api`'s
        # version returns the signed-in `user_id`, which the direct
        # repository seeding below needs (there is no deploy response to
        # read it off).
        user_id = await _sign_in_and_get_user_id(client, monkeypatch, OWNER)
        beat_id = await _seed_beat(user_id=user_id)
        brief_id = await _seed_published(
            beat_id, number=1, published_on=date(2026, 8, 11), url="https://x.test/a"
        )

        resp = await client.post(
            f"/api/v1/briefs/{brief_id}/read",
            params={"tz_offset_minutes": -540},
            json={"marker": "opened"},
        )
        assert resp.status_code == 204, resp.text

    record = assert_event(capfire, events.BRIEF_READ)
    assert record["brief_id"] == str(brief_id)
    # Local day is 2026-08-11 (16:00 UTC + 9h = 01:00 local, already the
    # 11th) — the SAME day the Brief published, so age_days is honestly 0.
    # The old, UTC-only computation would have read UTC's date (still the
    # 10th) minus published_on (the 11th) = -1, a value with no honest
    # reading.
    assert record["age_days"] == 0


@pytest.mark.anyio
async def test_a_repeat_read_ping_does_not_re_emit_brief_read(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    analyst_flag_enabled: None,
    capfire,
) -> None:
    """First-write-wins (D11): a repeat ping for a marker already stamped is
    not a second read — the quick_check_attempted precedent."""
    configure_logging()
    beats_spawn = CollectingSpawn()
    monkeypatch.setattr(briefing_module.briefing_service, "_spawn", beats_spawn)
    monkeypatch.setattr(
        briefing_module.briefing_service,
        "_retriever",
        _FakeRetriever(documents=[_doc("https://example.com/a")]),
    )
    responder = _PipelineResponder(
        researcher=(
            "findings",
            {"findings": [_finding_payload("X happened", ["https://example.com/a"])]},
        ),
        analyst=(
            "cited_urls",
            {
                "title": "T",
                "body_markdown": "Body.",
                "cited_urls": ["https://example.com/a"],
            },
        ),
    )
    monkeypatch.setattr(
        briefing_module.briefing_service,
        "_resolve_model",
        _resolver(FunctionModel(responder)),
    )

    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        deployed = await client.post(
            "/api/v1/beats",
            json={
                "topic": "EU AI regulation",
                "level": "some_experience",
                "anchor_weekday": 0,
            },
        )
        beat_id = deployed.json()["id"]
        await beats_spawn.drain()
        detail = await client.get(f"/api/v1/beats/{beat_id}")
        brief_id = detail.json()["entries"][0]["id"]

        first = await client.post(
            f"/api/v1/briefs/{brief_id}/read", json={"marker": "opened"}
        )
        assert first.status_code == 204, first.text
        second = await client.post(
            f"/api/v1/briefs/{brief_id}/read", json={"marker": "opened"}
        )
        assert second.status_code == 204, second.text

    assert len(captured_records(capfire, events.BRIEF_READ)) == 1
