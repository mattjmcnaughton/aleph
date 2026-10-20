"""Observe zones through authenticated requests against real PostgreSQL."""

import asyncio
import json
from base64 import b64encode
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import literal_column, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aleph import db
from aleph.app import create_app
from aleph.bearer import get_bearer_authenticator
from aleph.config import settings
from aleph.models import User
from tests._bearer_fakes import fake_authenticator, mint


@pytest.mark.anyio
@pytest.mark.parametrize("credential", ["bearer", "cookie"])
async def test_established_account_observation_with_one_connection(
    isolated_database: str, monkeypatch: pytest.MonkeyPatch, credential: str
) -> None:
    engine = create_async_engine(
        isolated_database, pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    monkeypatch.setattr(
        db, "async_session", async_sessionmaker(engine, expire_on_commit=False)
    )
    app = create_app()
    authenticator = fake_authenticator()
    app.dependency_overrides[get_bearer_authenticator] = lambda: authenticator
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            # Provision first without a hint: resolve must take the established,
            # unchanged account SELECT path on the observed request.
            response = await client.get(
                "/api/v1/auth/session",
                headers={"Authorization": f"Bearer {mint()}"},
            )
            assert response.status_code == 200
            headers = {"X-Aleph-Time-Zone": "Europe/London"}
            if credential == "bearer":
                headers["Authorization"] = f"Bearer {mint()}"
            else:
                data = b64encode(
                    json.dumps({"user_id": response.json()["user"]["id"]}).encode()
                )
                cookie = TimestampSigner(settings.session_secret_key).sign(data)
                client.cookies.set("session", cookie.decode())
            response = await client.get("/api/v1/auth/session", headers=headers)
            assert response.status_code == 200, response.text
            async with db.new_session() as session:
                assert await session.scalar(select(User.time_zone)) == "Europe/London"
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_request_zone_change_only_and_concurrent() -> None:
    app = create_app()
    authenticator = fake_authenticator()
    app.dependency_overrides[get_bearer_authenticator] = lambda: authenticator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {mint()}"},
    ) as client:

        async def request(zone: str | None) -> None:
            headers = {} if zone is None else {"X-Aleph-Time-Zone": zone}
            response = await client.get("/api/v1/auth/session", headers=headers)
            assert response.status_code == 200, response.text

        async def stored() -> tuple[str | None, datetime | None, str]:
            async with db.new_session() as session:
                row = (
                    await session.execute(
                        select(
                            User.time_zone,
                            User.time_zone_seen_at,
                            literal_column("xmin::text"),
                        )
                    )
                ).one()
                return row[0], row[1], row[2]

        await request("Europe/London")
        first = await stored()
        assert first[0] == "Europe/London"
        assert first[1] is not None
        for zone in [
            None,
            "Europe/London",
            "Mars/Olympus",
            "localtime",
            "posixrules",
            "x" * 65,
            "",
        ]:
            await request(zone)
            assert await stored() == first
        await asyncio.gather(*(request("America/New_York") for _ in range(4)))
        changed = await stored()
        assert changed[0] == "America/New_York"
        assert changed[1] is not None
        assert changed[1] > first[1]
        await request("America/New_York")
        assert await stored() == changed
        # Even a failed downstream read closes/rolls back its request session;
        # the zone has already committed in its independent transaction.
        response = await client.get(
            "/api/v1/paths/11111111-1111-4111-8111-111111111111",
            headers={"X-Aleph-Time-Zone": "Asia/Tokyo"},
        )
        assert response.status_code == 404
        assert (await stored())[0] == "Asia/Tokyo"


@pytest.mark.anyio
async def test_observation_headers_do_not_authorize() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/paths",
            headers={"X-Aleph-Time-Zone": "UTC", "X-Aleph-Display-Mode": "standalone"},
        )
        assert response.status_code == 401
