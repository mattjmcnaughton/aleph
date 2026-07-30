"""Admin feature-flag API + session delivery (real app, real Postgres).

AL-203 (epic #82, owner amendment 1). Phase 2 ships dark: the ``tutor`` flag
defaults **off** globally and **on for admins**, so every Phase 2 ticket can
merge and deploy with zero learner exposure while admins dogfood in production.
This module is the contract test for that story end to end:

* the admin-only override API (403 / 404 / upsert / idempotent delete),
* the resolved map delivered on ``GET /api/v1/auth/session`` (the only surface a
  regular learner ever sees — they never call the admin routes),
* the ``ON DELETE CASCADE`` that keeps ``user_feature_overrides`` orphan-free,
* the launch rehearsal: flipping ``FEATURE_FLAG_DEFAULTS`` reaches learners
  without a code deploy (AL-270), and a per-user override still wins over it.

Auth is the real cookie flow with a stubbed OIDC code exchange (mirroring
``test_auth_api`` / ``test_paths_api``), so the admin gate is genuine — admin
status is derived from the email domain (``authz.is_admin``), never stored.
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
from aleph.models import User, UserFeatureOverride

if TYPE_CHECKING:
    from fastapi import FastAPI

FLAGS_URL = "/api/v1/admin/feature-flags"
SESSION_URL = "/api/v1/auth/session"
TUTOR = "tutor"

LEARNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="flag-learner-subject",
    username="flag-learner",
    display_name="Flag Learner",
    email="learner@example.com",
)
# The email domain (``mattjmcnaughton.com``) is the default admin domain.
ADMIN = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="flag-admin-subject",
    username="flag-admin",
    display_name="Flag Admin",
    email="admin@mattjmcnaughton.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


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
    assert response.status_code == 303
    body = (await client.get(SESSION_URL)).json()
    return uuid.UUID(body["user"]["id"])


async def _flags_on_session(client: AsyncClient) -> dict[str, bool]:
    response = await client.get(SESSION_URL)
    assert response.status_code == 200, response.text
    return response.json()["user"]["feature_flags"]


async def _override_count() -> int:
    async with db.async_session() as session:
        return (
            await session.scalar(select(func.count()).select_from(UserFeatureOverride))
            or 0
        )


# --------------------------------------------------------------------------- #
# The admin gate
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_routes_require_an_admin(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        target = f"{FLAGS_URL}/{TUTOR}/users/{uuid.uuid4()}"
        # Signed out is 401 (the shared auth gate), not 403.
        assert (await client.get(FLAGS_URL)).status_code == 401

        await _sign_in(client, monkeypatch, LEARNER)
        assert (await client.get(FLAGS_URL)).status_code == 403
        assert (await client.put(target, json={"enabled": True})).status_code == 403
        assert (await client.delete(target)).status_code == 403


@pytest.mark.anyio
async def test_admin_lists_every_registered_flag(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)

        response = await client.get(FLAGS_URL)

        assert response.status_code == 200, response.text
        # ``enabled_default`` is the *global* default — the admin baseline is a
        # resolution-time concern, not a property of the flag.
        assert response.json() == {
            "flags": [{"key": TUTOR, "enabled_default": False, "override_count": 0}]
        }


@pytest.mark.anyio
async def test_unknown_flag_or_user_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        admin_id = await _sign_in(client, monkeypatch, ADMIN)

        unknown_flag = f"{FLAGS_URL}/not_a_flag/users/{admin_id}"
        assert (await client.put(unknown_flag, json={"enabled": True})).status_code == (
            404
        )
        assert (await client.delete(unknown_flag)).status_code == 404

        unknown_user = f"{FLAGS_URL}/{TUTOR}/users/{uuid.uuid4()}"
        assert (await client.put(unknown_user, json={"enabled": True})).status_code == (
            404
        )
        # Nothing was written for the missing user.
        assert await _override_count() == 0


# --------------------------------------------------------------------------- #
# Session delivery + the override round trip
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_tutor_ships_dark_but_resolves_on_for_admins(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Amendment 1: off for learners, on for admins, defaults untouched."""
    async with _client(app) as learner:
        await _sign_in(learner, monkeypatch, LEARNER)
        assert await _flags_on_session(learner) == {TUTOR: False}

    async with _client(app) as admin:
        await _sign_in(admin, monkeypatch, ADMIN)
        assert await _flags_on_session(admin) == {TUTOR: True}
        # The global default is still off — nothing was mutated to make the
        # admin's map true.
        listed = (await admin.get(FLAGS_URL)).json()["flags"]
        assert listed == [{"key": TUTOR, "enabled_default": False, "override_count": 0}]


