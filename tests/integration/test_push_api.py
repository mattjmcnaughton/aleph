"""Push ownership and persistence over the public HTTP interface."""

import asyncio
import datetime
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from aleph import db
from aleph.app import create_app
from aleph.config import settings
from aleph.dependencies import get_current_user
from aleph.models import Device, NotificationPreferences, ReminderSend, User
from aleph.services.notifications import notification_service

from .conftest import connect, run_alembic

PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcx"
    "aOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
BODY = {
    "endpoint": "https://push.apple.com/test",
    "keys": {"p256dh": PUBLIC, "auth": AUTH},
}


async def make_user():
    async with db.new_session() as session:
        name = uuid.uuid4().hex
        user = User(username=name, display_name=name, issuer="test", subject=name)
        session.add(user)
        await session.commit()
        return user


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", [False, True])
async def test_registration_capabilities_never_reach_exported_spans(
    capfire, monkeypatch, invalid
):
    import aleph.telemetry as telemetry

    monkeypatch.setattr(telemetry, "_logfire_configured", True)
    owner = await make_user()
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: owner
    body = {
        "endpoint": "https://push.apple.com/ingress-capability-marker",
        "keys": {"p256dh": PUBLIC, "auth": AUTH},
    }
    if invalid:
        body["keys"] = {"p256dh": PUBLIC + "invalid-marker", "auth": AUTH}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/devices", json=body)
    assert response.status_code == (422 if invalid else 200)
    spans = capfire.exporter.exported_spans_as_dict()
    assert any(span["name"] == "POST /api/v1/devices" for span in spans)
    for span in spans:
        exported = repr(span)
        for marker in (body["endpoint"], PUBLIC, AUTH, "invalid-marker"):
            assert marker not in exported


@pytest.mark.anyio
async def test_devices_and_preferences():
    owner, other = await make_user(), await make_user()
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: owner
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/push-config")).status_code == 404
        responses = await asyncio.gather(
            *(client.post("/api/v1/devices", json=BODY) for _ in range(4))
        )
        assert all(r.status_code == 200 for r in responses)
        device_id = responses[0].json()["id"]
        assert all(r.json()["id"] == device_id for r in responses)
        async with db.new_session() as session:
            assert await session.scalar(select(func.count()).select_from(Device)) == 1
        defaults = {
            "reminder_enabled": False,
            "reminder_hour": 20,
            "path_ready_enabled": True,
            "brief_published_enabled": True,
        }
        assert (await client.get("/api/v1/notification-preferences")).json() == defaults
        changed = {
            **defaults,
            "reminder_enabled": True,
            "reminder_hour": 7,
            "path_ready_enabled": False,
        }
        assert (
            await client.put("/api/v1/notification-preferences", json=changed)
        ).json() == changed
        assert (await client.get("/api/v1/notification-preferences")).json() == changed
        for hour in (-1, 24):
            assert (
                await client.put(
                    "/api/v1/notification-preferences",
                    json={**changed, "reminder_hour": hour},
                )
            ).status_code == 422
        bad = await client.post(
            "/api/v1/devices", json={**BODY, "endpoint": "https://evilpush.apple.com/x"}
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "push_endpoint_not_allowed"
        for keys in (
            {"p256dh": "BA" * 43, "auth": AUTH},
            {"p256dh": PUBLIC, "auth": "AA"},
            {"p256dh": "+/", "auth": AUTH},
        ):
            assert (
                await client.post("/api/v1/devices", json={**BODY, "keys": keys})
            ).status_code == 422
        app.dependency_overrides[get_current_user] = lambda: other
        assert (await client.delete(f"/api/v1/devices/{device_id}")).status_code == 404
        moved = await client.post("/api/v1/devices", json=BODY)
        assert moved.json()["id"] == device_id
        app.dependency_overrides[get_current_user] = lambda: owner
        assert (await client.delete(f"/api/v1/devices/{device_id}")).status_code == 404
        app.dependency_overrides[get_current_user] = lambda: other
        assert (await client.delete(f"/api/v1/devices/{device_id}")).status_code == 204
        assert (await client.delete(f"/api/v1/devices/{device_id}")).status_code == 204


@pytest.mark.anyio
async def test_all_push_routes_require_auth():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, path in [
            ("GET", "/push-config"),
            ("POST", "/devices"),
            ("DELETE", f"/devices/{uuid.uuid4()}"),
            ("GET", "/notification-preferences"),
            ("PUT", "/notification-preferences"),
        ]:
            assert (await client.request(method, "/api/v1" + path)).status_code == 401


@pytest.mark.anyio
async def test_schema_defaults_constraints_and_account_cascade():
    owner = await make_user()
    async with db.new_session() as session:
        await session.execute(
            text("INSERT INTO notification_preferences (user_id) VALUES (:id)"),
            {"id": owner.id},
        )
        await session.commit()
        prefs = await session.get(NotificationPreferences, owner.id)
        assert prefs is not None
        assert (
            prefs.reminder_enabled,
            prefs.reminder_hour,
            prefs.path_ready_enabled,
            prefs.brief_published_enabled,
        ) == (False, 20, True, True)
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "UPDATE notification_preferences SET reminder_hour=24 "
                    "WHERE user_id=:id"
                ),
                {"id": owner.id},
            )
        await session.rollback()
        session.add(
            ReminderSend(
                user_id=owner.id,
                local_date=datetime.date(2026, 10, 20),
                outcome="disabled",
            )
        )
        await session.commit()
        with pytest.raises(IntegrityError):
            session.add(
                ReminderSend(
                    user_id=owner.id,
                    local_date=datetime.date(2026, 10, 20),
                    outcome="error",
                )
            )
            await session.commit()
        await session.rollback()
        app = create_app()
        app.dependency_overrides[get_current_user] = lambda: owner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.post("/api/v1/devices", json=BODY)).status_code == 200
        await session.execute(delete(User).where(User.id == owner.id))
        await session.commit()
        for model in (Device, NotificationPreferences, ReminderSend):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


