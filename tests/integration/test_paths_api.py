"""Contract tests for the Paths API (AL-050, TDD §6).

The learner-facing HTTP surface over the generation orchestrator, exercised
end-to-end against real Postgres with the deterministic stub model at the
model-resolution seam (fakes over mocks — the stub is the one fake, injected
exactly as production resolves it). Auth is the real cookie flow (a stubbed OIDC
code exchange, mirroring ``test_auth_api``) so ownership and the ``401`` gate are
genuine.

Generation is fire-and-forget: the module-level orchestrator's ``spawn`` seam is
swapped for a collecting one so each background trigger (outline, prefetch,
resume, retry) can be drained deterministically — the same mechanism
``test_generation`` uses, applied through the HTTP layer.

Workflow tags (``@pytest.mark.workflow(...)``, TDD §12) give one vocabulary from
PRD → test → trace. The ``workflow`` marker is not yet registered in
``pyproject.toml`` (AL-003, in flight); the tag emits a non-fatal
``PytestUnknownMarkWarning`` until then and is used deliberately.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.config import settings
from aleph.models import Lesson, Path, PathStatus, QuickCheck, Unit
from aleph.services import generation as gen_module

from .conftest import CollectingSpawn, recording_resolver, stub_resolver

if TYPE_CHECKING:
    from fastapi import FastAPI

# Two distinct learners for ownership assertions. Neither email is in an admin
# domain (``mattjmcnaughton.com``), so both are subject to the daily cap.
OWNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="owner-subject",
    username="owner",
    display_name="Owner User",
    email="owner@example.com",
)
OTHER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="other-subject",
    username="other",
    display_name="Other User",
    email="other@example.com",
)
# An admin: the email domain (``mattjmcnaughton.com``) is the default admin
# domain, so this learner is exempt from the daily caps (TDD §7/§10).
ADMIN = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="admin-subject",
    username="admin",
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
    """Point the module-level orchestrator at the stub model + a drainable spawn.

    The router imports ``generation_orchestrator`` directly (AL-041's singleton),
    so patching its seams in place is what the routes see. ``_resolve_model`` →
    the deterministic stub (topic sentinels force W7/W8); ``_spawn`` → a
    collector the test drains to run background generation deterministically.

    Patching the private ``_resolve_model``/``_spawn`` on the singleton is a
    conscious, accepted seam (thermo-3), not a smell: it mirrors exactly how
    AL-041's lifespan rebinds those same seams on this same instance at startup
    (``bind_runtime``). Widening the production ``bind_runtime`` API with a
    test-only resolver parameter was considered and rejected — it would leak a
    test concern into a lifespan contract. AL-051 reuses this fixture pattern.
    """
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


async def _create(
    client: AsyncClient,
    spawn: CollectingSpawn,
    topic: str,
    level: str = "some_experience",
) -> str:
    """POST a path, drain the outline+prefetch, return the path id string."""
    resp = await client.post("/api/v1/paths", json={"topic": topic, "level": level})
    assert resp.status_code == 202, resp.text
    path_id = resp.json()["id"]
    await spawn.drain()
    return path_id


async def _poll(client: AsyncClient, spawn: CollectingSpawn, path_id: str) -> dict:
    """GET the poll target, draining the resume it spawns."""
    resp = await client.get(f"/api/v1/paths/{path_id}")
    await spawn.drain()
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _path_row(path_id: str) -> Path:
    async with db.async_session() as session:
        row = await session.get(Path, uuid.UUID(path_id))
        assert row is not None
        return row


async def _count_paths() -> int:
    """Total ``paths`` rows in the (per-test isolated) database."""
    async with db.async_session() as session:
        return await session.scalar(select(func.count()).select_from(Path)) or 0


async def _count_tree(path_id: str) -> tuple[int, int, int]:
    """(units, lessons, quick_checks) still attached to ``path_id``."""
    pid = uuid.UUID(path_id)
    async with db.async_session() as session:
        units = await session.scalar(
            select(func.count()).select_from(Unit).where(Unit.path_id == pid)
        )
        lessons = await session.scalar(
            select(func.count()).select_from(Lesson).where(Lesson.path_id == pid)
        )
        quick_checks = await session.scalar(
            select(func.count())
            .select_from(QuickCheck)
            .join(Lesson)
            .where(Lesson.path_id == pid)
        )
        return units or 0, lessons or 0, quick_checks or 0


# --------------------------------------------------------------------------- #
# W1: create → poll → ready lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W1")
async def test_create_poll_ready_lifecycle(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        path_id = await _create(client, spawn, "Rust ownership", "some_experience")
        body = await _poll(client, spawn, path_id)

        assert body["id"] == path_id
        assert body["topic"] == "Rust ownership"
        assert body["level"] == "some_experience"
        assert body["status"] == "ready"
        assert body["refusal_message"] is None

        # Outline present: units with ordered lessons.
        assert body["units"], "outline units should be present once ready"
        positions = [
            lesson["position_in_path"]
            for unit in body["units"]
            for lesson in unit["lessons"]
        ]
        assert positions == sorted(positions)

        # Per-lesson generation state + derived unlock state (the two axes).
        first_lesson = body["units"][0]["lessons"][0]
        assert first_lesson["generation_state"] == "generated"
        assert first_lesson["unlock_state"] == "available"
        all_lessons = [lesson for unit in body["units"] for lesson in unit["lessons"]]
        # Only the first incomplete lesson is available; the rest are locked.
        assert (
            sum(1 for lesson in all_lessons if lesson["unlock_state"] == "available")
            == 1
        )
        assert any(lesson["unlock_state"] == "locked" for lesson in all_lessons)

        # Progress roll-up: prefetch generated the first window.
        assert body["progress"]["total_lessons"] == len(all_lessons)
        assert body["progress"]["generated_lessons"] >= 1
        assert body["progress"]["completed_lessons"] == 0


# --------------------------------------------------------------------------- #
# W7: refusal payload distinct from failure
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W7")
async def test_refusal_is_distinct_from_failure(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        # Refused: terminal, carries a graceful message, no outline.
        refused_id = await _create(
            client, spawn, "Please [force-refusal] teach this", "new_to_it"
        )
        refused = await _poll(client, spawn, refused_id)
        assert refused["status"] == "refused"
        assert refused["refusal_message"], "a refusal carries a graceful message"
        assert refused["units"] == []

        # Failed: retryable, and NO refusal message — the two never conflate.
        failed_id = await _create(
            client, spawn, "[force-outline-failure] this topic", "new_to_it"
        )
        failed = await _poll(client, spawn, failed_id)
        assert failed["status"] == "failed"
        assert failed["refusal_message"] is None
        assert failed["units"] == []


# --------------------------------------------------------------------------- #
# W8: retry re-claims a failed outline
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W8")
async def test_retry_reclaims_failed_outline(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        path_id = await _create(
            client, spawn, "[force-outline-failure] retry me", "new_to_it"
        )
        assert (await _poll(client, spawn, path_id))["status"] == "failed"

        before = await _path_row(path_id)
        assert before.status is PathStatus.FAILED
        before_started = before.generation_started_at

        # Retry triggers a re-claim (202, non-blocking); the client polls after.
        resp = await client.post(f"/api/v1/paths/{path_id}/retry")
        assert resp.status_code == 202
        assert resp.json()["id"] == path_id
        await spawn.drain()

        after = await _path_row(path_id)
        # The stub fails the same topic deterministically, so the path is failed
        # again — but a NEW claim stamp proves the retry re-claimed and re-ran the
        # outline (§5.5/W8: retry re-claims a real failure), not a silent no-op.
        assert after.status is PathStatus.FAILED
        assert before_started is not None
        assert after.generation_started_at is not None
        assert after.generation_started_at > before_started


@pytest.mark.anyio
async def test_retry_on_refused_is_a_noop(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A refusal is terminal (§5.5: no retry — the learner starts a new topic). A
    # stray retry must not re-claim it.
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        refused_id = await _create(
            client, spawn, "[force-refusal] not allowed", "new_to_it"
        )
        assert (await _poll(client, spawn, refused_id))["status"] == "refused"
        before = await _path_row(refused_id)

        resp = await client.post(f"/api/v1/paths/{refused_id}/retry")
        assert resp.status_code == 202
        await spawn.drain()

        after = await _path_row(refused_id)
        assert after.status is PathStatus.REFUSED
        assert after.generation_started_at == before.generation_started_at


# --------------------------------------------------------------------------- #
# W5: delete removes the tree; other paths untouched
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W5")
async def test_delete_removes_tree_and_leaves_other_paths(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        keep_id = await _create(client, spawn, "Keep this path", "some_experience")
        drop_id = await _create(client, spawn, "Drop this path", "some_experience")
        await _poll(client, spawn, keep_id)
        await _poll(client, spawn, drop_id)
        assert all(count > 0 for count in await _count_tree(drop_id))

        resp = await client.delete(f"/api/v1/paths/{drop_id}")
        assert resp.status_code == 204

        # Gone for reads, and the whole tree cascaded away.
        assert (await client.get(f"/api/v1/paths/{drop_id}")).status_code == 404
        assert await _count_tree(drop_id) == (0, 0, 0)

        # The other path is entirely untouched.
        kept = await _poll(client, spawn, keep_id)
        assert kept["status"] == "ready"
        assert all(count > 0 for count in await _count_tree(keep_id))

        # And the switcher now lists only the kept path.
        listing = (await client.get("/api/v1/paths")).json()
        assert [row["id"] for row in listing["paths"]] == [keep_id]


# --------------------------------------------------------------------------- #
# switcher list shape
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_list_paths_switcher_shape(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        first_id = await _create(client, spawn, "First topic", "new_to_it")
        second_id = await _create(client, spawn, "Second topic", "work_in_it")
        await _poll(client, spawn, first_id)
        await _poll(client, spawn, second_id)

        listing = (await client.get("/api/v1/paths")).json()
        rows = listing["paths"]
        # Newest first (created_at desc).
        assert [row["id"] for row in rows] == [second_id, first_id]
        row = rows[0]
        assert set(row) == {"id", "topic", "level", "status", "progress"}
        assert row["topic"] == "Second topic"
        assert row["level"] == "work_in_it"
        assert row["status"] == "ready"
        assert set(row["progress"]) == {
            "total_lessons",
            "generated_lessons",
            "completed_lessons",
        }
        assert row["progress"]["total_lessons"] >= 1


# --------------------------------------------------------------------------- #
# ownership: another learner's resources read as 404 (GET, retry, DELETE)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_non_owner_gets_404_everywhere(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as owner, _client(app) as other:
        await _sign_in(owner, monkeypatch, OWNER)
        path_id = await _create(owner, spawn, "Owner-only path", "some_experience")

        await _sign_in(other, monkeypatch, OTHER)
        assert (await other.get(f"/api/v1/paths/{path_id}")).status_code == 404
        assert (await other.post(f"/api/v1/paths/{path_id}/retry")).status_code == 404
        assert (await other.delete(f"/api/v1/paths/{path_id}")).status_code == 404
        # The other learner's switcher is empty (isolation).
        assert (await other.get("/api/v1/paths")).json()["paths"] == []

        # None of that touched the owner's path.
        owner_view = await _poll(owner, spawn, path_id)
        assert owner_view["status"] == "ready"


# --------------------------------------------------------------------------- #
# carried 429 contract test (from AL-042): the rate-limited envelope over HTTP
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_path_creation_rate_limited_returns_429_envelope(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_paths_per_day", 1)
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        first = await client.post(
            "/api/v1/paths", json={"topic": "one", "level": "new_to_it"}
        )
        assert first.status_code == 202
        await spawn.drain()

        second = await client.post(
            "/api/v1/paths", json={"topic": "two", "level": "new_to_it"}
        )
        assert second.status_code == 429
        body = second.json()
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["message"]
        assert body["error"]["request_id"] == second.headers["X-Request-ID"]

        # The cap is checked BEFORE the billed work: the denied create inserted
        # no row, so only the first path exists (G2 — the check fences the write).
        assert await _count_paths() == 1


# --------------------------------------------------------------------------- #
# AL-052: admin model-picker enforcement + routing (TDD §5.3/D14)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_non_admin_model_override_is_403(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The picker is admin-only (§5.3): a non-admin sending an override is a 403
    # through the shared ``forbidden`` envelope — the capability gate, not the
    # cosmetic hidden picker. The billed work never runs: no path row is created.
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post(
            "/api/v1/paths",
            json={
                "topic": "Rust ownership",
                "level": "new_to_it",
                "model_lesson": "anthropic/claude-haiku-4-5",
            },
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["error"]["code"] == "forbidden"
        assert body["error"]["message"]
        assert await _count_paths() == 0


@pytest.mark.anyio
async def test_admin_off_allowlist_model_override_is_422(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Allowlist enforcement is server-side (§5.3): even an admin cannot select a
    # model outside ``MODEL_ALLOWLIST`` — an off-allowlist id is a 422 through the
    # shared ``validation_error`` envelope, and no path is created.
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)
        resp = await client.post(
            "/api/v1/paths",
            json={
                "topic": "Rust ownership",
                "level": "new_to_it",
                "model_outline": "anthropic/claude-not-a-real-model",
            },
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["message"]
        assert await _count_paths() == 0


@pytest.mark.anyio
async def test_admin_model_override_routes_chosen_model(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real acceptance: an admin override actually ROUTES the chosen model.
    # A recording resolver captures every id the orchestrator resolves; the
    # outline override must drive the outline call and the lesson override the
    # lesson calls — and the default Sonnet is never resolved for this path. The
    # override reaches the background tasks only because it is persisted on the
    # path row (survives the trigger/poll/reconcile boundary, §5.4).
    resolver, calls = recording_resolver()
    collector = CollectingSpawn()
    monkeypatch.setattr(gen_module.generation_orchestrator, "_resolve_model", resolver)
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)

    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)
        resp = await client.post(
            "/api/v1/paths",
            json={
                "topic": "Rust ownership",
                "level": "some_experience",
                "model_outline": "anthropic/claude-opus-4-8",
                "model_lesson": "anthropic/claude-haiku-4-5",
            },
        )
        assert resp.status_code == 202, resp.text
        path_id = resp.json()["id"]
        await collector.drain()
        # Poll drives the idempotent resume + prefetch too (poll-as-trigger); the
        # override still routes because it lives on the persisted row, not the
        # request.
        await client.get(f"/api/v1/paths/{path_id}")
        await collector.drain()

    assert "anthropic/claude-opus-4-8" in calls, "outline override was not routed"
    assert "anthropic/claude-haiku-4-5" in calls, "lesson override was not routed"
    # The configured default was never resolved for this path — the override, not
    # config, chose the models.
    assert settings.model_outline not in calls  # the config default was never resolved


@pytest.mark.anyio
async def test_admin_override_persists_on_path_row(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Resume-correctness (§5.4): the override is stored on the path so a poller or
    # the reconciler re-drives generation with the admin's chosen models, not the
    # config default. Assert the persisted columns directly.
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)
        resp = await client.post(
            "/api/v1/paths",
            json={
                "topic": "Rust ownership",
                "level": "some_experience",
                "model_outline": "anthropic/claude-opus-4-8",
                "model_lesson": "anthropic/claude-haiku-4-5",
            },
        )
        assert resp.status_code == 202, resp.text
        path_id = resp.json()["id"]
        await spawn.drain()

    row = await _path_row(path_id)
    assert row.model_outline == "anthropic/claude-opus-4-8"
    assert row.model_lesson == "anthropic/claude-haiku-4-5"


@pytest.mark.anyio
async def test_no_override_leaves_path_on_config_defaults(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A create with no picker fields records NULL overrides; the orchestrator then
    # falls back to the configured slots (the non-admin/default path is unchanged).
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path_id = await _create(client, spawn, "Rust ownership", "some_experience")

    row = await _path_row(path_id)
    assert row.model_outline is None
    assert row.model_lesson is None


# --------------------------------------------------------------------------- #
# 401: anonymous requests are rejected through the shared envelope
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_anonymous_requests_get_401(app: FastAPI) -> None:
    random_id = uuid.uuid4()
    async with _client(app) as client:
        assert (await client.get("/api/v1/paths")).status_code == 401
        assert (
            await client.post(
                "/api/v1/paths", json={"topic": "x", "level": "new_to_it"}
            )
        ).status_code == 401
        assert (await client.get(f"/api/v1/paths/{random_id}")).status_code == 401
        assert (
            await client.post(f"/api/v1/paths/{random_id}/retry")
        ).status_code == 401
        assert (await client.delete(f"/api/v1/paths/{random_id}")).status_code == 401

        body = (await client.get("/api/v1/paths")).json()
        assert body["error"]["code"] == "unauthenticated"


# --------------------------------------------------------------------------- #
# request validation: an empty topic is a 422 through the shared envelope
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_empty_topic_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post(
            "/api/v1/paths", json={"topic": "   ", "level": "new_to_it"}
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.anyio
async def test_invalid_level_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A level outside the onboarding enum is a wire validation error (G4).
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post(
            "/api/v1/paths", json={"topic": "Rust", "level": "guru"}
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.anyio
async def test_overlong_topic_is_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The topic is bounded (max_length=500) so a pathological payload never
    # reaches the model or the DB Text column (G4).
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post(
            "/api/v1/paths", json={"topic": "x" * 501, "level": "new_to_it"}
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- #
# F1: retry is a billed trigger with its own daily cap (429 over HTTP)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.workflow("W8")
async def test_retry_rate_limited_returns_429_envelope(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Retry inserts no row, so its cap counts paths with an outline attempt today
    # (``count_path_outline_generations_since``, reusing the paths/day cap). One
    # failed path today = one outline attempt; with the cap at 1 the learner is
    # already at it, so the retry is denied with the shared ``rate_limited``
    # envelope — never a silent billed re-run.
    monkeypatch.setattr(settings, "rate_limit_paths_per_day", 1)
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        path_id = await _create(
            client, spawn, "[force-outline-failure] retry cap", "new_to_it"
        )
        assert (await _poll(client, spawn, path_id))["status"] == "failed"

        resp = await client.post(f"/api/v1/paths/{path_id}/retry")
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["message"]
        assert body["error"]["request_id"] == resp.headers["X-Request-ID"]


@pytest.mark.anyio
async def test_admin_is_exempt_from_path_cap_over_http(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Admin exemption is wired at the route (``is_admin(user, settings)``): with
    # the cap at 1, a non-admin's second create would 429, but an admin's does
    # not (G1).
    monkeypatch.setattr(settings, "rate_limit_paths_per_day", 1)
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)

        first = await client.post(
            "/api/v1/paths", json={"topic": "admin one", "level": "new_to_it"}
        )
        assert first.status_code == 202
        await spawn.drain()

        second = await client.post(
            "/api/v1/paths", json={"topic": "admin two", "level": "new_to_it"}
        )
        assert second.status_code == 202
        await spawn.drain()

        # Both landed — the admin was never capped.
        assert await _count_paths() == 2


# --------------------------------------------------------------------------- #
# poll-as-trigger: a lost outline self-heals over HTTP (§5.4)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_poll_as_trigger_self_heals_lost_outline(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)

        # Create with the outline spawn DROPPED (closing the coroutine so it never
        # runs) — as if the background task were lost to a crash/deploy.
        monkeypatch.setattr(
            gen_module.generation_orchestrator, "_spawn", lambda coro: coro.close()
        )
        resp = await client.post(
            "/api/v1/paths", json={"topic": "self heal", "level": "some_experience"}
        )
        assert resp.status_code == 202
        path_id = resp.json()["id"]

        # Restore the drainable collector; the outline never generated.
        monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", spawn)

        # G4: a poll before the resume drains observes a NON-terminal status and
        # an empty outline — no dead spinner, and no units yet.
        pre = (await client.get(f"/api/v1/paths/{path_id}")).json()
        assert pre["status"] in {"pending", "generating"}
        assert pre["units"] == []

        # The poll above spawned the idempotent resume; drain it.
        await spawn.drain()

        # A subsequent poll now observes the self-healed, ready path with its
        # outline — the waiting learner's poll WAS the retry loop (§5.4).
        healed = await _poll(client, spawn, path_id)
        assert healed["status"] == "ready"
        assert healed["units"]


@pytest.mark.anyio
async def test_non_admin_override_at_rate_cap_is_403_not_429(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enforcement precedes the rate limit: the capability gate answers first."""
    monkeypatch.setattr(settings, "rate_limit_paths_per_day", 1)
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        first = await client.post(
            "/api/v1/paths", json={"topic": "Topic one", "level": "new_to_it"}
        )
        assert first.status_code == 202
        await spawn.drain()
        resp = await client.post(
            "/api/v1/paths",
            json={
                "topic": "Topic two",
                "level": "new_to_it",
                "model_outline": "anthropic/claude-haiku-4-5",
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.anyio
async def test_outline_retry_reroutes_the_persisted_override(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry re-reads the row: the schema-change justification, pinned.

    The outline override must drive the RE-claimed outline run too — resume
    correctness is exactly why the override is persisted (§5.4/D6).
    """
    resolver, calls = recording_resolver()
    collector = CollectingSpawn()
    monkeypatch.setattr(gen_module.generation_orchestrator, "_resolve_model", resolver)
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)

    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)
        resp = await client.post(
            "/api/v1/paths",
            json={
                "topic": "[force-outline-failure] anything",
                "level": "new_to_it",
                "model_outline": "anthropic/claude-opus-4-8",
            },
        )
        assert resp.status_code == 202
        path_id = resp.json()["id"]
        await collector.drain()
        calls.clear()

        retry = await client.post(f"/api/v1/paths/{path_id}/retry")
        assert retry.status_code == 202
        await collector.drain()

    assert "anthropic/claude-opus-4-8" in calls, "retry did not re-route the override"
