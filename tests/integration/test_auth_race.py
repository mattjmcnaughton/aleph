"""Concurrent first-login provisioning race (AL-020 review, S3).

Two callers whose ``get_by_identity`` both miss race to insert the same
``(issuer, subject)`` — without the ``IntegrityError`` guard in
``AuthService._provision`` the loser's flush 500s. Reproduced deterministically
against real Postgres: an ``asyncio.Barrier`` holds both transactions open past
their identity SELECT so both attempt the INSERT, then one commits and the other
must recover.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from aleph import db
from aleph.auth import AuthIdentity
from aleph.models import User
from aleph.services.auth import AuthService

_IDENTITY = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="race-subject",
    username="racer",
    display_name="Racer",
    email="racer@example.com",
)


@pytest.mark.anyio
async def test_concurrent_first_login_provisions_exactly_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = asyncio.Barrier(2)
    original = AuthService._available_username

    async def barriered(self: AuthService, preferred: str) -> str:
        # Both callers are now past get_by_identity (each saw no row); release
        # them together so both reach the INSERT and genuinely contend.
        username = await original(self, preferred)
        await barrier.wait()
        return username

    monkeypatch.setattr(AuthService, "_available_username", barriered)

    async def _sign_in_once() -> str:
        async with db.async_session() as session:
            user = await AuthService(session).sign_in(_IDENTITY)
            return str(user.id)

    first_id, second_id = await asyncio.gather(_sign_in_once(), _sign_in_once())

    # Both callers resolve to the one committed account, and only one row exists.
    assert first_id == second_id
    async with db.async_session() as session:
        count = await session.scalar(
            select(func.count(User.id)).where(
                User.issuer == _IDENTITY.issuer,
                User.subject == _IDENTITY.subject,
            )
        )
    assert count == 1
