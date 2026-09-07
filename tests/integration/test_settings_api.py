"""Learner Settings API + session delivery (real app, real Postgres).

CONTEXT.md: Settings / Auto-draft. The contract end to end:

* ``GET /settings`` answers the code defaults for a learner who has never
  changed anything (no ``user_settings`` row exists yet),
* ``PATCH /settings`` creates that row on first change and updates it in
  place after (one row per learner, never two), touching only the settings
  the body names,
* the resolved settings ride ``GET /api/v1/auth/session`` as ``user.settings``
  (the feature-flag delivery precedent), so the lesson view can honour
  Auto-draft with no second request,
* a body naming a setting that does not exist is ``422``, not a silent ``200``,
* both routes are session-cookie protected (``401`` anonymous), and the
  ``ON DELETE CASCADE`` keeps the table orphan-free.

Auth is the real cookie flow with a stubbed OIDC code exchange (mirroring
``test_feature_flags_api``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.models import User, UserSettings

if TYPE_CHECKING:
    from fastapi import FastAPI

SETTINGS_URL = "/api/v1/settings"
SESSION_URL = "/api/v1/auth/session"

LEARNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="settings-learner-subject",
    username="settings-learner",
    display_name="Settings Learner",
    email="learner@example.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _sign_in(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    """Complete the stubbed OIDC callback; returns the local account id."""
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: LEARNER)
    response = await client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 303
    body = (await client.get(SESSION_URL)).json()
    return body["user"]["id"]


async def _row_count() -> int:
    async with db.async_session() as session:
        return (
            await session.scalar(select(func.count()).select_from(UserSettings))
        ) or 0


@pytest.mark.anyio
async def test_settings_routes_require_a_session(app: FastAPI) -> None:
    async with _client(app) as client:
        assert (await client.get(SETTINGS_URL)).status_code == 401
        response = await client.patch(
            SETTINGS_URL, json={"auto_draft_flashcards": False}
        )
        assert response.status_code == 401


@pytest.mark.anyio
async def test_a_new_learner_reads_the_defaults_with_no_row(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch)

        response = await client.get(SETTINGS_URL)

        assert response.status_code == 200
        assert response.json() == {"auto_draft_flashcards": True}
        # The session probe says the same thing, from the same defaults.
        session_body = (await client.get(SESSION_URL)).json()
        assert session_body["user"]["settings"] == {"auto_draft_flashcards": True}
    assert await _row_count() == 0


@pytest.mark.anyio
async def test_patch_creates_the_row_and_reaches_every_read(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch)

        response = await client.patch(
            SETTINGS_URL, json={"auto_draft_flashcards": False}
        )

        assert response.status_code == 200
        assert response.json() == {"auto_draft_flashcards": False}
        assert (await client.get(SETTINGS_URL)).json() == {
            "auto_draft_flashcards": False
        }
        session_body = (await client.get(SESSION_URL)).json()
        assert session_body["user"]["settings"] == {"auto_draft_flashcards": False}
    assert await _row_count() == 1


@pytest.mark.anyio
async def test_a_repeat_patch_updates_in_place(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch)
        await client.patch(SETTINGS_URL, json={"auto_draft_flashcards": False})

        response = await client.patch(
            SETTINGS_URL, json={"auto_draft_flashcards": True}
        )

        assert response.status_code == 200
        assert response.json() == {"auto_draft_flashcards": True}
    assert await _row_count() == 1


@pytest.mark.anyio
async def test_an_empty_patch_is_a_read(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch)
        await client.patch(SETTINGS_URL, json={"auto_draft_flashcards": False})

        response = await client.patch(SETTINGS_URL, json={})

        assert response.status_code == 200
        assert response.json() == {"auto_draft_flashcards": False}
    assert await _row_count() == 1


@pytest.mark.anyio
async def test_an_unknown_setting_is_rejected(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch)

        response = await client.patch(SETTINGS_URL, json={"dark_mode": True})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
    assert await _row_count() == 0


@pytest.mark.anyio
async def test_a_non_boolean_value_is_rejected(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch)

        response = await client.patch(
            SETTINGS_URL, json={"auto_draft_flashcards": "maybe"}
        )

        assert response.status_code == 422


@pytest.mark.anyio
async def test_deleting_the_account_cascades_to_its_settings(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        user_id = await _sign_in(client, monkeypatch)
        await client.patch(SETTINGS_URL, json={"auto_draft_flashcards": False})
    assert await _row_count() == 1

    async with db.async_session() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()

    assert await _row_count() == 0
