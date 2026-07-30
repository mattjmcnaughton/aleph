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

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from aleph import events
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.logging import configure_logging
from aleph.services import generation as gen_module

from .conftest import CollectingSpawn, assert_event, captured_records, stub_resolver

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