def test_push_migration_reverses_and_reapplies(isolated_database):
    run_alembic(isolated_database, "0017_user_time_zone", downgrade=True)

    async def tables():
        connection = await connect(isolated_database)
        try:
            return {
                row[0]
                for row in await connection.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )
            }
        finally:
            await connection.close()

    assert not {"devices", "notification_preferences", "reminder_sends"} & asyncio.run(
        tables()
    )
    run_alembic(isolated_database, "head")
    assert {"devices", "notification_preferences", "reminder_sends"} <= asyncio.run(
        tables()
    )


@pytest.mark.anyio
async def test_e2e_recording_factory_is_network_free_and_exposes_public_config(
    monkeypatch,
):
    from aleph.services.briefing import briefing_service
    from aleph.services.generation import generation_orchestrator
    from scripts.e2e_backend import create_stub_app

    from .conftest import CollectingSpawn

    snapshot = settings.model_dump()
    old_sender = notification_service.sender
    old_retriever = briefing_service._retriever  # noqa: SLF001
    spawn = CollectingSpawn()
    monkeypatch.setattr(generation_orchestrator, "_spawn", spawn)
    try:
        app = create_stub_app()
        owner = await make_user()
        app.dependency_overrides[get_current_user] = lambda: owner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/push-config")).status_code == 200
            assert (await client.get("/__e2e__/pushes")).json() == []
            assert (await client.post("/api/v1/devices", json=BODY)).status_code == 200
            path = await client.post(
                "/api/v1/paths", json={"topic": "Algebra", "level": "new_to_it"}
            )
            assert path.status_code == 202
            await spawn.drain()
            pushes = (await client.get("/__e2e__/pushes")).json()
            assert len(pushes) == 1
            assert pushes[0]["account_id"] == str(owner.id)
            assert (
                pushes[0]["payload"]["notification"]["body"]
                == "Your path on Algebra is ready."
            )
    finally:
        await spawn.cancel_pending()
        for name, value in snapshot.items():
            setattr(settings, name, value)
        notification_service.sender = old_sender
        briefing_service._retriever = old_retriever  # noqa: SLF001