@pytest.mark.anyio
async def test_override_flips_a_learner_on_and_clears_idempotently(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as learner, _client(app) as admin:
        learner_id = await _sign_in(learner, monkeypatch, LEARNER)
        admin_id = await _sign_in(admin, monkeypatch, ADMIN)
        target = f"{FLAGS_URL}/{TUTOR}/users/{learner_id}"

        assert await _flags_on_session(learner) == {TUTOR: False}

        response = await admin.put(target, json={"enabled": True})
        assert response.status_code == 200, response.text
        assert response.json() == {
            "flag_key": TUTOR,
            "user_id": str(learner_id),
            "enabled": True,
        }
        assert await _flags_on_session(learner) == {TUTOR: True}

        # The override is one row, and it targets exactly one learner: the
        # admin's own map is unchanged.
        assert await _override_count() == 1
        assert (await admin.get(FLAGS_URL)).json()["flags"] == [
            {"key": TUTOR, "enabled_default": False, "override_count": 1}
        ]

        # A repeat PUT updates in place rather than inserting a second row.
        assert (await admin.put(target, json={"enabled": False})).status_code == 200
        assert await _override_count() == 1
        assert await _flags_on_session(learner) == {TUTOR: False}

        # An override wins for admins too — including over the admin baseline.
        admin_target = f"{FLAGS_URL}/{TUTOR}/users/{admin_id}"
        assert (await admin.put(admin_target, json={"enabled": False})).status_code == (
            200
        )
        assert await _flags_on_session(admin) == {TUTOR: False}

        # DELETE is idempotent: clearing an absent override is still a 204.
        assert (await admin.delete(target)).status_code == 204
        assert (await admin.delete(target)).status_code == 204
        assert await _flags_on_session(learner) == {TUTOR: False}
        assert (await admin.delete(admin_target)).status_code == 204
        assert await _flags_on_session(admin) == {TUTOR: True}
        assert await _override_count() == 0


@pytest.mark.anyio
async def test_settings_default_flips_the_flag_without_a_deploy(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AL-270's launch move, rehearsed: env flip on; a per-user override still wins."""
    async with _client(app) as learner, _client(app) as admin:
        learner_id = await _sign_in(learner, monkeypatch, LEARNER)
        await _sign_in(admin, monkeypatch, ADMIN)
        monkeypatch.setattr(settings, "feature_flag_defaults", f"{TUTOR}:on")

        assert await _flags_on_session(learner) == {TUTOR: True}
        assert (await admin.get(FLAGS_URL)).json()["flags"] == [
            {"key": TUTOR, "enabled_default": True, "override_count": 0}
        ]

        target = f"{FLAGS_URL}/{TUTOR}/users/{learner_id}"
        assert (await admin.put(target, json={"enabled": False})).status_code == 200
        assert await _flags_on_session(learner) == {TUTOR: False}


@pytest.mark.anyio
async def test_tutor_flag_enabled_fixture_opens_the_surface_for_a_learner(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """The shared fixture later tutor tickets gate their coverage on.

    AL-221/AL-220/AL-240 drive the tutor surface as an ordinary learner; without
    this they would be testing the flag gate. Pinned here so a change to the
    resolution order cannot silently leave those suites testing a 404.
    """
    async with _client(app) as learner:
        await _sign_in(learner, monkeypatch, LEARNER)

        assert await _flags_on_session(learner) == {TUTOR: True}


@pytest.mark.anyio
async def test_stale_override_rows_are_ignored(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row for a flag no longer in code never leaks into a resolved map."""
    async with _client(app) as learner:
        learner_id = await _sign_in(learner, monkeypatch, LEARNER)
        async with db.async_session() as session:
            session.add(
                UserFeatureOverride(
                    user_id=learner_id, flag_key="removed_flag", enabled=True
                )
            )
            await session.commit()

        assert await _flags_on_session(learner) == {TUTOR: False}


@pytest.mark.anyio
async def test_deleting_a_user_cascades_their_overrides(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as learner, _client(app) as admin:
        learner_id = await _sign_in(learner, monkeypatch, LEARNER)
        await _sign_in(admin, monkeypatch, ADMIN)
        target = f"{FLAGS_URL}/{TUTOR}/users/{learner_id}"
        assert (await admin.put(target, json={"enabled": True})).status_code == 200
        assert await _override_count() == 1

        async with db.async_session() as session:
            user = await session.get(User, learner_id)
            assert user is not None
            await session.delete(user)
            await session.commit()

        assert await _override_count() == 0
